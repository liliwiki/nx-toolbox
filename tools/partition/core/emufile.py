"""文件形式的虚拟系统：emuMMC/<文件夹>/eMMC 下面的 BOOT0、BOOT1，和按顺序编号的 00、01……

编号的那些文件连起来就是系统镜像（以 GPT 开头，和分区形式里的一样）。超过 4 GB 时按 0xFE000000 字节一段拆开，
Atmosphere 读的时候按第一个文件的大小算每段多大（见 hekate 的 fe_emummc_tools.c 和 Atmosphere 的 emummc.c）。
"""

from __future__ import annotations

import os
from pathlib import Path

from core.emucfg import FOLDER

SPLIT_BYTES = 0xFE000000
SINGLE_LIMIT = 0xFFFFFFFF          # 不超过这个就是一个文件
MAX_PARTS = 32


def emmc_dir(card_root: Path, name: str) -> Path:
    return Path(card_root) / FOLDER / name / "eMMC"


def part_names(count: int) -> list[str]:
    return [f"{index:02d}" for index in range(count)]


def gpp_paths(folder: Path) -> list[Path]:
    """00、01……连续存在的那些。"""
    paths = []
    for name in part_names(MAX_PARTS):
        path = Path(folder) / name
        if not path.is_file():
            break
        paths.append(path)
    return paths


class SplitFiles:
    """把 00、01……当成一整块按位置读写的设备。"""

    def __init__(self, paths: list[Path], writable: bool = False) -> None:
        if not paths:
            raise OSError("找不到虚拟系统的镜像文件（00）")
        self._handles = [open(p, "r+b" if writable else "rb") for p in paths]
        self._sizes = [os.fstat(h.fileno()).st_size for h in self._handles]
        self.part = self._sizes[0]
        self.size = sum(self._sizes)

    def _spans(self, offset: int, length: int):
        while length > 0:
            index = min(offset // self.part, len(self._handles) - 1)
            inner = offset - index * self.part
            take = min(length, self._sizes[index] - inner)
            if take <= 0:
                raise OSError("超出了虚拟系统镜像的范围")
            yield index, inner, take
            offset += take
            length -= take

    def read_at(self, offset: int, length: int) -> bytes:
        parts = []
        for index, inner, take in self._spans(offset, length):
            handle = self._handles[index]
            handle.seek(inner)
            parts.append(handle.read(take))
        return b"".join(parts)

    def write_at(self, offset: int, data: bytes) -> None:
        view = memoryview(data)
        for index, inner, take in self._spans(offset, len(data)):
            handle = self._handles[index]
            handle.seek(inner)
            handle.write(view[:take])
            view = view[take:]

    def flush(self) -> None:
        for handle in self._handles:
            handle.flush()
            os.fsync(handle.fileno())

    def close(self) -> None:
        for handle in self._handles:
            handle.close()


def layout_for(total: int) -> list[int]:
    """镜像有 total 字节时每个文件多大，和 hekate 建的一样。"""
    if total <= SINGLE_LIMIT:
        return [total]
    sizes = [SPLIT_BYTES] * (total // SPLIT_BYTES)
    if total % SPLIT_BYTES:
        sizes.append(total % SPLIT_BYTES)
    return sizes


def create(folder: Path, total: int) -> SplitFiles:
    """建好一组新的镜像文件，先把大小定下来，让文件系统给它们分连续的空间。"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for old in gpp_paths(folder):
        old.unlink()
    paths = []
    for name, size in zip(part_names(MAX_PARTS), layout_for(total)):
        path = folder / name
        with open(path, "wb") as handle:
            handle.truncate(size)
        paths.append(path)
    return SplitFiles(paths, writable=True)
