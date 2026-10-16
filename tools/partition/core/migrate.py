"""换卡的整个过程：看旧卡、把旧卡存到电脑、写新卡。

旧卡全程只读。两个读卡器时旧卡直接当来源；只有一个读卡器时先把旧卡存到电脑上的一个文件夹，
换上新卡后再拿这个文件夹当来源。两种来源对"写新卡"来说是一样的。
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from core import clone, disk, emucfg, fat32, layout, userfs
from core.layout import ALIGN_SECTORS, BOOT_SECTORS, MB_SECTORS, SECTOR, Card, Partition, RawEmummc
from core.xts import Xts

SKIP_NAMES = {"system volume information", "$recycle.bin"}     # Windows 自己放在卡上的，不搬
CLUSTER = fat32.CLUSTER_BYTES
FAT_SLACK = 64 * 1024 * 1024          # 给 FAT 表和对齐留的余量
FAT_OVERHEAD = 1.002
COPY_CHUNK = 4 * 1024 * 1024
MANIFEST = "manifest.json"
FILES_DIR = "files"
_KEEP_ATTRS = 0x1 | 0x2 | 0x4 | 0x20  # 只读、隐藏、系统、存档
_INVALID_ATTRS = 0xFFFFFFFF
STEP_SAVE_PARTS, STEP_SAVE_FILES = "保存分区", "保存文件"
STEP_FORMAT, STEP_PARTS, STEP_FILES, STEP_VERIFY, STEP_FINISH = "给新卡分区", "写入分区", "复制文件", "核对文件", "收尾"

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
_k32.GetFileAttributesW.restype = ctypes.c_uint32
_k32.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]


class MigrateError(Exception):
    """文字直接给用户看。"""


# ---- 文件 ----

def _copy_attrs(source: str, target: str) -> None:
    """存档、隐藏这些属性要带过去：系统靠文件夹上的"存档"属性认拆分的大文件。"""
    attrs = _k32.GetFileAttributesW(source)
    if attrs != _INVALID_ATTRS:
        _k32.SetFileAttributesW(target, (attrs & _KEEP_ATTRS) or 0x80)


def walk(root: Path):
    """卡上的每个文件夹和文件：(相对路径, 是不是文件夹, 大小)。文件夹排在它里面的东西前面。"""
    stack = [""]
    while stack:
        rel = stack.pop()
        try:
            entries = sorted(os.scandir(root / rel), key=lambda e: e.name.lower())
        except OSError as exc:
            raise MigrateError(f"读不了卡上的文件夹 {rel or '/'}：{exc.strerror or exc}") from exc
        for entry in entries:
            if not rel and entry.name.lower() in SKIP_NAMES:
                continue
            path = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir(follow_symlinks=False):
                yield path, True, 0
                stack.append(path)
            else:
                yield path, False, entry.stat(follow_symlinks=False).st_size


def measure(root: Path) -> dict:
    files = folders = size = need = 0
    for _, is_dir, length in walk(root):
        if is_dir:
            folders += 1
            need += CLUSTER
        else:
            files += 1
            size += length
            need += -(-length // CLUSTER) * CLUSTER          # 新卡用 64 KB 簇，小文件占得比原来多
    return {"files": files, "folders": folders, "bytes": size, "need": int(need * FAT_OVERHEAD) + FAT_SLACK}


def copy_tree(job, source: Path, target: Path, total: int, digests: dict | None) -> int:
    """把 source 下的东西搬到 target 下。digests 不是 None 时记下每个文件的校验值，留着核对。"""
    done = count = 0
    folders = []
    for rel, is_dir, _ in walk(source):
        src, dst = source / rel, target / rel
        if is_dir:
            dst.mkdir(parents=True, exist_ok=True)
            folders.append(rel)
            continue
        digest = hashlib.blake2b(digest_size=16) if digests is not None else None
        with open(src, "rb") as reader, open(dst, "wb") as writer:
            while True:
                job.check()
                data = reader.read(COPY_CHUNK)
                if not data:
                    break
                writer.write(data)
                if digest:
                    digest.update(data)
                done += len(data)
                job.progress(done / total if total else 1.0, _amount(done, total), rel)
        shutil.copystat(src, dst)
        _copy_attrs(str(src), str(dst))
        if digests is not None:
            digests[rel] = digest.hexdigest()
        count += 1
    for rel in reversed(folders):                               # 文件夹的属性最后设，里面的东西都放好了
        _copy_attrs(str(source / rel), str(target / rel))
    return count


def verify_tree(job, root: Path, digests: dict, total: int) -> None:
    done = 0
    for rel, expected in digests.items():
        digest = hashlib.blake2b(digest_size=16)
        try:
            with open(root / rel, "rb") as reader:
                while True:
                    job.check()
                    data = reader.read(COPY_CHUNK)
                    if not data:
                        break
                    digest.update(data)
                    done += len(data)
                    job.progress(done / total if total else 1.0, _amount(done, total), rel)
        except OSError as exc:
            raise MigrateError(f"核对时读不出 {rel}，新卡可能有问题") from exc
        if digest.hexdigest() != expected:
            raise MigrateError(f"{rel} 写到新卡之后内容对不上，新卡或读卡器可能有问题")


def _amount(done: int, total: int) -> str:
    return f"{done / 1073741824:.2f} / {total / 1073741824:.2f} GB"


# ---- 卡的信息 ----

def _card_to_json(card: Card) -> dict:
    return {"total": card.total, "fat": asdict(card.fat), "extras": [asdict(p) for p in card.extras],
            "emummcs": [{"index": card.extras.index(e.partition), "sector": e.sector, "used": e.used} for e in card.emummcs],
            "mbr": card.mbr.hex(), "gpt_header": card.gpt_header.hex() if card.gpt_header else None,
            "gpt_entries": card.gpt_entries.hex() if card.gpt_entries else None}


def _card_from_json(data: dict) -> Card:
    extras = [Partition(**p) for p in data["extras"]]
    return Card(data["total"], Partition(**data["fat"]), extras,
                [RawEmummc(extras[e["index"]], e["sector"], e["used"]) for e in data["emummcs"]], bytes.fromhex(data["mbr"]),
                bytes.fromhex(data["gpt_header"]) if data["gpt_header"] else None,
                bytes.fromhex(data["gpt_entries"]) if data["gpt_entries"] else None)


def _copy_lengths(card: Card) -> list[int]:
    used = {e.partition: e.used for e in card.emummcs}
    return [used.get(p, p.size) for p in card.extras]


def _summary(card: Card, files: dict, root: Path | None) -> dict:
    lengths = _copy_lengths(card)
    extras = [{"kind": card.kind(p), "name": p.name, "bytes": p.size * SECTOR, "used": n * SECTOR}
              for p, n in zip(card.extras, lengths)]
    span = (card.extras[-1].end - card.extras[0].start) if card.extras else 0
    kinds = emucfg.kinds(root) if root else {"raw": [], "file": []}
    return {"files": files, "extras": extras, "emu_file": kinds["file"], "emu_raw": len(card.emummcs),
            "fat_bytes": card.fat.size * SECTOR,
            "copy_bytes": files["bytes"] + sum(lengths) * SECTOR,
            "min_bytes": (ALIGN_SECTORS + span + MB_SECTORS) * SECTOR + files["need"]}


def _open_user(source, index: int):
    """打开第 index 个分区里的 USER：(设备, 分区在设备上的起点, 虚拟系统在分区里的偏移扇区, 镜像, 加解密, 明文读写, 文件系统)。"""
    emu = next(e for e in source.card.emummcs if e.partition == source.card.extras[index])
    keys = userfs.read_keys(source.root)
    if not keys:
        raise userfs.UserError("卡上没有 switch/prod.keys")
    device, offset = source.open_part(index)
    inner = emu.sector - emu.partition.start
    base = offset + (inner + BOOT_SECTORS) * SECTOR
    nand = userfs.read_nand(device, base)
    xts = Xts(*keys)
    plain = userfs.Plain(device, base + nand.user_start * SECTOR, xts)
    return device, offset, inner, nand, xts, plain, userfs.read_fat(plain)


def _round_mb(sectors: int) -> int:
    return -(-sectors // MB_SECTORS) * MB_SECTORS


def emu_details(source) -> list[dict]:
    """每个分区形式的虚拟系统：现在多大、USER 用了多少、最小能缩到多少。读不了密钥的只能原样搬。"""
    details = []
    for emu in source.card.emummcs:
        index = source.card.extras.index(emu.partition)
        item = {"index": index, "bytes": emu.partition.size * SECTOR, "resizable": False, "reason": ""}
        try:
            _, _, inner, nand, _, _, fat = _open_user(source, index)
            use = userfs.usage(fat)
            fixed = inner + BOOT_SECTORS + nand.user_start + userfs.GPT_SECTORS      # USER 以外占的扇区
            item.update(resizable=True, user_bytes=use["bytes"], user_used=use["used"], fixed_bytes=fixed * SECTOR,
                        min_bytes=min(emu.partition.size, _round_mb(fixed + userfs.minimum_sectors(fat))) * SECTOR)
        except userfs.UserError as exc:
            item["reason"] = str(exc)
        except OSError as exc:
            item["reason"] = f"读不了这个虚拟系统：{exc.strerror or exc}"
        details.append(item)
    return details


def _find_disk(number: int) -> dict:
    info = next((d for d in disk.list_disks() if d["number"] == number), None)
    if info is None:
        raise MigrateError("找不到这张卡了，可能被拔掉了")
    return info


def _fat_root(number: int, card: Card) -> Path:
    letter = next((v["letter"] for v in disk.volumes()
                   if v["disk"] == number and v["offset"] == card.fat.start * SECTOR and v["letter"]), None)
    if not letter:
        raise MigrateError("Windows 没有给这张卡的 FAT32 分区分配盘符，拔下来重新插一次试试")
    return Path(letter)


def inspect(number: int) -> dict:
    """看一张旧卡：有哪些分区、文件有多少、新卡至少要多大。"""
    info = _find_disk(number)
    source = DiskSource(number)
    try:
        return {"number": number, "name": info["name"], "size": info["size"], "letter": str(source.root),
                **_summary(source.card, measure(source.root), source.root), "emus": emu_details(source)}
    finally:
        source.close()


# ---- 来源 ----

class DiskSource:
    def __init__(self, number: int) -> None:
        info = _find_disk(number)
        self.number, self.name = number, info["name"]
        self._device = disk.open_disk(number, info["size"])
        self.card = layout.scan(self._device)
        self.root = _fat_root(number, self.card)

    def open_part(self, index: int):
        return self._device, self.card.extras[index].start * SECTOR

    def close(self) -> None:
        self._device.close()


class StagedSource:
    """存在电脑上的旧卡：manifest.json + 每个分区一个文件 + files 文件夹。"""
    number = None

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.manifest = read_stage(self.folder)
        if not self.manifest or not self.manifest.get("complete"):
            raise MigrateError("电脑上没有存好的旧卡内容")
        self.name = self.manifest["name"]
        self.card = _card_from_json(self.manifest["card"])
        self.root = self.folder / FILES_DIR
        self._open = []

    def open_part(self, index: int):
        device = clone.FileDevice(str(self.folder / f"part_{index}.bin"))
        self._open.append(device)
        return device, 0

    def close(self) -> None:
        for device in self._open:
            device.close()


def read_stage(folder: Path) -> dict | None:
    try:
        return json.loads((Path(folder) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def stage_summary(folder: Path) -> dict | None:
    manifest = read_stage(folder)
    if not manifest or not manifest.get("complete"):
        return None
    card = _card_from_json(manifest["card"])
    return {"name": manifest["name"], "size": manifest["size"], "saved_at": manifest["saved_at"],
            **_summary(card, manifest["files"], Path(folder) / FILES_DIR), "emus": manifest.get("emus", [])}


def discard_stage(folder: Path) -> None:
    folder = Path(folder)
    if (folder / MANIFEST).is_file() or not folder.exists() or not any(folder.iterdir()):
        shutil.rmtree(folder, ignore_errors=True)


# ---- 存到电脑 ----

def save_steps(summary: dict) -> list[str]:
    return ([STEP_SAVE_PARTS] if summary["extras"] else []) + [STEP_SAVE_FILES]


def save(job, number: int, folder: Path) -> dict:
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()) and not (folder / MANIFEST).is_file():
        raise MigrateError("存放的文件夹里已经有别的东西了，请换一个空文件夹")
    shutil.rmtree(folder, ignore_errors=True)
    source = DiskSource(number)
    try:
        files = measure(source.root)
        lengths = _copy_lengths(source.card)
        need = files["bytes"] + sum(lengths) * SECTOR
        folder.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(folder).free
        if free < need + FAT_SLACK:
            raise MigrateError(f"电脑上这个位置只剩 {free / 1073741824:.1f} GB，存旧卡需要 {need / 1073741824:.1f} GB")
        manifest = {"version": 1, "complete": False, "name": source.name, "size": _find_disk(number)["size"],
                    "saved_at": time.time(), "card": _card_to_json(source.card), "files": files, "emus": emu_details(source)}
        (folder / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        total = sum(lengths) * SECTOR
        done = 0
        if lengths:
            job.step(STEP_SAVE_PARTS)
        for index, length in enumerate(lengths):
            path = folder / f"part_{index}.bin"
            path.write_bytes(b"")
            target = clone.FileDevice(str(path), writable=True, size=0)
            device, offset = source.open_part(index)

            def tick(count, base=done):
                job.check()
                job.progress((base + count) / total, _amount(base + count, total))

            try:
                digest = clone.copy(device, offset, target, 0, length * SECTOR, tick)
            finally:
                target.close()
            manifest.setdefault("digests", []).append(digest)
            done += length * SECTOR
        job.step(STEP_SAVE_FILES)
        copy_tree(job, source.root, folder / FILES_DIR, files["bytes"], None)
        manifest["complete"] = True
        (folder / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        return {"saved": True, "files": files["files"], "bytes": need}
    finally:
        source.close()


# ---- 写新卡 ----

def _is_same_card(card: Card, target: dict) -> bool:
    """经电脑中转时旧卡可能还插着：分区表和存下来的一模一样，就是旧卡本身。"""
    if target["size"] // SECTOR != card.total:
        return False
    try:
        device = disk.open_disk(target["number"], target["size"])
    except OSError:
        return False
    try:
        return device.read_at(0, SECTOR) == card.mbr
    finally:
        device.close()


def _sizes(summary: dict, sizes: dict | None) -> dict[int, int]:
    """页面给的新大小（字节）→ 扇区。只接受能调的那些，并且不能小于最小值。"""
    result = {}
    for key, value in (sizes or {}).items():
        item = next((e for e in summary.get("emus", []) if e["index"] == int(key)), None)
        if item is None or int(value) == item["bytes"]:
            continue
        if not item["resizable"]:
            raise MigrateError("这个虚拟系统的大小调不了：" + item["reason"])
        if int(value) < item["min_bytes"]:
            raise MigrateError(f"虚拟系统最小只能缩到 {item['min_bytes'] / 1073741824:.1f} GB")
        result[int(key)] = _round_mb(int(value) // SECTOR)
    return result


def check_target(summary: dict, card: Card, target: dict, source_number: int | None, sizes: dict | None = None) -> dict:
    """新卡行不行。行的话返回新卡会变成什么样，不行抛出原因。sizes：第几个分区 → 新的大小（字节）。"""
    if target["number"] == source_number or _is_same_card(card, target):
        raise MigrateError("新卡和旧卡不能是同一张")
    if target["size"] < summary["min_bytes"]:
        short = (summary["min_bytes"] - target["size"]) / 1073741824
        raise MigrateError(f"这张卡放不下旧卡的内容，还差 {short:.1f} GB")
    chosen = _sizes(summary, sizes)
    try:
        plan = layout.plan(target["size"] // SECTOR, card, chosen)
    except layout.LayoutError as exc:
        raise MigrateError(str(exc)) from exc
    spare = plan.fat.size * SECTOR - summary["files"]["need"]
    if spare < 0:
        raise MigrateError(f"这张卡放不下旧卡的文件，还差 {-spare / 1073741824:.1f} GB")
    emus = [{**e, "new_bytes": plan.moves[e["index"]].new_size * SECTOR,
             "max_bytes": (plan.moves[e["index"]].new_size * SECTOR + spare) // 1048576 * 1048576} for e in summary.get("emus", [])]
    return {"fat_bytes": plan.fat.size * SECTOR, "old_fat_bytes": card.fat.size * SECTOR, "emus": emus,
            "extras": [{"kind": m.kind, "bytes": m.new_size * SECTOR} for m in plan.moves]}


def write_steps(summary: dict, verify: bool) -> list[str]:
    return ([STEP_FORMAT] + ([STEP_PARTS] if summary["extras"] else []) + [STEP_FILES]
            + ([STEP_VERIFY] if verify else []) + [STEP_FINISH])


def write_partitions(job, source, plan, device, verify: bool) -> None:
    """把 FAT32 后面的分区写到新卡上：大小没变的原样搬，变了的重建里面的 USER。写完都读回来核对。"""
    count = len(plan.moves)
    for index, move in enumerate(plan.moves):
        name = layout.KIND_NAMES[move.kind]

        def tick(part: float, label: str = name, base: int = index):
            job.check()
            job.progress((base + min(1.0, part)) / count, f"第 {base + 1} 个，共 {count} 个", label)

        if move.new_size == move.old.size:
            reader, offset = source.open_part(index)
            size = move.length * SECTOR
            digest = clone.copy(reader, offset, device, move.new_start * SECTOR, size, lambda done: tick(done / size / 2))
            if clone.checksum(device, move.new_start * SECTOR, size, lambda done: tick(0.5 + done / size / 2)) != digest:
                raise MigrateError(f"{name}分区写到新卡之后内容对不上，新卡或读卡器可能有问题")
            continue
        try:
            reader, offset, inner, nand, xts, old_plain, fat = _open_user(source, index)
            nand_sectors = move.new_size - inner - BOOT_SECTORS
            resize = userfs.plan(fat, userfs.user_sectors_for(nand, nand_sectors))
            target = move.new_start * SECTOR
            # USER 前面的部分（保护区、BOOT0/1、PRODINFO 到 SYSTEM）原样搬
            head = (inner + BOOT_SECTORS + nand.user_start) * SECTOR
            digest = clone.copy(reader, offset, device, target, head, lambda done: tick(done / head * 0.1))
            if clone.checksum(device, target, head, lambda done: tick(0.1 + done / head * 0.1)) != digest:
                raise MigrateError(f"{name}分区写到新卡之后内容对不上，新卡或读卡器可能有问题")
            base = target + (inner + BOOT_SECTORS) * SECTOR
            new_plain = userfs.Plain(device, base + nand.user_start * SECTOR, xts)
            userfs.write_resized(old_plain, fat, resize, new_plain, lambda done, total: tick(0.2 + done / total * 0.4), job.check)
            for sector, data in userfs.nand_tables(nand, nand_sectors):
                device.write_at(base + sector * SECTOR, data)
            device.write_at(target + (inner + userfs.NAND_PATROL_SECTOR) * SECTOR, bytes(SECTOR))
            device.flush()
            userfs.verify(fat, new_plain, resize, old_plain if verify else None,
                          lambda done, total: tick(0.6 + done / max(1, total) * 0.4), job.check)
        except userfs.UserError as exc:
            raise MigrateError(str(exc)) from exc


def write(job, source, target_number: int, target_size: int, verify: bool, sizes: dict | None = None) -> dict:
    """把来源写到新卡上。target_size 是用户确认时看到的容量，对不上就不写。sizes：第几个分区 → 新的大小（字节）。"""
    card = source.card
    target = _find_disk(target_number)
    if target["size"] != target_size:
        raise MigrateError("新卡和刚才确认的不是同一张了，请重新选一次")
    files = measure(source.root)
    summary = {**_summary(card, files, source.root), "emus": emu_details(source)}
    check_target(summary, card, target, source.number, sizes)
    total_sectors = target["size"] // SECTOR
    plan = layout.plan(total_sectors, card, _sizes(summary, sizes))

    job.step(STEP_FORMAT, danger=True)
    with disk.locked(target_number):
        device = disk.open_disk(target_number, target["size"], writable=True)
        try:
            device.write_at(0, bytes(ALIGN_SECTORS * SECTOR))                       # 开头 16 MB 清零，旧的分区表不能留
            device.write_at((total_sectors - MB_SECTORS) * SECTOR, bytes(MB_SECTORS * SECTOR))
            for sector, data in plan.tables:
                device.write_at(sector * SECTOR, data)
            fat32.format_partition(device, plan.fat.start, plan.fat.size, progress=lambda frac: job.progress(frac))
            device.flush()
            if plan.moves:
                job.step(STEP_PARTS)
                write_partitions(job, source, plan, device, verify)
        finally:
            device.close()
    disk.rescan(target_number)
    letter = disk.wait_for_volume(target_number, plan.fat.start * SECTOR)
    if not letter:
        raise MigrateError("新卡分好区了，但 Windows 没有把它挂出来。把新卡拔下来重新插上，再重新开始一次")
    root = Path(letter)

    job.step(STEP_FILES)
    digests = {} if verify else None
    count = copy_tree(job, source.root, root, files["bytes"], digests)
    if verify:
        job.step(STEP_VERIFY)
        verify_tree(job, root, digests, files["bytes"])
    job.step(STEP_FINISH)
    changed = [name for old, new in plan.sectors for name in emucfg.fix_sector(root, old, new)]
    return {"letter": letter, "files": count, "bytes": files["bytes"], "parts": len(plan.moves), "changed": changed,
            "resized": sum(1 for m in plan.moves if m.new_size != m.old.size), "fat_bytes": plan.fat.size * SECTOR, "name": target["name"], "size": target["size"]}
