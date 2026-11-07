"""存储卡的分区布局：读旧卡、找虚拟系统分区、给新卡算布局和分区表。

规则照 hekate 的分区管理来（nyx/nyx_gui/frontend/gui_tools_partition_manager.c、gui_emummc_tools.c）：
- 前 16 MB 留空，FAT32 从第 0x8000 扇区开始，后面依次是 Linux、Android、虚拟系统分区，末尾留 1 MB。
- 有 Android 分区时除了 MBR 还有一份 GPT，两份都要对。
- 虚拟系统分区里先是 16 MB 保护区，然后 BOOT0、BOOT1 各 4 MB，再往后是系统镜像（以 GPT 开头）。

换卡时 FAT32 后面的分区搬到新卡的末尾，顺序和间隔不变（虚拟系统分区可以另外指定大小），
多出来或者少掉的空间都算在 FAT32 上。
"""

from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass, field

SECTOR = 512
ALIGN_SECTORS = 0x8000            # 16 MB
MB_SECTORS = 0x800                # 1 MB
PROTECTIVE_SECTORS = 0x8000       # 虚拟系统分区开头的保护区
BOOT_SECTORS = 0x4000             # BOOT0 + BOOT1
MIN_FAT_SECTORS = 512 * MB_SECTORS       # 再小就建不出像样的 FAT32 了
TYPE_FAT32, TYPE_EMUMMC, TYPE_LINUX, TYPE_GPT = 0x0C, 0xE0, 0x83, 0xEE
GPT_MAGIC = b"EFI PART"
GPT_HEADER_BYTES, GPT_ENTRY_BYTES, GPT_TABLE_SECTORS = 92, 128, 32
_MBR_TABLE, _MBR_SIGNATURE = 0x1BE, 0x1FE
KIND_NAMES = {"emummc": "虚拟系统", "linux": "Linux", "android": "Android"}


class LayoutError(Exception):
    """这张卡的布局处理不了，文字直接给用户看。"""


@dataclass(frozen=True)
class Partition:
    start: int               # 扇区
    size: int                # 扇区
    type: int = 0            # MBR 里的类型；只在 GPT 里有的分区是 0
    name: str = ""           # GPT 里的名字
    mbr_slot: int = -1       # 在 MBR 里是第几项，没有就是 -1
    gpt_slot: int = -1

    @property
    def end(self) -> int:
        return self.start + self.size


@dataclass(frozen=True)
class RawEmummc:
    """分区形式的虚拟系统。sector 是写进 emummc.ini 和 raw_based 的那个扇区号。"""
    partition: Partition
    sector: int
    used: int                # 从分区开头算起，实际有内容的扇区数


@dataclass(frozen=True)
class Card:
    total: int
    fat: Partition
    extras: list[Partition]                  # FAT32 后面的分区，按位置排好
    emummcs: list[RawEmummc]
    mbr: bytes
    gpt_header: bytes | None = None
    gpt_entries: bytes | None = None

    def kind(self, part: Partition) -> str:
        if any(e.partition == part for e in self.emummcs):
            return "emummc"
        return "linux" if part.type == TYPE_LINUX or part.name == "l4t" else "android"


@dataclass(frozen=True)
class Move:
    old: Partition
    new_start: int
    new_size: int            # 新分区的大小；和旧的不一样就是要调整大小
    length: int              # 原样搬时要搬的扇区数
    kind: str


@dataclass(frozen=True)
class Plan:
    fat: Partition
    moves: list[Move]
    tables: list[tuple[int, bytes]]          # 要写的分区表：(扇区, 内容)
    sectors: list[tuple[int, int | None]] = field(default_factory=list)   # 虚拟系统的扇区号：(旧, 新)；删掉的新的是 None


