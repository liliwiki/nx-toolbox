"""快照：更新前把要清掉和要覆盖的文件存一份，之后可以放回去。"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from nxhost import shared

SNAPSHOT_DIR = shared.DATA_DIR / "snapshots"
INDEX_FILE = SNAPSHOT_DIR / "index.json"
MAX_SNAPSHOTS = 10


def _load() -> list[dict]:
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save(index: list[dict]) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def list_all() -> list[dict]:
    return list(reversed(_load()))


def total_mb() -> float:
    return round(sum(s.get("mb", 0) for s in _load()), 1)


def _files_of(sd_root: str, items: list[tuple[str, bool]]):
    for rel, is_dir in items:
        source = os.path.join(sd_root, rel)
        if not is_dir:
            if os.path.isfile(source):
                yield rel
            continue
        for folder, _, names in os.walk(source):
            for name in names:
                yield os.path.relpath(os.path.join(folder, name), sd_root)


def create(sd_root: str, title: str, items: list[tuple[str, bool]], progress, check) -> dict | None:
    """items 是 (相对路径, 是否文件夹)。没有可备份的文件时返回 None。"""
    # 一个文件可能既在要清掉的文件夹里、又在要覆盖的列表里
    files = list({os.path.normcase(os.path.normpath(f)): f for f in _files_of(sd_root, items)}.values())
    if not files:
        return None
    snapshot_id = time.strftime("%Y%m%d_%H%M%S")
    target = SNAPSHOT_DIR / snapshot_id
    size = 0
    try:
        for index, rel in enumerate(files, 1):
            check()
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(os.path.join(sd_root, rel), dest)
            size += dest.stat().st_size
            progress(index / len(files), f"{index} / {len(files)}", rel)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    meta = {"id": snapshot_id, "time": time.strftime("%Y-%m-%d %H:%M"), "title": title, "sd": sd_root,
            "files": len(files), "mb": round(size / 1048576, 1)}
    index = _load() + [meta]
    while len(index) > MAX_SNAPSHOTS:
        shutil.rmtree(SNAPSHOT_DIR / index.pop(0)["id"], ignore_errors=True)
    _save(index)
    return meta


def restore(snapshot_id: str, sd_root: str, progress, check) -> int:
    source = SNAPSHOT_DIR / snapshot_id
    files = [p for p in source.rglob("*") if p.is_file()]
    if not files:
        raise OSError("这个快照里没有文件")
    for index, path in enumerate(files, 1):
        check()
        rel = path.relative_to(source)
        dest = Path(sd_root) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        progress(index / len(files), f"{index} / {len(files)}", str(rel))
    return len(files)


def delete(snapshot_id: str) -> None:
    index = _load()
    if any(s["id"] == snapshot_id for s in index):
        shutil.rmtree(SNAPSHOT_DIR / snapshot_id, ignore_errors=True)
        _save([s for s in index if s["id"] != snapshot_id])


def delete_all() -> None:
    for meta in _load():
        shutil.rmtree(SNAPSHOT_DIR / meta["id"], ignore_errors=True)
    _save([])
