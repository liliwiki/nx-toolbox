"""Windows 上的整块磁盘：列出存储卡、按扇区读写、写之前把上面的卷锁住。

读写整块磁盘需要管理员权限；只是列出磁盘不需要。
"""

from __future__ import annotations

import ctypes
import os
import struct
import time
from contextlib import contextmanager
from ctypes import wintypes

from core.clone import FileDevice

MAX_DISKS = 64
BUS_USB, BUS_SD, BUS_MMC, BUS_VIRTUAL = 7, 12, 13, 15
CARD_BUSES = (BUS_USB, BUS_SD, BUS_MMC)
VOLUME_WAIT_SECONDS = 30

_GENERIC_READ, _GENERIC_WRITE = 0x80000000, 0x40000000
_SHARE_RW = 0x1 | 0x2
_OPEN_EXISTING = 3
_INVALID = wintypes.HANDLE(-1).value
_IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
_IOCTL_DISK_GET_DRIVE_GEOMETRY_EX = 0x700A0
_IOCTL_DISK_UPDATE_PROPERTIES = 0x70140
_IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x560000
_FSCTL_LOCK_VOLUME, _FSCTL_DISMOUNT_VOLUME = 0x90018, 0x90020
_LOCK_TRIES = 10

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


class DiskError(OSError):
    pass


def _open(path: str, access: int = 0):
    handle = _k32.CreateFileW(path, access, _SHARE_RW, None, _OPEN_EXISTING, 0, None)
    return None if handle == _INVALID or handle is None else handle


def _ioctl(handle, code: int, in_data: bytes = b"", out_size: int = 0) -> bytes | None:
    out = ctypes.create_string_buffer(out_size) if out_size else None
    returned = wintypes.DWORD(0)
    ok = _k32.DeviceIoControl(handle, code, in_data or None, len(in_data), out, out_size, ctypes.byref(returned), None)
    return out.raw[:returned.value] if ok and out else (b"" if ok else None)


def _text(data: bytes, offset: int) -> str:
    return data[offset:data.index(b"\0", offset)].decode("ascii", "replace").strip() if 0 < offset < len(data) else ""


def disk_path(number: int) -> str:
    return rf"\\.\PhysicalDrive{number}"


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
            handle = _open(guid.rstrip("\\"))
            if handle:
                data = _ioctl(handle, _IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, out_size=1024)
                _k32.CloseHandle(handle)
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


def _system_letter() -> str:
    return os.path.splitdrive(os.environ.get("SystemRoot", r"C:\Windows"))[0].upper() + "\\"


def list_disks(include_virtual: bool = False) -> list[dict]:
    """能当存储卡用的磁盘。系统盘和普通硬盘不在里面。include_virtual 只给测试用。"""
    all_volumes = volumes()
    system_disks = {v["disk"] for v in all_volumes if v["letter"].upper() == _system_letter()}
    buses = CARD_BUSES + ((BUS_VIRTUAL,) if include_virtual else ())
    disks = []
    for number in range(MAX_DISKS):
        handle = _open(disk_path(number))
        if not handle:
            continue
        try:
            desc = _ioctl(handle, _IOCTL_STORAGE_QUERY_PROPERTY, struct.pack("<II4x", 0, 0), 1024)
            geometry = _ioctl(handle, _IOCTL_DISK_GET_DRIVE_GEOMETRY_EX, out_size=64)
        finally:
            _k32.CloseHandle(handle)
        if not desc or len(desc) < 32 or not geometry or len(geometry) < 32:
            continue                                            # 读卡器里没插卡时读不到容量
        removable = bool(desc[10])
        vendor, product = (_text(desc, struct.unpack_from("<I", desc, off)[0]) for off in (12, 16))
        bus = struct.unpack_from("<I", desc, 28)[0]
        size = struct.unpack_from("<q", geometry, 24)[0]
        if number in system_disks or bus not in buses or not (removable or bus != BUS_USB or include_virtual):
            continue
        disks.append({"number": number, "size": size, "name": " ".join(x for x in (vendor, product) if x) or "存储卡",
                      "bus": bus, "letters": sorted(v["letter"] for v in all_volumes if v["disk"] == number and v["letter"])})
    return disks


def open_disk(number: int, size: int, writable: bool = False) -> FileDevice:
    try:
        return FileDevice(disk_path(number), writable=writable, size=size)
    except PermissionError as exc:
        raise DiskError("没有权限读写这块磁盘，需要用管理员身份运行") from exc


@contextmanager
def locked(number: int):
    """写整块磁盘之前先把上面的卷锁住并卸下，不然 Windows 不让写卷所在的位置。"""
    handles = []
    try:
        for volume in (v for v in volumes() if v["disk"] == number):
            handle = _open(volume["guid"].rstrip("\\"), _GENERIC_READ | _GENERIC_WRITE)
            if not handle:
                raise DiskError("打不开这张卡上的分区，需要用管理员身份运行")
            handles.append(handle)
            for _ in range(_LOCK_TRIES):
                if _ioctl(handle, _FSCTL_LOCK_VOLUME) is not None:
                    break
                time.sleep(0.5)
            else:
                raise DiskError(f"这张卡{('（' + volume['letter'] + '）') if volume['letter'] else ''}正在被别的程序使用，"
                                "请关掉打开着它的窗口再试")
            _ioctl(handle, _FSCTL_DISMOUNT_VOLUME)
        yield
    finally:
        for handle in handles:
            _k32.CloseHandle(handle)


def rescan(number: int) -> None:
    """让 Windows 重新读这块磁盘的分区表。"""
    handle = _open(disk_path(number), _GENERIC_READ | _GENERIC_WRITE)
    if handle:
        _ioctl(handle, _IOCTL_DISK_UPDATE_PROPERTIES)
        _k32.CloseHandle(handle)


def wait_for_volume(number: int, offset: int, seconds: float = VOLUME_WAIT_SECONDS) -> str | None:
    """等 Windows 把这个位置上的分区挂出来，返回盘符（像 "E:\\"）。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        for volume in volumes():
            if volume["disk"] == number and volume["offset"] == offset and volume["letter"]:
                return volume["letter"]
        time.sleep(0.5)
    return None
