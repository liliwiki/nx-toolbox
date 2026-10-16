"""存储卡：识别、信息、弹出。"""

from __future__ import annotations

import ctypes
import os
import shutil
import string
import struct
from ctypes import wintypes

FAKE_SD_ENV = "NXTOOLBOX_FAKE_SD"   # 开发用：把普通文件夹当成存储卡，多个用分号隔开
_DRIVE_REMOVABLE = 2
_BUS_SD, _BUS_MMC = 0x0C, 0x0D
_IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400

# 根目录有这些就是电脑硬盘，绝不能当存储卡清理
PC_MARKERS = ("windows", "program files", "program files (x86)", "programdata", "users", "documents and settings",
              "$windows.~bt", "$windows.~ws", "pagefile.sys", "hiberfil.sys", "swapfile.sys", "bootmgr")
# 这些电脑文件夹在所选位置里面（或就是它），说明选得太靠上了
_PC_DIR_ENVS = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "USERPROFILE", "APPDATA", "LOCALAPPDATA")


def is_switch_sd(path: str) -> bool:
    has = lambda name: os.path.exists(os.path.join(path, name))
    if has("atmosphere") and has("bootloader"):
        return True
    return has("emuMMC") or has("Nintendo")


def _system_drive() -> str:
    return os.path.splitdrive(os.environ.get("SystemRoot", r"C:\Windows"))[0].upper()


def _fake_paths() -> list[str]:
    return [p for p in os.environ.get(FAKE_SD_ENV, "").split(";") if p and os.path.isdir(p)]


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _bus_type(letter: str) -> int | None:
    """问驱动器挂在什么总线上（USB、SD……），问不到返回 None。"""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(f"\\\\.\\{letter}:", 0, 0x1 | 0x2, None, 3, 0, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        return None
    try:
        query = struct.pack("<II4x", 0, 0)                 # StorageDeviceProperty, PropertyStandardQuery
        out = ctypes.create_string_buffer(1024)
        returned = wintypes.DWORD()
        if not kernel32.DeviceIoControl(handle, _IOCTL_STORAGE_QUERY_PROPERTY, query, len(query), out, len(out),
                                        ctypes.byref(returned), None) or returned.value < 32:
            return None
        return struct.unpack_from("<I", out.raw, 28)[0]   # STORAGE_DEVICE_DESCRIPTOR.BusType
    finally:
        kernel32.CloseHandle(handle)


def is_card(path: str) -> bool:
    """是不是一整张可移动存储卡（读卡器里的卡、U 盘）。

    本机硬盘和 USB 移动硬盘都不算；只有可移动存储卡的根目录才会按"白名单以外全部清除"处理。
    """
    if any(_same(path, p) for p in _fake_paths()):
        return True
    drive, tail = os.path.splitdrive(os.path.abspath(path))
    if tail not in ("\\", "/") or not drive.endswith(":"):
        return False
    if ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == _DRIVE_REMOVABLE:
        return True
    return _bus_type(drive[0]) in (_BUS_SD, _BUS_MMC)


def pc_markers(path: str) -> list[str]:
    """所选位置的根目录里有哪些电脑硬盘才有的东西。"""
    try:
        names = {e.name.lower(): e.name for e in os.scandir(path)}
    except OSError:
        return []
    return [names[m] for m in PC_MARKERS if m in names]


def check_target(path: str) -> str | None:
    """返回不能操作这个位置的原因；可以操作就返回 None。"""
    if not path or not os.path.isdir(path):
        return "这个位置不存在"
    full = os.path.abspath(path)
    drive, tail = os.path.splitdrive(full)
    if drive.upper() == _system_drive() and tail in ("\\", "/", "") or os.path.isdir(os.path.join(full, "Windows", "System32")):
        return "这是系统盘，不能当存储卡用"
    for env in _PC_DIR_ENVS:
        important = os.environ.get(env)
        if important and os.path.isdir(important):
            important = os.path.abspath(important)
            try:
                inside = os.path.commonpath([os.path.normcase(important), os.path.normcase(full)]) == os.path.normcase(full)
            except ValueError:         # 不在同一个盘
                inside = False
            if inside:
                return f"{important} 在这个位置里面，这是电脑上的重要文件夹，不能当存储卡用"
    found = pc_markers(full)
    if found:
        return f"这里有 {'、'.join(found[:3])}，看起来是电脑硬盘，不能当存储卡用"
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
        "ams": ams, "hos": hos, "looks_like_sd": is_switch_sd(path), "is_drive": is_root, "is_card": is_card(path),
    }


def detect() -> list[dict]:
    """自动识别只找可移动存储卡。电脑硬盘、移动硬盘就算有 Nintendo 文件夹也不列出来，要用只能手动选。"""
    paths = _fake_paths()
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        root = f"{letter}:\\"
        if not mask >> i & 1 or letter + ":" == _system_drive():
            continue
        try:
            if is_card(root) and is_switch_sd(root) and check_target(root) is None:
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
