"""换卡用到的整块磁盘：按扇区读写、写之前把上面的卷锁住、写完让 Windows 重新认分区。

认卡、列卷在共用的 nxhost.cards 里。读写整块磁盘需要管理员权限；只是列出磁盘不需要。
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager

from core.clone import FileDevice
from nxhost import cards
from nxhost.cards import BUS_VIRTUAL, disk_path, volume_serial, volumes  # noqa: F401  （换卡的其他代码和测试从这里取）

VOLUME_WAIT_SECONDS = 30
_IOCTL_DISK_UPDATE_PROPERTIES = 0x70140
_LOCK_TRIES = 10


class DiskError(OSError):
    pass


def list_disks(include_virtual: bool = False) -> list[dict]:
    """能当存储卡用的磁盘。系统盘和普通硬盘不在里面。include_virtual 只给测试用。"""
    return cards.card_disks(include_virtual)


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
            handle = cards.open_device(volume["guid"].rstrip("\\"), cards.GENERIC_READ | cards.GENERIC_WRITE)
            if not handle:
                raise DiskError("打不开这张卡上的分区，需要用管理员身份运行")
            handles.append(handle)
            for _ in range(_LOCK_TRIES):
                if cards.ioctl(handle, cards.FSCTL_LOCK_VOLUME) is not None:
                    break
                time.sleep(0.5)
            else:
                raise DiskError(f"这张卡{('（' + volume['letter'] + '）') if volume['letter'] else ''}正在被别的程序使用，"
                                "请关掉打开着它的窗口再试")
            cards.ioctl(handle, cards.FSCTL_DISMOUNT_VOLUME)
        yield
    finally:
        for handle in handles:
            cards.close(handle)


def rescan(number: int) -> None:
    """让 Windows 重新读这块磁盘的分区表。"""
    handle = cards.open_device(disk_path(number), cards.GENERIC_READ | cards.GENERIC_WRITE)
    if handle:
        cards.ioctl(handle, _IOCTL_DISK_UPDATE_PROPERTIES)
        cards.close(handle)




def wait_for_volume(number: int, offset: int, seconds: float = VOLUME_WAIT_SECONDS, serial: int | None = None) -> str | None:
    """等 Windows 把这个位置上的分区挂出来，返回盘符（像 "E:\\"）。
    serial 是刚格式化时写进去的卷序列号：同一张卡重新分区时，旧的卷可能还没卸掉，位置又一样，要靠它认出是新的。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        for volume in volumes():
            if volume["disk"] == number and volume["offset"] == offset and volume["letter"]:
                if serial is None or (volume_serial(volume["letter"]) == serial and os.path.isdir(volume["letter"])):
                    return volume["letter"]
        time.sleep(0.5)
    return None