def _mbr_entries(mbr: bytes) -> list[Partition]:
    parts = []
    for slot in range(4):
        entry = mbr[_MBR_TABLE + slot * 16:_MBR_TABLE + slot * 16 + 16]
        kind, start, size = entry[4], *struct.unpack_from("<II", entry, 8)
        if kind and size:
            parts.append(Partition(start, size, kind, mbr_slot=slot))
    return parts


def mbr_bytes(parts: list[Partition], template: bytes | None = None) -> bytes:
    """template 是旧卡的 MBR：除了分区表以外的内容（比如 hekate 存在里面的东西）原样带过去。"""
    mbr = bytearray(template[:SECTOR] if template else bytes(SECTOR))
    mbr[_MBR_TABLE:_MBR_SIGNATURE] = bytes(64)
    mbr[0x1B8:0x1BC] = os.urandom(4)                          # 磁盘签名每张卡都要不一样，Windows 靠它区分磁盘
    for part in parts:
        entry = bytearray(16)
        entry[4] = part.type
        struct.pack_into("<II", entry, 8, part.start, part.size)
        mbr[_MBR_TABLE + part.mbr_slot * 16:_MBR_TABLE + part.mbr_slot * 16 + 16] = entry
    mbr[_MBR_SIGNATURE:] = b"\x55\xAA"
    return bytes(mbr)


def _read_gpt(device) -> tuple[bytes, bytes] | None:
    header = device.read_at(SECTOR, SECTOR)
    if header[:8] != GPT_MAGIC:
        return None
    table_lba, count, entry_size = struct.unpack_from("<QII", header, 0x48)
    if entry_size != GPT_ENTRY_BYTES or not 0 < count <= GPT_TABLE_SECTORS * SECTOR // GPT_ENTRY_BYTES:
        raise LayoutError("这张卡的 GPT 分区表格式不常见，现在处理不了")
    return header, device.read_at(table_lba * SECTOR, GPT_TABLE_SECTORS * SECTOR)


def _gpt_parts(header: bytes, entries: bytes) -> list[Partition]:
    parts = []
    for slot in range(struct.unpack_from("<I", header, 0x50)[0]):
        entry = entries[slot * GPT_ENTRY_BYTES:(slot + 1) * GPT_ENTRY_BYTES]
        first, last = struct.unpack_from("<QQ", entry, 32)
        if entry[:16] != bytes(16) and last >= first > 0:
            name = entry[56:128].decode("utf-16-le", "replace").split("\0")[0]
            parts.append(Partition(first, last - first + 1, name=name, gpt_slot=slot))
    return parts


def _has_gpt(device, sector: int) -> bool:
    if (sector + 1) * SECTOR > device.size:
        return False
    return device.read_at(sector * SECTOR, SECTOR)[:8] == GPT_MAGIC


def _probe_emummc(device, part: Partition) -> RawEmummc | None:
    """和 hekate 的"修复原始分区"用同一个办法认：看固定位置有没有系统镜像的 GPT 头。"""
    for offset in (PROTECTIVE_SECTORS, 0):                     # 新式有保护区，老式没有
        head = part.start + offset + BOOT_SECTORS + 1
        if not _has_gpt(device, head):
            continue
        last_lba = struct.unpack_from("<Q", device.read_at(head * SECTOR, SECTOR), 0x20)[0]   # 镜像的最后一个扇区
        used = offset + BOOT_SECTORS + last_lba + 1
        if not offset + BOOT_SECTORS < used <= part.size:
            used = part.size                                   # 读到的数不合理就整个分区都搬
        return RawEmummc(part, part.start + offset, used)
    return None


