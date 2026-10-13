"""整合包：不下载就列出里面的文件、下载、解压。

在线的 zip 用 HTTP Range 只取末尾的目录区，几十 KB 就能知道包里有什么，
这样点"更新"之前就能算出会改动哪些文件。
"""

from __future__ import annotations

import io
import os
import shutil
import struct
import subprocess
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

BIN_7Z = Path(__file__).resolve().parent.parent / "bin" / "7z" / "7z.exe"
USER_AGENT = "NXToolbox/2.0"
RANGE_BLOCK = 256 * 1024
DOWNLOAD_BLOCK = 256 * 1024
ROOT_DIRS = ("atmosphere", "bootloader")
ROOT_FILE = "payload.bin"
FIRMWARE_MIN_NCA = 10
ARCHIVE_EXTS = (".zip", ".7z", ".rar")


class Cancelled(Exception):
    pass


@dataclass
class Listing:
    kind: str                      # online / local
    location: str                  # 网址或本地路径
    files: list[str] = field(default_factory=list)   # 相对整合包根目录
    prefix: str = ""               # 压缩包里根目录前面多套的那层文件夹
    size: int = 0                  # 压缩包大小
    ams: str | None = None
    hos: str | None = None
    lists: dict = field(default_factory=lambda: {"white": [], "black": []})

    @property
    def name(self) -> str:
        return "在线最新" if self.kind == "online" else os.path.basename(self.location)


class HttpRangeFile(io.RawIOBase):
    """把一个支持 Range 的网址当成只读文件，zipfile 只会读它需要的那几段。"""

    def __init__(self, url: str, timeout: float = 20) -> None:
        self._url, self._timeout, self._pos, self._blocks = url, timeout, 0, {}
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            self.size = int(resp.headers.get("Content-Length") or 0)
            if resp.headers.get("Accept-Ranges", "").lower() != "bytes" or not self.size:
                raise OSError("服务器不支持分段读取")

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self._pos = offset if whence == io.SEEK_SET else (self._pos + offset if whence == io.SEEK_CUR else self.size + offset)
        return self._pos

    def _block(self, index: int) -> bytes:
        if index not in self._blocks:
            start = index * RANGE_BLOCK
            end = min(start + RANGE_BLOCK, self.size) - 1
            req = urllib.request.Request(self._url, headers={"User-Agent": USER_AGENT, "Range": f"bytes={start}-{end}"})
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                self._blocks[index] = resp.read()
        return self._blocks[index]

    def read(self, n: int = -1) -> bytes:
        end = self.size if n is None or n < 0 else min(self._pos + n, self.size)
        chunks = []
        while self._pos < end:
            index, offset = divmod(self._pos, RANGE_BLOCK)
            piece = self._block(index)[offset:offset + end - self._pos]
            if not piece:
                break
            chunks.append(piece)
            self._pos += len(piece)
        return b"".join(chunks)


def _find_prefix(names: list[str]) -> str | None:
    """整合包根目录 = 最浅的那个直接包含 atmosphere / bootloader / payload.bin 的文件夹。"""
    best = None
    for name in names:
        parts = name.split("/")
        for index, part in enumerate(parts):
            is_file = index == len(parts) - 1
            if (part.lower() in ROOT_DIRS and not is_file) or (part.lower() == ROOT_FILE and is_file):
                prefix = "".join(p + "/" for p in parts[:index])
                if best is None or len(prefix) < len(best):
                    best = prefix
                break
    return best


def _version_from_package3(header: bytes) -> tuple[str | None, str | None]:
    if len(header) < 0x40 or header[:4] != b"PK31":
        return None, None
    _, hos_micro, hos_minor, hos_major = struct.unpack_from("<BBBB", header, 0x34)
    _, micro, minor, major = struct.unpack_from("<BBBB", header, 0x38)
    return f"{major}.{minor}.{micro}", f"{hos_major}.{hos_minor}.{hos_micro}"


