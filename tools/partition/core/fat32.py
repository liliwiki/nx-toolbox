"""在一段扇区上建 FAT32。Windows 自带的格式化不给超过 32 GB 的分区建 FAT32，所以自己写。"""

from __future__ import annotations

import os
import struct

from core.layout import SECTOR

LABEL = "SWITCH SD"
CLUSTER_BYTES = 65536             # hekate 先用 64 KB 簇，建不出来再减半
MIN_CLUSTER_BYTES = 4096
MIN_CLUSTERS, MAX_CLUSTERS = 65526, 0x0FFFFFF5
RESERVED_BASE = 32
DATA_ALIGN = 0x2000               # 数据区按 4 MB 对齐，存储卡按块擦写，对齐了更快
FATS = 2
ROOT_CLUSTER = 2
ZERO_CHUNK = 4 * 1024 * 1024
_END, _MEDIA = 0x0FFFFFFF, 0xF8
_ATTR_VOLUME = 0x08


class FormatError(Exception):
    pass


def _geometry(sectors: int, cluster_bytes: int) -> tuple[int, int, int] | None:
    """返回 (保留扇区数, 每个 FAT 的扇区数, 簇数)；这个簇大小建不出 FAT32 就返回 None。"""
    per_cluster = cluster_bytes // SECTOR
    fat_sectors = 1
    while True:
        # 保留区补到让数据区对齐
        reserved = RESERVED_BASE + (-(RESERVED_BASE + FATS * fat_sectors)) % DATA_ALIGN
        clusters = (sectors - reserved - FATS * fat_sectors) // per_cluster
        needed = -(-(clusters + 2) * 4 // SECTOR)
        if needed <= fat_sectors:
            break
        fat_sectors = needed
    if not MIN_CLUSTERS <= clusters <= MAX_CLUSTERS or reserved > 0xFFFF:
        return None
    return reserved, fat_sectors, clusters


def format_partition(device, start_sector: int, sectors: int, label: str = LABEL, progress=lambda frac: None,
                     cluster_bytes: int = CLUSTER_BYTES) -> dict:
    geometry = _geometry(sectors, cluster_bytes)
    while geometry is None and cluster_bytes > MIN_CLUSTER_BYTES:
        cluster_bytes //= 2
        geometry = _geometry(sectors, cluster_bytes)
    if geometry is None:
        raise FormatError("这个分区的大小建不了 FAT32")
    reserved, fat_sectors, clusters = geometry
    per_cluster = cluster_bytes // SECTOR
    name = label.upper().encode("ascii", "replace")[:11].ljust(11)

    boot = bytearray(SECTOR)
    boot[0:3] = b"\xEB\x58\x90"
    boot[3:11] = b"MSDOS5.0"
    struct.pack_into("<HBHBHHBHHHII", boot, 11, SECTOR, per_cluster, reserved, FATS, 0, 0, _MEDIA, 0, 63, 255,
                     start_sector, sectors)
    struct.pack_into("<IHHIHH", boot, 36, fat_sectors, 0, 0, ROOT_CLUSTER, 1, 6)      # FSInfo 在第 1 扇区，备份引导区在第 6
    boot[64], boot[66] = 0x80, 0x29
    boot[67:71] = os.urandom(4)
    boot[71:82] = name
    boot[82:90] = b"FAT32   "
    boot[510:] = b"\x55\xAA"

    info = bytearray(SECTOR)
    info[0:4], info[484:488] = b"RRaA", b"rrAa"
    struct.pack_into("<II", info, 488, clusters - 1, ROOT_CLUSTER + 1)                # 空闲簇数、下一个空闲簇
    info[510:] = b"\x55\xAA"

    base = start_sector * SECTOR
    # 保留区、两个 FAT、根目录所在的簇全部清零，旧数据留在这里会被当成文件
    to_zero = (reserved + FATS * fat_sectors + per_cluster) * SECTOR
    zeros = bytes(ZERO_CHUNK)
    for offset in range(0, to_zero, ZERO_CHUNK):
        device.write_at(base + offset, zeros[:min(ZERO_CHUNK, to_zero - offset)])
        progress(min(1.0, (offset + ZERO_CHUNK) / to_zero))
    for copy in (0, 6):
        device.write_at(base + copy * SECTOR, bytes(boot))
        device.write_at(base + (copy + 1) * SECTOR, bytes(info))
    head = bytearray(SECTOR)
    struct.pack_into("<III", head, 0, 0x0FFFFF00 | _MEDIA, _END, _END)                # 前两项固定，第三项是根目录
    for copy in range(FATS):
        device.write_at(base + (reserved + copy * fat_sectors) * SECTOR, bytes(head))
    root = bytearray(SECTOR)
    root[0:11], root[11] = name, _ATTR_VOLUME
    device.write_at(base + (reserved + FATS * fat_sectors) * SECTOR, bytes(root))
    return {"cluster_bytes": cluster_bytes, "clusters": clusters, "reserved": reserved, "fat_sectors": fat_sectors}
