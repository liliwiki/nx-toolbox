"""卡上 emuMMC 文件夹里的配置：认出虚拟系统是哪种形式，换卡后把扇区号改对。

hekate 的"修复原始分区"会整个重写 emummc.ini；这里只改扇区号，别的设置原样留着。
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

FOLDER = "emuMMC"
INI = "emummc.ini"
RAW_MARK, FILE_MARK = "raw_based", "file_based"
_SECTOR_LINE = re.compile(r"^(\s*sector\s*=\s*).*$", re.MULTILINE | re.IGNORECASE)


def kinds(card_root: Path) -> dict:
    """卡上有哪些虚拟系统：{"raw": [文件夹名...], "file": [文件夹名...]}。"""
    found = {"raw": [], "file": []}
    folder = Path(card_root) / FOLDER
    if folder.is_dir():
        for sub in sorted(p for p in folder.iterdir() if p.is_dir()):
            if (sub / RAW_MARK).is_file():
                found["raw"].append(sub.name)
            elif (sub / FILE_MARK).is_file():
                found["file"].append(sub.name)
    return found


def ini_sector(card_root: Path) -> int | None:
    try:
        text = (Path(card_root) / FOLDER / INI).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"^\s*sector\s*=\s*(\S+)", text, re.MULTILINE | re.IGNORECASE)
    try:
        return int(match.group(1), 16) if match else None
    except ValueError:
        return None


def fix_sector(card_root: Path, old_sector: int, new_sector: int) -> list[str]:
    """把指向旧位置的扇区号换成新的，返回改过的文件。只动原来就指向这个分区的那些。"""
    changed = []
    folder = Path(card_root) / FOLDER
    for name in kinds(card_root)["raw"]:
        mark = folder / name / RAW_MARK
        data = mark.read_bytes()
        if len(data) >= 4 and struct.unpack_from("<I", data)[0] == old_sector:
            mark.write_bytes(struct.pack("<I", new_sector) + data[4:])
            changed.append(f"{FOLDER}/{name}/{RAW_MARK}")
    ini = folder / INI
    if ini.is_file() and ini_sector(card_root) == old_sector:
        text = ini.read_text(encoding="utf-8", errors="replace")
        ini.write_text(_SECTOR_LINE.sub(lambda m: f"{m.group(1)}0x{new_sector:x}", text, count=1), encoding="utf-8", newline="")
        changed.append(f"{FOLDER}/{INI}")
    return changed
