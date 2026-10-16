"""虚拟系统里的 USER 分区：读占用、调整大小。

USER 是加密的 FAT32（密钥是 prod.keys 里的 bis_key_03）。调整大小时不动簇的编号：
每个用着的簇还叫原来的号，只是 FAT 表变大变小之后数据区的起点会挪，内容要按新位置重新加密。
缩小时落在新范围外面的簇搬到前面的空位上，并把指向它们的地方改过来。
旧卡只读；新的 USER 直接写到新卡上。

做法参照 hekate 建"缩小的虚拟系统"的那段（fe_emummc_tools.c），区别是它把 USER 格式化成空的，这里保留内容。
"""

from __future__ import annotations

import re
import struct
import zlib
from array import array
from dataclasses import dataclass
from pathlib import Path

from core.xts import UNIT, Xts
from nxhost.jobs import Cancelled  # noqa: F401  （job.check 会抛出它，这里不拦）

SECTOR = 512
KEY_FILE = "switch/prod.keys"
KEY_NAMES = ("bis_key_03", "bis_key_02")           # SYSTEM 和 USER 用的是同一把
GPT_SECTORS = 33                                   # GPT 备份占的扇区：32 个表项扇区 + 1 个头
NAND_PATROL_SECTOR = 0xC20                         # 在 BOOT0 里，改了大小要清掉（hekate 也清）
BOOT_SECTORS = 0x4000
CLUSTER_ALIGN = 0x20                               # USER 的大小按簇对齐，和 hekate 一样
FIRST_CLUSTER = 2
EOC_MIN, BAD = 0x0FFFFFF8, 0x0FFFFFF7
ENTRY_MASK = 0x0FFFFFFF
INVALID = 0xFFFFFFFF
SPARE_CLUSTERS = 2048 * 64                         # 缩小时至少留这么多空簇给系统用（16 KB 的簇就是 2 GB）
CHUNK_UNITS = 128                                  # 一次处理 2 MB
_ATTR_LFN, _ATTR_VOLUME, _ATTR_DIR = 0x0F, 0x08, 0x10


class UserError(Exception):
    """文字直接给用户看。"""


