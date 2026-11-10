"""存储卡识别，所有工具共用：哪些是卡、哪些是电脑硬盘、卷在哪块磁盘上、弹出。

只用 Windows 自带的接口，不需要管理员权限（读写整块磁盘才要，那部分在各工具自己那里）。

什么算存储卡：介质可移动（读卡器里的卡、U 盘），或者挂在 SD/MMC 总线上（笔记本自带的读卡器常报成不可移动）。
系统盘、普通硬盘、USB 移动硬盘都不算。
"""

from __future__ import annotations

import ctypes
import os
import string
import struct
from ctypes import wintypes
from dataclasses import dataclass

BUS_USB, BUS_SD, BUS_MMC, BUS_VIRTUAL = 7, 12, 13, 15
CARD_BUSES = (BUS_SD, BUS_MMC)
MAX_DISKS = 64
DRIVE_REMOVABLE = 2

GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
_SHARE_RW = 0x1 | 0x2
_OPEN_EXISTING = 3
_INVALID = wintypes.HANDLE(-1).value
_IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
_IOCTL_DISK_GET_DRIVE_GEOMETRY_EX = 0x700A0
_IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x560000
_IOCTL_STORAGE_EJECT_MEDIA = 0x2D4808
FSCTL_LOCK_VOLUME, FSCTL_DISMOUNT_VOLUME = 0x90018, 0x90020

# 根目录有这些就是电脑硬盘，绝不能当存储卡用
PC_MARKERS = ("windows", "program files", "program files (x86)", "programdata", "users", "documents and settings",
              "$windows.~bt", "$windows.~ws", "pagefile.sys", "hiberfil.sys", "swapfile.sys", "bootmgr")
# 这些电脑文件夹在所选位置里面（或就是它），说明选得太靠上了
_PC_DIR_ENVS = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "USERPROFILE", "APPDATA", "LOCALAPPDATA")

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                             wintypes.DWORD, wintypes.HANDLE]
_k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
                                 wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.FindFirstVolumeW.restype = wintypes.HANDLE
_k32.FindFirstVolumeW.argtypes = [wintypes.LPWSTR, wintypes.DWORD]
_k32.FindNextVolumeW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD]
_k32.FindVolumeClose.argtypes = [wintypes.HANDLE]
_k32.GetVolumePathNamesForVolumeNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                                                  ctypes.POINTER(wintypes.DWORD)]
_k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
_k32.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                       ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR,
                                       wintypes.DWORD]


# ---- 底层：打开设备、发控制码 ----

def open_device(path: str, access: int = 0):
    """打开 \\\\.\\PhysicalDriveN、\\\\.\\E: 或卷的 GUID 路径。打不开返回 None。"""
    handle = _k32.CreateFileW(path, access, _SHARE_RW, None, _OPEN_EXISTING, 0, None)
    return None if handle == _INVALID or handle is None else handle


def close(handle) -> None:
    _k32.CloseHandle(handle)


def ioctl(handle, code: int, in_data: bytes = b"", out_size: int = 0) -> bytes | None:
    """成功时返回输出（没有输出就是空的 bytes），失败返回 None。"""
    out = ctypes.create_string_buffer(out_size) if out_size else None
    returned = wintypes.DWORD(0)
    ok = _k32.DeviceIoControl(handle, code, in_data or None, len(in_data), out, out_size, ctypes.byref(returned), None)
    return out.raw[:returned.value] if ok and out else (b"" if ok else None)


def disk_path(number: int) -> str:
    return rf"\\.\PhysicalDrive{number}"


# ---- 设备是什么 ----

@dataclass(frozen=True)
class Descriptor:
    removable: bool
    bus: int
    vendor: str = ""
    product: str = ""

    @property
    def name(self) -> str:
        return " ".join(x for x in (self.vendor, self.product) if x) or "存储卡"


def _text(data: bytes, offset: int) -> str:
    if not 0 < offset < len(data):
        return ""
    end = data.find(b"\0", offset)
    return data[offset:end if end >= 0 else len(data)].decode("ascii", "replace").strip()


def parse_descriptor(data: bytes | None) -> Descriptor | None:
    """STORAGE_DEVICE_DESCRIPTOR：第 10 字节是否可移动，12/16 是厂商、型号字符串的位置，28 是总线类型。"""
    if not data or len(data) < 32:
        return None
    vendor, product = (_text(data, struct.unpack_from("<I", data, off)[0]) for off in (12, 16))
    return Descriptor(bool(data[10]), struct.unpack_from("<I", data, 28)[0], vendor, product)


def descriptor(path: str) -> Descriptor | None:
    handle = open_device(path)
    if not handle:
        return None
    try:
        return parse_descriptor(ioctl(handle, _IOCTL_STORAGE_QUERY_PROPERTY, struct.pack("<II4x", 0, 0), 1024))
    finally:
        close(handle)


def is_card_device(desc: Descriptor | None, include_virtual: bool = False) -> bool:
    if desc is None:
        return False
    return desc.removable or desc.bus in CARD_BUSES or (include_virtual and desc.bus == BUS_VIRTUAL)


# ---- 卷和盘符 ----

def system_letter() -> str:
    """系统盘，像 "C:\\"。"""
    return os.path.splitdrive(os.environ.get("SystemRoot", r"C:\Windows"))[0].upper() + "\\"


