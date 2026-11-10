"""换包用到的存储卡信息。认卡、防电脑硬盘、弹出在共用的 nxhost.cards 里，这里只管和 Switch 有关的：
卡上是不是大气层、装的什么版本。"""

from __future__ import annotations

import os
import shutil
import struct

from nxhost import cards

FAKE_SD_ENV = "NXTOOLBOX_FAKE_SD"   # 开发用：把普通文件夹当成存储卡，多个用分号隔开


def is_switch_sd(path: str) -> bool:
    has = lambda name: os.path.exists(os.path.join(path, name))
    if has("atmosphere") and has("bootloader"):
        return True
    return has("emuMMC") or has("Nintendo")


def _fake_paths() -> list[str]:
    return [p for p in os.environ.get(FAKE_SD_ENV, "").split(";") if p and os.path.isdir(p)]


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def is_card(path: str) -> bool:
    """是不是一整张可移动存储卡（读卡器里的卡、U 盘）。

    本机硬盘和 USB 移动硬盘都不算；只有可移动存储卡的根目录才会按"白名单以外全部清除"处理。
    """
    if any(_same(path, p) for p in _fake_paths()):
        return True
    drive, tail = os.path.splitdrive(os.path.abspath(path))
    return tail in ("\\", "/") and cards.drive_is_card(drive + "\\")


def check_target(path: str) -> str | None:
    """返回不能操作这个位置的原因；可以操作就返回 None。"""
    if not path or not os.path.isdir(path):
        return "这个位置不存在"
    return cards.pc_reason(path)


def _versions(path: str) -> tuple[str | None, str | None]:
    try:
        with open(os.path.join(path, "atmosphere", "package3"), "rb") as f:
            header = f.read(0x40)
    except OSError:
        return None, None
    if len(header) < 0x40 or header[:4] != b"PK31":
        return None, None
    _, hos_micro, hos_minor, hos_major = struct.unpack_from("<BBBB", header, 0x34)
    _, micro, minor, major = struct.unpack_from("<BBBB", header, 0x38)
    return f"{major}.{minor}.{micro}", f"{hos_major}.{hos_minor}.{hos_micro}"


def info(path: str) -> dict:
    label, fs = cards.volume_info(os.path.splitdrive(os.path.abspath(path))[0] + "\\")
    try:
        usage = shutil.disk_usage(path)
        total, free = usage.total / 1024 ** 3, usage.free / 1024 ** 3
    except OSError:
        total = free = 0.0
    ams, hos = _versions(path)
    is_root = os.path.splitdrive(os.path.abspath(path))[1] in ("\\", "/")
    return {
        "path": path, "letter": os.path.splitdrive(os.path.abspath(path))[0] + "\\" if is_root else path,
        "label": label or ("存储卡" if is_root else os.path.basename(os.path.normpath(path))), "fs": fs,
        "total_gb": round(total, 1), "free_gb": round(free, 1), "used_gb": round(total - free, 1),
        "ams": ams, "hos": hos, "looks_like_sd": is_switch_sd(path), "is_drive": is_root, "is_card": is_card(path),
    }


def detect() -> list[dict]:
    """自动识别只找可移动存储卡。电脑硬盘、移动硬盘就算有 Nintendo 文件夹也不列出来，要用只能手动选。"""
    paths = _fake_paths()
    for root in cards.card_letters():
        try:
            if is_switch_sd(root) and check_target(root) is None:
                paths.append(root)
        except OSError:
            continue
    return [info(p) for p in paths]


def eject(path: str) -> tuple[bool, str]:
    return cards.eject(path)