def read_keys(card_root: Path) -> tuple[bytes, bytes] | None:
    """从卡上的 prod.keys 里取 USER 分区的密钥：(加密用的, 调整值用的)。"""
    try:
        text = (Path(card_root) / KEY_FILE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for name in KEY_NAMES:
        match = re.search(rf"^\s*{name}\s*=\s*([0-9a-fA-F]{{64}})\s*$", text, re.MULTILINE)
        if match:
            key = bytes.fromhex(match.group(1))
            return key[:16], key[16:]
    return None


# ---- 系统镜像里的分区表 ----

@dataclass(frozen=True)
class Nand:
    """虚拟系统里的系统镜像。offset 是它在设备上的起点（字节）。"""
    offset: int
    sectors: int             # 镜像一共多少扇区
    mbr: bytes
    header: bytes
    entries: bytes
    user_slot: int
    user_start: int          # USER 在镜像里的起点（扇区）
    user_sectors: int


def read_nand(device, offset: int) -> Nand:
    header = device.read_at(offset + SECTOR, SECTOR)
    if header[:8] != b"EFI PART":
        raise UserError("虚拟系统里的分区表读不出来")
    last_lba = struct.unpack_from("<Q", header, 0x20)[0]
    table_lba, count, entry_size = struct.unpack_from("<QII", header, 0x48)
    if entry_size != 128 or not 0 < count <= 128:
        raise UserError("虚拟系统里的分区表格式不常见")
    entries = device.read_at(offset + table_lba * SECTOR, 32 * SECTOR)
    for slot in range(count):
        entry = entries[slot * 128:(slot + 1) * 128]
        if entry[56:64] == "USER".encode("utf-16-le"):
            first, last = struct.unpack_from("<QQ", entry, 32)
            if any(struct.unpack_from("<Q", entries, s * 128 + 32)[0] > first for s in range(count)):
                raise UserError("USER 不是虚拟系统里的最后一个分区，现在处理不了")
            return Nand(offset, last_lba + 1, device.read_at(offset, SECTOR), header, entries, slot, first, last - first + 1)
    raise UserError("虚拟系统里没有找到 USER 分区")


def user_sectors_for(nand: Nand, nand_sectors: int) -> int:
    """镜像改成这么大时 USER 有多少扇区。"""
    return (nand_sectors - nand.user_start - GPT_SECTORS) // CLUSTER_ALIGN * CLUSTER_ALIGN


def nand_tables(nand: Nand, nand_sectors: int) -> list[tuple[int, bytes]]:
    """镜像改成这么大之后要写的分区表：(镜像里的扇区, 内容)。"""
    count = struct.unpack_from("<I", nand.header, 0x50)[0]
    size = struct.unpack_from("<I", nand.header, 0x0C)[0]
    entries = bytearray(nand.entries)
    struct.pack_into("<Q", entries, nand.user_slot * 128 + 40, nand.user_start + user_sectors_for(nand, nand_sectors) - 1)

    def header(my_lba: int, alt_lba: int, table_lba: int) -> bytes:
        data = bytearray(SECTOR)
        data[:size] = nand.header[:size]
        struct.pack_into("<I", data, 0x10, 0)
        struct.pack_into("<QQ", data, 0x18, my_lba, alt_lba)
        struct.pack_into("<Q", data, 0x30, nand_sectors - GPT_SECTORS - 1)
        struct.pack_into("<Q", data, 0x48, table_lba)
        struct.pack_into("<I", data, 0x58, zlib.crc32(bytes(entries[:count * 128])))
        struct.pack_into("<I", data, 0x10, zlib.crc32(bytes(data[:size])))
        return bytes(data)

    mbr = bytearray(nand.mbr)
    struct.pack_into("<I", mbr, 0x1BE + 12, nand_sectors - 1)                  # 保护分区的大小，不含 MBR 自己
    return [(0, bytes(mbr)), (1, header(1, nand_sectors - 1, 2)), (2, bytes(entries)),
            (nand_sectors - GPT_SECTORS, bytes(entries)), (nand_sectors - 1, header(nand_sectors - 1, 1, nand_sectors - GPT_SECTORS))]


# ---- 解密之后的 USER ----

class Plain:
    """按明文读写 USER。base 是 USER 在设备上的起点（字节）。读写的位置和长度不用对齐。"""

    def __init__(self, device, base: int, xts: Xts) -> None:
        self._device, self._base, self._xts = device, base, xts

    def read(self, offset: int, length: int) -> bytes:
        first, last = offset // UNIT, (offset + length + UNIT - 1) // UNIT
        raw = self._device.read_at(self._base + first * UNIT, (last - first) * UNIT)
        if len(raw) != (last - first) * UNIT:
            raise OSError("读到一半读不出来了，卡可能松了或者有坏块")
        plain = self._xts.decrypt(first, raw)
        return plain[offset - first * UNIT:offset - first * UNIT + length]

    def write_units(self, first: int, data: bytes) -> None:
        self._device.write_at(self._base + first * UNIT, self._xts.encrypt(first, data))


@dataclass
class Fat:
    boot: bytes
    reserved: int            # 保留扇区数
    fats: int
    fat_sectors: int
    per_cluster: int         # 每簇扇区数
    total: int               # USER 的扇区数（文件系统自己记的）
    root: int
    info_sector: int
    backup_sector: int
    table: array             # 每个簇的 FAT 项

    @property
    def data_start(self) -> int:
        return self.reserved + self.fats * self.fat_sectors

    @property
    def clusters(self) -> int:
        return (self.total - self.data_start) // self.per_cluster

    @property
    def cluster_bytes(self) -> int:
        return self.per_cluster * SECTOR

    def used(self) -> list[int]:
        limit = min(self.clusters + FIRST_CLUSTER, len(self.table))
        return [c for c in range(FIRST_CLUSTER, limit) if self.table[c] & ENTRY_MASK]

    def offset(self, cluster: int) -> int:
        return (self.data_start + (cluster - FIRST_CLUSTER) * self.per_cluster) * SECTOR


def read_fat(plain: Plain) -> Fat:
    boot = plain.read(0, SECTOR)
    if boot[510:512] != b"\x55\xAA" or boot[82:87] != b"FAT32" or struct.unpack_from("<H", boot, 11)[0] != SECTOR:
        raise UserError("用卡上的密钥解不开这个虚拟系统。prod.keys 可能是别的机器的，或者是很久以前导出的")
    per_cluster, reserved, fats = boot[13], struct.unpack_from("<H", boot, 14)[0], boot[16]
    total, fat_sectors = struct.unpack_from("<I", boot, 32)[0], struct.unpack_from("<I", boot, 36)[0]
    root, info_sector, backup_sector = struct.unpack_from("<IHH", boot, 44)
    if not (per_cluster and reserved and fats and total and fat_sectors):
        raise UserError("USER 分区的文件系统读不懂")
    table = array("I")
    table.frombytes(plain.read(reserved * SECTOR, fat_sectors * SECTOR))
    return Fat(boot, reserved, fats, fat_sectors, per_cluster, total, root, info_sector, backup_sector, table)


def usage(fat: Fat) -> dict:
    used = len(fat.used())
    return {"bytes": fat.total * SECTOR, "used": used * fat.cluster_bytes + fat.data_start * SECTOR,
            "clusters": fat.clusters, "used_clusters": used}


# ---- 调整大小 ----

@dataclass
class Resize:
    total: int                       # 新的扇区数
    fat_sectors: int
    clusters: int
    moved: dict[int, int]            # 落在新范围外面的簇：旧号 → 新号

    def data_start(self, fat: Fat) -> int:
        return fat.reserved + fat.fats * self.fat_sectors


def minimum_sectors(fat: Fat) -> int:
    """USER 最小能缩到多少扇区：放得下现在用着的簇，再留一点余量给系统写东西。"""
    used = len(fat.used())
    spare = max(SPARE_CLUSTERS, used // 20)
    return fat.data_start + (used + spare) * fat.per_cluster


def plan(fat: Fat, total: int) -> Resize:
    total = total // CLUSTER_ALIGN * CLUSTER_ALIGN
    fat_sectors = fat.fat_sectors
    while True:
        clusters = (total - fat.reserved - fat.fats * fat_sectors) // fat.per_cluster
        needed = -(-(clusters + FIRST_CLUSTER) * 4 // SECTOR)
        if needed <= fat_sectors:
            break
        # FAT 表变大时让数据区的起点挪整数个加密单元，簇和单元的相对位置就不变
        step = UNIT // SECTOR
        fat_sectors += -(-(needed - fat_sectors) // step) * step
    used = fat.used()
    outside = [c for c in used if c >= clusters + FIRST_CLUSTER]
    if len(used) > clusters:
        raise UserError("USER 里的内容放不进这么小的空间")
    moved = {}
    if outside:
        taken = set(used)
        free = (c for c in range(FIRST_CLUSTER, clusters + FIRST_CLUSTER) if c not in taken)
        moved = dict(zip(outside, free))
        if len(moved) != len(outside):
            raise UserError("USER 里的内容放不进这么小的空间")
    return Resize(total, fat_sectors, clusters, moved)


def _chain(fat: Fat, first: int, limit: int):
    seen = 0
    while FIRST_CLUSTER <= first < len(fat.table) and seen <= limit:
        yield first
        first = fat.table[first] & ENTRY_MASK
        seen += 1
        if first >= BAD:
            return
    if seen > limit:
        raise UserError("USER 里的文件系统有环，可能已经损坏")


def _patch_directories(plain: Plain, fat: Fat, moved: dict[int, int], check) -> dict[int, bytes]:
    """把目录里指向被搬走的簇的地方改成新号。返回改过的目录簇：旧号 → 新内容。"""
    patched: dict[int, bytes] = {}
    pending, visited = [fat.root], set()
    size = fat.cluster_bytes
    while pending:
        start = pending.pop()
        if start in visited:
            continue
        visited.add(start)
        for cluster in _chain(fat, start, fat.clusters):
            check()
            data = bytearray(plain.read(fat.offset(cluster), size))
            changed = False
            for at in range(0, size, 32):
                if data[at] == 0:
                    break
                attr = data[at + 11]
                if data[at] == 0xE5 or attr & 0x3F == _ATTR_LFN or attr & _ATTR_VOLUME:
                    continue
                first = struct.unpack_from("<H", data, at + 20)[0] << 16 | struct.unpack_from("<H", data, at + 26)[0]
                if attr & _ATTR_DIR and data[at:at + 2] not in (b". ", b"..") and first >= FIRST_CLUSTER:
                    pending.append(first)
                if first in moved:
                    struct.pack_into("<H", data, at + 20, moved[first] >> 16)
                    struct.pack_into("<H", data, at + 26, moved[first] & 0xFFFF)
                    changed = True
            if changed:
                patched[cluster] = bytes(data)
    return patched


def _new_head(old: Plain, fat: Fat, resize: Resize) -> bytes:
    """新的保留区和 FAT 表（明文）。"""
    head = bytearray(old.read(0, fat.reserved * SECTOR))
    remap = resize.moved
    for sector in {0, fat.backup_sector} - {None}:
        if sector * SECTOR + SECTOR <= len(head) and head[sector * SECTOR + 82:sector * SECTOR + 87] == b"FAT32":
            struct.pack_into("<I", head, sector * SECTOR + 32, resize.total)
            struct.pack_into("<I", head, sector * SECTOR + 36, resize.fat_sectors)
            struct.pack_into("<I", head, sector * SECTOR + 44, remap.get(fat.root, fat.root))
    for sector in {fat.info_sector, fat.info_sector + fat.backup_sector}:
        at = sector * SECTOR
        if at + SECTOR <= len(head) and head[at:at + 4] == b"RRaA":
            struct.pack_into("<II", head, at + 488, INVALID, INVALID)          # 空闲数让系统自己重新数
    if 4 * SECTOR <= len(head):
        head[3 * SECTOR:4 * SECTOR] = bytes(SECTOR)                             # PRF2SAFE 的记录清掉，系统会重建（hekate 也这么做）
    table = array("I", bytes(resize.fat_sectors * SECTOR))
    limit = resize.clusters + FIRST_CLUSTER
    table[0], table[1] = fat.table[0], fat.table[1]
    for cluster in range(FIRST_CLUSTER, min(len(fat.table), fat.clusters + FIRST_CLUSTER)):
        entry = fat.table[cluster]
        value = entry & ENTRY_MASK
        if not value:
            continue
        target = remap.get(cluster, cluster)
        if target >= limit:
            raise UserError("USER 里的内容放不进这么小的空间")
        if FIRST_CLUSTER <= value < BAD:
            value = remap.get(value, value)
        table[target] = (entry & ~ENTRY_MASK) | value
    return bytes(head) + table.tobytes() * fat.fats


def write_resized(old: Plain, fat: Fat, resize: Resize, new: Plain, progress=lambda done, total: None, check=lambda: None) -> dict:
    """把 USER 按新的大小写到新卡上。只写有内容的部分。"""
    patched = _patch_directories(old, fat, resize.moved, check) if resize.moved else {}
    head = _new_head(old, fat, resize)
    data_start = resize.data_start(fat) * SECTOR
    assert len(head) == data_start
    size = fat.cluster_bytes
    source = {resize.moved.get(c, c): c for c in fat.used()}                  # 新号 → 旧号
    last_cluster = max(source, default=FIRST_CLUSTER - 1)
    end = data_start + (last_cluster - FIRST_CLUSTER + 1) * size               # 新 USER 里有内容的部分到这里为止
    units = -(-end // UNIT)
    total = max(1, units)

    def piece(offset: int, length: int) -> bytes:
        """新 USER 里 [offset, offset+length) 的明文。"""
        parts = []
        while length:
            if offset < data_start:
                take = min(length, data_start - offset)
                parts.append(head[offset:offset + take])
            else:
                cluster, inside = divmod(offset - data_start, size)
                cluster += FIRST_CLUSTER
                origin = source.get(cluster)
                if origin is None:
                    take = min(length, size - inside)
                    parts.append(bytes(take))
                elif origin in patched:
                    take = min(length, size - inside)
                    parts.append(patched[origin][inside:inside + take])
                else:
                    # 连着的簇如果来源也是连着的，就一次读出来
                    run = 1
                    while (inside + length > run * size and source.get(cluster + run) == origin + run
                           and origin + run not in patched):
                        run += 1
                    take = min(length, run * size - inside)
                    parts.append(old.read(fat.offset(origin) + inside, take))
            offset += take
            length -= take
        return b"".join(parts)

    def busy(first: int, count: int) -> bool:
        """这几个单元里有没有内容。全是空簇的就不写。"""
        start, stop = first * UNIT, (first + count) * UNIT
        if start < data_start:
            return True
        a = (start - data_start) // size + FIRST_CLUSTER
        b = (stop - 1 - data_start) // size + FIRST_CLUSTER
        return any(c in source for c in range(a, b + 1))

    written = 0
    for first in range(0, units, CHUNK_UNITS):
        check()
        count = min(CHUNK_UNITS, units - first)
        if busy(first, count):
            new.write_units(first, piece(first * UNIT, count * UNIT))
            written += count
        progress(first + count, total)
    return {"units": written, "moved": len(resize.moved)}


def verify(fat: Fat, new: Plain, resize: Resize, old: Plain | None = None, progress=lambda done, total: None, check=lambda: None) -> None:
    """写完之后从新卡上读回来核对。old 给了的话连内容一起比。"""
    again = read_fat(new)
    if again.total != resize.total or again.fat_sectors != resize.fat_sectors or again.clusters != resize.clusters:
        raise UserError("新的 USER 分区写上去之后读回来不对")
    if len(again.used()) != len(fat.used()):
        raise UserError("新的 USER 分区里用着的空间和旧的对不上")
    # 从根目录走一遍：每个文件和文件夹的簇链都要在范围里、能走到头
    pending, visited, limit = [again.root], set(), again.clusters + FIRST_CLUSTER
    while pending:
        start = pending.pop()
        if start in visited:
            continue
        visited.add(start)
        for cluster in _chain(again, start, again.clusters):
            check()
            if cluster >= limit:
                raise UserError("新的 USER 分区里有文件夹指到了范围外面")
            data = new.read(again.offset(cluster), again.cluster_bytes)
            for at in range(0, len(data), 32):
                if data[at] == 0:
                    break
                attr = data[at + 11]
                if data[at] == 0xE5 or attr & 0x3F == _ATTR_LFN or attr & _ATTR_VOLUME:
                    continue
                first = struct.unpack_from("<H", data, at + 20)[0] << 16 | struct.unpack_from("<H", data, at + 26)[0]
                if first and not FIRST_CLUSTER <= first < limit:
                    raise UserError("新的 USER 分区里有文件指到了范围外面")
                if first and data[at:at + 2] not in (b". ", b".."):
                    if attr & _ATTR_DIR:
                        pending.append(first)
                    elif sum(1 for _ in _chain(again, first, again.clusters)) * again.cluster_bytes < struct.unpack_from("<I", data, at + 28)[0]:
                        raise UserError("新的 USER 分区里有文件的内容不完整")
    if old is None:
        return
    used = fat.used()
    directories = set(_patch_directories(old, fat, resize.moved, check)) if resize.moved else set()
    size, index = fat.cluster_bytes, 0
    batch = max(1, CHUNK_UNITS * UNIT // size)
    while index < len(used):
        check()
        progress(index, len(used))
        cluster = used[index]
        target = resize.moved.get(cluster, cluster)
        run = 1                                              # 旧卡和新卡上都连着的一段，一次读出来比
        while (run < batch and index + run < len(used) and used[index + run] == cluster + run
               and resize.moved.get(cluster + run, cluster + run) == target + run):
            run += 1
        before, after = old.read(fat.offset(cluster), run * size), new.read(again.offset(target), run * size)
        if before != after and any(cluster + i not in directories and before[i * size:(i + 1) * size] != after[i * size:(i + 1) * size]
                                   for i in range(run)):     # 改过的目录簇本来就不一样
            raise UserError("USER 分区的内容写到新卡之后对不上，新卡或读卡器可能有问题")
        index += run