def volumes() -> list[dict]:
    """所有的卷：在哪块磁盘的什么位置、盘符是什么（可能没有）。"""
    found = []
    name = ctypes.create_unicode_buffer(260)
    search = _k32.FindFirstVolumeW(name, 260)
    if search == _INVALID or search is None:
        return found
    try:
        while True:
            guid = name.value                                   # \\?\Volume{...}\
            handle = open_device(guid.rstrip("\\"))
            if handle:
                data = ioctl(handle, _IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, out_size=1024)
                close(handle)
                if data and len(data) >= 32 and struct.unpack_from("<I", data)[0] >= 1:
                    disk, _, offset, length = struct.unpack_from("<IIqq", data, 8)
                    paths = ctypes.create_unicode_buffer(1024)
                    size = wintypes.DWORD(0)
                    _k32.GetVolumePathNamesForVolumeNameW(guid, paths, 1024, ctypes.byref(size))
                    letter = paths.value if len(paths.value) == 3 else ""
                    found.append({"guid": guid, "disk": disk, "offset": offset, "length": length, "letter": letter})
            if not _k32.FindNextVolumeW(search, name, 260):
                break
    finally:
        _k32.FindVolumeClose(search)
    return found


def volume_info(root: str) -> tuple[str, str]:
    """(卷标, 文件系统)，读不到就是两个空字符串。"""
    label, fs = ctypes.create_unicode_buffer(261), ctypes.create_unicode_buffer(261)
    ok = _k32.GetVolumeInformationW(root, label, 261, None, None, None, fs, 261)
    return (label.value, fs.value) if ok else ("", "")


def volume_serial(root: str) -> int | None:
    serial = wintypes.DWORD(0)
    if _k32.GetVolumeInformationW(root, None, 0, ctypes.byref(serial), None, None, None, 0):
        return serial.value
    return None


def drive_is_card(root: str) -> bool:
    """这个盘符（像 "E:\\"）是不是一张存储卡。"""
    drive = os.path.splitdrive(root)[0].upper()
    if not drive.endswith(":") or drive + "\\" == system_letter():
        return False
    if _k32.GetDriveTypeW(drive + "\\") == DRIVE_REMOVABLE:
        return True
    return is_card_device(descriptor(f"\\\\.\\{drive}"))


def card_letters() -> list[str]:
    """插着的存储卡的盘符。"""
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    found = []
    for i, letter in enumerate(string.ascii_uppercase):
        root = f"{letter}:\\"
        if mask >> i & 1:
            try:
                if drive_is_card(root):
                    found.append(root)
            except OSError:
                continue
    return found


# ---- 整块磁盘 ----

def card_disks(include_virtual: bool = False) -> list[dict]:
    """插着的存储卡（整块磁盘）：编号、容量、名字、总线、上面的盘符。include_virtual 只给测试用。"""
    all_volumes = volumes()
    system_disks = {v["disk"] for v in all_volumes if v["letter"].upper() == system_letter()}
    disks = []
    for number in range(MAX_DISKS):
        handle = open_device(disk_path(number))
        if not handle:
            continue
        try:
            desc = parse_descriptor(ioctl(handle, _IOCTL_STORAGE_QUERY_PROPERTY, struct.pack("<II4x", 0, 0), 1024))
            geometry = ioctl(handle, _IOCTL_DISK_GET_DRIVE_GEOMETRY_EX, out_size=64)
        finally:
            close(handle)
        if not geometry or len(geometry) < 32:
            continue                                            # 读卡器里没插卡时读不到容量
        if number in system_disks or not is_card_device(desc, include_virtual):
            continue
        disks.append({"number": number, "size": struct.unpack_from("<q", geometry, 24)[0], "name": desc.name, "bus": desc.bus,
                      "letters": sorted(v["letter"] for v in all_volumes if v["disk"] == number and v["letter"])})
    return disks


# ---- 电脑硬盘的保护 ----

def pc_markers(path: str) -> list[str]:
    """所选位置的根目录里有哪些电脑硬盘才有的东西。"""
    try:
        names = {e.name.lower(): e.name for e in os.scandir(path)}
    except OSError:
        return []
    return [names[m] for m in PC_MARKERS if m in names]


def pc_reason(path: str) -> str | None:
    """这个位置看起来是电脑硬盘或电脑上的重要文件夹的话，返回原因；不是就返回 None。"""
    full = os.path.abspath(path)
    drive, tail = os.path.splitdrive(full)
    if (drive.upper() + "\\" == system_letter() and tail in ("\\", "/", "")) or os.path.isdir(os.path.join(full, "Windows", "System32")):
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


# ---- 弹出 ----

def eject(root: str) -> tuple[bool, str]:
    drive = os.path.splitdrive(os.path.abspath(root))[0]
    if os.path.splitdrive(os.path.abspath(root))[1] not in ("\\", "/"):
        return False, "这是一个文件夹，不是存储卡，不需要弹出"
    handle = open_device(f"\\\\.\\{drive}", GENERIC_READ | GENERIC_WRITE)
    if not handle:
        return False, f"打不开 {drive}，请确认没有别的程序在用这张卡"
    try:
        if ioctl(handle, FSCTL_LOCK_VOLUME) is None:
            return False, "有程序正在使用这张卡，关掉后再试"
        ioctl(handle, FSCTL_DISMOUNT_VOLUME)
        if ioctl(handle, _IOCTL_STORAGE_EJECT_MEDIA) is not None:
            return True, f"{drive} 已安全弹出"
        return False, f"弹出失败（错误 {ctypes.get_last_error()}）"
    finally:
        close(handle)