def _listing_from_zip(archive: zipfile.ZipFile, listing: Listing) -> Listing:
    names = [i.filename for i in archive.infolist() if not i.is_dir()]
    prefix = _find_prefix(names)
    if prefix is None:
        raise ValueError("这不是整合包：里面没有 atmosphere、bootloader 或 payload.bin")
    listing.prefix = prefix
    listing.files = [n[len(prefix):] for n in names if n.startswith(prefix)]
    by_lower = {f.lower(): f for f in listing.files}

    def read(rel: str, limit: int = -1) -> bytes:
        actual = by_lower.get(rel)
        if actual is None:
            return b""
        with archive.open(prefix + actual) as member:
            return member.read(limit)

    listing.ams, listing.hos = _version_from_package3(read("atmosphere/package3", 0x40))
    for key in ("white", "black"):
        for rel in (f"atmosphere/{key}list.txt", f"{key}list.txt"):
            text = read(rel).decode("utf-8-sig", "replace")
            listing.lists[key] += [line.strip().strip("/\\") for line in text.splitlines() if line.strip()]
    return listing


def list_online(url: str) -> Listing:
    remote = HttpRangeFile(url)
    with zipfile.ZipFile(remote) as archive:
        return _listing_from_zip(archive, Listing("online", url, size=remote.size))


def _list_7z(path: str) -> list[str]:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run([str(BIN_7Z), "l", "-slt", "-ba", path], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=120, creationflags=flags)
    if result.returncode != 0:
        raise ValueError("读不了这个压缩包")
    names, current = [], None
    for line in result.stdout.splitlines():
        if line.startswith("Path = "):
            current = line[7:].replace("\\", "/")
        elif line.startswith("Folder = ") and current is not None:
            if line.endswith("-"):
                names.append(current)
            current = None
    return names


def classify(path: str) -> str:
    """本地压缩包是整合包还是固件。返回 package / firmware / unknown。"""
    try:
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as archive:
                names = [i.filename for i in archive.infolist() if not i.is_dir()]
        else:
            names = _list_7z(path)
    except (OSError, ValueError, zipfile.BadZipFile):
        return "unknown"
    if sum(n.lower().endswith(".nca") for n in names) > FIRMWARE_MIN_NCA:
        return "firmware"
    return "package" if _find_prefix(names) is not None else "unknown"


def list_local(path: str) -> Listing:
    listing = Listing("local", path, size=os.path.getsize(path))
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            return _listing_from_zip(archive, listing)
    names = _list_7z(path)
    prefix = _find_prefix(names)
    if prefix is None:
        raise ValueError("这不是整合包：里面没有 atmosphere、bootloader 或 payload.bin")
    listing.prefix, listing.files = prefix, [n[len(prefix):] for n in names if n.startswith(prefix)]
    return listing


def download(url: str, dest: str, progress, check) -> None:
    """progress(已下载字节, 总字节)；check() 在需要取消时抛 Cancelled。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            check()
            block = resp.read(DOWNLOAD_BLOCK)
            if not block:
                break
            out.write(block)
            done += len(block)
            progress(done, total)


def extract(archive_path: str, dest: str, progress, check) -> None:
    """progress(已解压个数, 总个数, 当前文件)。"""
    if archive_path.lower().endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive:
            members = [i for i in archive.infolist() if not i.is_dir()]
            for index, member in enumerate(members, 1):
                check()
                archive.extract(member, dest)
                progress(index, len(members), member.filename)
        return
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    progress(0, 1, os.path.basename(archive_path))
    result = subprocess.run([str(BIN_7Z), "x", archive_path, f"-o{dest}", "-y"], capture_output=True, text=True,
                            errors="replace", creationflags=flags)
    if result.returncode != 0:
        raise OSError(f"解压失败：{(result.stderr or result.stdout).strip()[:200]}")
    progress(1, 1, "")


def make_work_dir() -> str:
    return tempfile.mkdtemp(prefix="nxtoolbox_")


def remove_work_dir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)