def scan(device) -> Card:
    mbr = device.read_at(0, SECTOR)
    if len(mbr) < SECTOR or mbr[_MBR_SIGNATURE:] != b"\x55\xAA":
        raise LayoutError("这张卡没有分区表")
    in_mbr = _mbr_entries(mbr)
    first = next((p for p in in_mbr if p.mbr_slot == 0), None)
    if first is None or first.type in (TYPE_GPT, TYPE_EMUMMC, TYPE_LINUX):
        raise LayoutError("这张卡的第一个分区不是 FAT32，不像是 Switch 用的卡")
    gpt = _read_gpt(device) if any(p.type == TYPE_GPT for p in in_mbr) else None
    merged: dict[int, Partition] = {p.start: p for p in in_mbr if p.type != TYPE_GPT}
    for part in _gpt_parts(*gpt) if gpt else []:
        known = merged.get(part.start)
        merged[part.start] = Partition(part.start, part.size, known.type if known else 0, part.name,
                                       known.mbr_slot if known else -1, part.gpt_slot)
    fat = merged.pop(first.start)
    extras = sorted(merged.values(), key=lambda p: p.start)
    if any(p.start < fat.end for p in extras) or any(a.end > b.start for a, b in zip(extras, extras[1:])):
        raise LayoutError("这张卡上的分区互相重叠，分区表可能坏了")
    emummcs = [e for e in (_probe_emummc(device, p) for p in extras) if e]
    return Card(device.size // SECTOR, fat, extras, emummcs, mbr, *(gpt or (None, None)))


def _gpt_tables(card: Card, total: int, fat: Partition, placed: dict[int, tuple[int, int]]) -> list[tuple[int, bytes]]:
    """placed：旧的起点 → (新的起点, 新的大小)。不在 placed 里的分区是要删掉的，它的表项去掉，后面的往前挪。"""
    count = struct.unpack_from("<I", card.gpt_header, 0x50)[0]
    kept = []
    for slot in range(count):
        entry = bytearray(card.gpt_entries[slot * GPT_ENTRY_BYTES:(slot + 1) * GPT_ENTRY_BYTES])
        first = struct.unpack_from("<Q", entry, 32)[0]
        if entry[:16] == bytes(16) or not first:
            continue
        if first == card.fat.start:
            start, size = fat.start, fat.size
        elif first in placed:
            start, size = placed[first]
        else:
            continue
        struct.pack_into("<QQ", entry, 32, start, start + size - 1)
        kept.append(bytes(entry))
    entries = bytearray(b"".join(kept).ljust(len(card.gpt_entries), b"\x00"))
    disk_guid = os.urandom(10)

    def header(my_lba: int, alt_lba: int, table_lba: int) -> bytes:
        data = bytearray(SECTOR)
        data[:GPT_HEADER_BYTES] = card.gpt_header[:GPT_HEADER_BYTES]
        data[0x38:0x42] = disk_guid                                                # 后 6 个字节是 hekate 的标记，留着
        struct.pack_into("<I", data, 0x10, 0)
        struct.pack_into("<QQ", data, 0x18, my_lba, alt_lba)
        struct.pack_into("<Q", data, 0x30, total - MB_SECTORS - 1)                 # 最后一个能用的扇区，hekate 留 1 MB
        struct.pack_into("<Q", data, 0x48, table_lba)
        struct.pack_into("<I", data, 0x58, zlib.crc32(bytes(entries[:count * GPT_ENTRY_BYTES])))
        struct.pack_into("<I", data, 0x10, zlib.crc32(bytes(data[:GPT_HEADER_BYTES])))
        return bytes(data)

    backup_table = total - 1 - GPT_TABLE_SECTORS
    return [(1, header(1, total - 1, 2)), (2, bytes(entries)),
            (backup_table, bytes(entries)), (total - 1, header(total - 1, 1, backup_table))]


def span_sectors(card: Card, sizes: dict[int, int] | None = None) -> int:
    """FAT32 后面那些分区按新大小排下来一共占多少扇区（大小为 0 的是删掉的，连同它前面的间隔一起不算）。"""
    sizes = sizes or {}
    total, first = 0, True
    for index, part in enumerate(card.extras):
        size = sizes.get(index, part.size)
        if size:
            total += size + (0 if first else part.start - card.extras[index - 1].end)
            first = False
    return total


def plan(new_total: int, card: Card, sizes: dict[int, int] | None = None) -> Plan:
    """给新卡算布局。sizes：第几个分区（按 card.extras 的顺序）→ 新的扇区数；0 是删掉；没写的大小不变。"""
    usable = new_total - new_total % ALIGN_SECTORS
    sizes = sizes or {}
    placed: dict[int, tuple[int, int]] = {}
    moves = []
    used = {e.partition: e.used for e in card.emummcs}
    keep = [i for i, p in enumerate(card.extras) if sizes.get(i, p.size)]
    if not keep:
        fat = Partition(ALIGN_SECTORS, usable - ALIGN_SECTORS - MB_SECTORS, card.fat.type or TYPE_FAT32, card.fat.name, 0, card.fat.gpt_slot)
    else:
        cursor = (usable - MB_SECTORS - span_sectors(card, sizes)) // MB_SECTORS * MB_SECTORS   # 末尾留 1 MB，GPT 的备份也放在里面
        fat = Partition(ALIGN_SECTORS, cursor - ALIGN_SECTORS, card.fat.type, card.fat.name, 0, card.fat.gpt_slot)
    for index, part in enumerate(card.extras):
        size = sizes.get(index, part.size)
        if not size:
            moves.append(Move(part, -1, 0, 0, card.kind(part)))
            continue
        cursor += part.start - card.extras[index - 1].end if index and index != keep[0] else 0
        placed[part.start] = (cursor, size)
        moves.append(Move(part, cursor, size, used.get(part, part.size), card.kind(part)))
        cursor += size
    if fat.size < MIN_FAT_SECTORS:
        raise LayoutError("新卡太小：放下虚拟系统这些分区之后，剩不下放文件的空间")
    # 还剩 Linux 或 Android 才需要 GPT；只剩虚拟系统的话照 hekate 的做法只用 MBR
    keep_gpt = bool(card.gpt_header) and any(card.kind(card.extras[i]) != "emummc" for i in keep)
    in_mbr = [fat]
    taken = {0}
    for part in _mbr_entries(card.mbr):
        if part.type == TYPE_GPT:
            if keep_gpt:
                in_mbr.append(Partition(1, min(new_total - 1, 0xFFFFFFFF), TYPE_GPT, mbr_slot=part.mbr_slot))
                taken.add(part.mbr_slot)
        elif part.mbr_slot != 0 and part.start in placed:
            start, size = placed[part.start]
            in_mbr.append(Partition(start, size, part.type, mbr_slot=part.mbr_slot))
            taken.add(part.mbr_slot)
    if not keep_gpt:
        # 原来只记在 GPT 里的虚拟系统分区，去掉 GPT 之后要在 MBR 里占一项
        for i in keep:
            part = card.extras[i]
            if part.mbr_slot < 0:
                free = [slot for slot in range(1, 4) if slot not in taken]
                if not free:
                    raise LayoutError("分区太多，MBR 里放不下了")
                start, size = placed[part.start]
                in_mbr.append(Partition(start, size, part.type or TYPE_EMUMMC, mbr_slot=free[0]))
                taken.add(free[0])
    tables = [(0, mbr_bytes(in_mbr, card.mbr))]
    if keep_gpt:
        tables += _gpt_tables(card, new_total, fat, placed)
    sectors = [(e.sector, placed[e.partition.start][0] + e.sector - e.partition.start if e.partition.start in placed else None)
               for e in card.emummcs]
    return Plan(fat, moves, tables, sectors)


def blank_card(total: int) -> Card:
    """一张只有 FAT32 的卡，给"没有别的分区"的情况和测试用。"""
    fat = Partition(ALIGN_SECTORS, total - ALIGN_SECTORS, TYPE_FAT32, mbr_slot=0)
    return Card(total, fat, [], [], mbr_bytes([fat]))
