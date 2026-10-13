"""存储卡：识别、信息、弹出。"""

from __future__ import annotations

import ctypes
import os
import shutil
import string
import struct
from ctypes import wintypes

FAKE_SD_ENV = "NXTOOLBOX_FAKE_SD"   # 开发用：把普通文件夹当成存储卡，多个用分号隔开
_DRIVE_REMOVABLE, _DRIVE_FIXED = 2, 3


def is_switch_sd(path: str) -> bool:
    has = lambda name: os.path.exists(os.path.join(path, name))
    if has("atmosphere") and has("bootloader"):
        return True
    return has("emuMMC") or has("Nintendo")


def _system_drive() -> str:
    return os.path.splitdrive(os.environ.get("SystemRoot", r"C:\Windows"))[0].upper()


def check_target(path: str) -> str | None:
    """返回不能操作这个位置的原因；可以操作就返回 None。"""
    if not path or not os.path.isdir(path):
        return "这个位置不存在"
    full = os.path.abspath(path)
    drive, tail = os.path.splitdrive(full)
    if drive.upper() == _system_drive() and tail in ("\\", "/", "") or os.path.isdir(os.path.join(full, "Windows", "System32")):
        return "这是系统盘，不能当存储卡用"
    return None


def _volume(path: str) -> tuple[str, str]:
    root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    label = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(root, label, 261, None, None, None, fs, 261)
    return (label.value, fs.value) if ok else ("", "")


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
    label, fs = _volume(path)
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
        "ams": ams, "hos": hos, "looks_like_sd": is_switch_sd(path), "is_drive": is_root,
    }


def detect() -> list[dict]:
    paths = [p for p in os.environ.get(FAKE_SD_ENV, "").split(";") if p and os.path.isdir(p)]
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        root = f"{letter}:\\"
        if not mask >> i & 1 or letter + ":" == _system_drive():
            continue
        if ctypes.windll.kernel32.GetDriveTypeW(root) not in (_DRIVE_REMOVABLE, _DRIVE_FIXED):
            continue
        try:
            if is_switch_sd(root):
                paths.append(root)
        except OSError:
            continue
    return [info(p) for p in paths]


def eject(path: str) -> tuple[bool, str]:
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if os.path.splitdrive(os.path.abspath(path))[1] not in ("\\", "/"):
        return False, "这是一个文件夹，不是存储卡，不需要弹出"
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(f"\\\\.\\{drive}", 0x80000000 | 0x40000000, 0x1 | 0x2, None, 3, 0, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        return False, f"打不开 {drive}，请确认没有别的程序在用这张卡"
    try:
        returned = wintypes.DWORD()
        args = (None, 0, None, 0, ctypes.byref(returned), None)
        if not kernel32.DeviceIoControl(handle, 0x00090018, *args):    # FSCTL_LOCK_VOLUME
            return False, "有程序正在使用这张卡，关掉后再试"
        kernel32.DeviceIoControl(handle, 0x00090020, *args)            # FSCTL_DISMOUNT_VOLUME
        if kernel32.DeviceIoControl(handle, 0x2D4808, *args):          # IOCTL_STORAGE_EJECT_MEDIA
            return True, f"{drive} 已安全弹出"
        return False, f"弹出失败（错误 {kernel32.GetLastError()}）"
    finally:
        kernel32.CloseHandle(handle)
