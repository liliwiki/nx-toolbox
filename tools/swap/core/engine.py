"""执行更新：下载、解压、快照、清理、安装、校验。"""

from __future__ import annotations

import os
import shutil
import stat
import zlib

from core import package, snapshots
from core.jobs import Job
from core.rules import Node, RuleSet

CRC_BLOCK = 1024 * 1024
S_DOWNLOAD, S_EXTRACT, S_SNAPSHOT, S_CLEAN, S_INSTALL, S_VERIFY = (
    "下载整合包", "解压并检查", "创建快照", "清理旧文件", "安装新文件", "校验")


def plan_steps(listing: package.Listing | None, snapshot: bool, has_deletes: bool) -> list[str]:
    steps = []
    if listing and listing.kind == "online":
        steps.append(S_DOWNLOAD)
    if listing:
        steps.append(S_EXTRACT)
    if snapshot:
        steps.append(S_SNAPSHOT)
    if has_deletes:
        steps.append(S_CLEAN)
    if listing:
        steps += [S_INSTALL, S_VERIFY]
    return steps


def _inside(sd_root: str, rel: str) -> str:
    """所有要动的路径都从这里过，保证不会跑到存储卡外面去。"""
    full = os.path.abspath(os.path.join(sd_root, rel))
    root = os.path.abspath(sd_root)
    if os.path.commonpath([full, root]) != root or full == root:
        raise OSError(f"路径不在存储卡里：{rel}")
    return full


def _force_remove(path: str) -> None:
    def retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if os.path.isdir(path):
        shutil.rmtree(path, onerror=retry)
    elif os.path.exists(path):
        try:
            os.remove(path)
        except PermissionError:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)


def _crc(path: str) -> int:
    value = 0
    with open(path, "rb") as f:
        while block := f.read(CRC_BLOCK):
            value = zlib.crc32(block, value)
    return value


def _package_files(root: str) -> list[str]:
    return [os.path.relpath(os.path.join(folder, name), root).replace("\\", "/")
            for folder, _, names in os.walk(root) for name in names]


def run_update(job: Job, sd_root: str, listing: package.Listing | None, nodes: list[Node], rules: RuleSet,
               snapshot: bool) -> dict:
    deletes = [n for n in nodes if not n.verdict.keep]
    work_dir = package.make_work_dir() if listing else None
    try:
        installs: list[str] = []
        root = ""
        if listing:
            def on_download(done: int, total: int) -> None:
                job.progress(done / total if total else 0, f"{done / 1048576:.1f} / {total / 1048576:.1f} MB")

            def on_extract(done: int, total: int, name: str) -> None:
                job.progress(done / total, f"{done} / {total}", name)

            archive = listing.location
            if listing.kind == "online":
                job.step(S_DOWNLOAD)
                archive = os.path.join(work_dir, "package.zip")
                package.download(listing.location, archive, on_download, job.check)
            job.step(S_EXTRACT)
            out = os.path.join(work_dir, "package")
            package.extract(archive, out, on_extract, job.check)
            root = os.path.join(out, listing.prefix.replace("/", os.sep)) if listing.prefix else out
            installs = [f for f in _package_files(root) if rules.will_install(f)]
            if not installs:
                raise OSError("整合包解压出来是空的")

        snapshot_meta = None
        if snapshot:
            job.step(S_SNAPSHOT)
            # 要清掉的和要被覆盖的可能是同一个文件，按路径去重。
            # 游戏和虚拟系统动辄几十 GB，不进快照（取消保留时界面已经说明）
            items = list({path.lower(): (path, is_dir) for path, is_dir in
                          [(f, False) for f in installs if os.path.isfile(os.path.join(sd_root, f))]
                          + [(n.path.replace("\\", "/"), n.is_dir) for n in deletes if n.verdict.group != "emu"]}.values())
            title = f"更新整合包 {listing.ams or ''}".strip() if listing else "清理存储卡"
            snapshot_meta = snapshots.create(sd_root, title, items, job.progress, job.check)

        deleted = 0
        if deletes:
            job.step(S_CLEAN, danger=True)
            total = sum(n.files for n in deletes) or 1
            for node in deletes:
                job.check()
                _force_remove(_inside(sd_root, node.path))
                deleted += node.files
                job.progress(deleted / total, f"{deleted} / {total}", node.path)

        if listing:
            job.step(S_INSTALL, danger=True)
            for index, rel in enumerate(installs, 1):
                job.check()
                dest = _inside(sd_root, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if os.path.exists(dest) and not os.access(dest, os.W_OK):
                    os.chmod(dest, stat.S_IWRITE)
                shutil.copyfile(os.path.join(root, rel), dest)
                job.progress(index / len(installs), f"{index} / {len(installs)}", rel)

            job.step(S_VERIFY)
            for index, rel in enumerate(installs, 1):
                job.check()
                source, dest = os.path.join(root, rel), os.path.join(sd_root, rel)
                if os.path.getsize(source) != os.path.getsize(dest) or _crc(source) != _crc(dest):
                    raise OSError(f"校验失败：{rel} 写到存储卡上的内容和整合包不一致，请检查存储卡后重试")
                job.progress(index / len(installs), f"{index} / {len(installs)}", rel)

        return {"deleted": deleted, "installed": len(installs), "kept": sum(n.verdict.keep for n in nodes),
                "snapshot": snapshot_meta, "package": bool(listing),
                "ams": listing.ams if listing else None, "hos": listing.hos if listing else None}
    finally:
        if work_dir:
            package.remove_work_dir(work_dir)
