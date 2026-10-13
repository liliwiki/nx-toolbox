"""固件：本地和在线列表，放进存储卡。"""

from __future__ import annotations

import os
import re
import shutil
import urllib.parse
import urllib.request

from core import package
from core.jobs import Job
from nxhost import shared

FIRMWARE_DIR = shared.LEGACY_DIR / "固件"      # 沿用旧版的位置，之前下载的固件还能用
ONLINE_URL = "https://download.liliwiki.com/firmware/NS/"
S_DOWNLOAD, S_EXTRACT, S_COPY = "下载固件", "解压", "复制到存储卡"
SEARCH_DEPTH = 3


def version_of(name: str) -> str | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", name)
    return match.group(0) if match else None


def _sort_key(item: dict):
    return tuple(int(x) for x in (item["version"] or "0.0.0").split("."))


def local() -> list[dict]:
    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for path in FIRMWARE_DIR.rglob("*"):
        if path.is_file() and path.suffix.lower() in package.ARCHIVE_EXTS:
            items.append({"name": path.stem, "path": str(path), "mb": round(path.stat().st_size / 1048576),
                          "version": version_of(path.name)})
    return sorted(items, key=_sort_key, reverse=True)


def total_mb() -> int:
    return sum(i["mb"] for i in local())


def online() -> list[dict]:
    req = urllib.request.Request(ONLINE_URL, headers={"User-Agent": "Mozilla/5.0 NXToolbox/2.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode("utf-8", "replace")
    items = []
    for href in dict.fromkeys(re.findall(r'href="([^"]+\.(?:zip|7z|rar))"', html, re.IGNORECASE)):
        if href.startswith(".."):
            continue
        url = urllib.parse.urljoin(ONLINE_URL, href)
        name = urllib.parse.unquote(href.rsplit("/", 1)[-1])
        items.append({"name": os.path.splitext(name)[0], "url": url, "file": name, "version": version_of(name)})
    return sorted(items, key=_sort_key, reverse=True)


def add_local(source: str) -> str:
    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    dest = FIRMWARE_DIR / os.path.basename(source)
    shutil.copyfile(source, dest)
    return str(dest)


def delete_local(path: str) -> None:
    full = os.path.abspath(path)
    if os.path.commonpath([full, str(FIRMWARE_DIR)]) != str(FIRMWARE_DIR):
        raise OSError("只能删除固件文件夹里的文件")
    os.remove(full)


def _find_source(root: str) -> str | None:
    """固件就是一堆 .nca 文件，找到直接装着它们的那个文件夹。"""
    level = [root]
    for _ in range(SEARCH_DEPTH):
        next_level = []
        for folder in level:
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            if any(e.name.lower().endswith(".nca") for e in entries):
                return folder
            next_level += [e.path for e in entries if e.is_dir()]
        level = next_level
    return None


def steps(item: dict) -> list[str]:
    return ([S_DOWNLOAD] if item.get("url") else []) + [S_EXTRACT, S_COPY]


def install(job: Job, sd_root: str, item: dict) -> dict:
    archive = item.get("path")
    if item.get("url"):
        job.step(S_DOWNLOAD)
        FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
        archive = str(FIRMWARE_DIR / item["file"])
        partial = archive + ".part"
        try:
            package.download(item["url"], partial,
                             lambda done, total: job.progress(done / total if total else 0, f"{done / 1048576:.0f} / {total / 1048576:.0f} MB"),
                             job.check)
            os.replace(partial, archive)
        finally:
            if os.path.exists(partial):
                os.remove(partial)
    work_dir = package.make_work_dir()
    try:
        job.step(S_EXTRACT)
        package.extract(archive, work_dir, lambda done, total, name: job.progress(done / total, f"{done} / {total}", name), job.check)
        source = _find_source(work_dir)
        if source is None:
            raise OSError("压缩包里没有找到固件文件（.nca）")
        job.step(S_COPY)
        folder = item["name"]
        target = os.path.join(sd_root, folder)
        names = os.listdir(source)
        os.makedirs(target, exist_ok=True)
        for index, name in enumerate(names, 1):
            job.check()
            path = os.path.join(source, name)
            if os.path.isdir(path):
                shutil.copytree(path, os.path.join(target, name), dirs_exist_ok=True)
            else:
                shutil.copyfile(path, os.path.join(target, name))
            job.progress(index / len(names), f"{index} / {len(names)}", name)
        return {"folder": folder, "files": len(names)}
    finally:
        package.remove_work_dir(work_dir)
