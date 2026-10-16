"""按扇区搬数据：卡到卡、卡到电脑上的文件、文件到卡，都是同一个过程。写完再读一遍核对。"""

from __future__ import annotations

import hashlib
import os

from nxhost.jobs import Cancelled

CHUNK = 8 * 1024 * 1024           # 一次搬 8 MB，是扇区的整数倍


class FileDevice:
    """把一个文件或者一块磁盘当成可以按位置读写的设备。"""

    def __init__(self, path: str, writable: bool = False, size: int | None = None) -> None:
        flags = (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, "O_BINARY", 0)
        self._fd = os.open(path, flags)
        self.size = size if size is not None else os.fstat(self._fd).st_size

    def read_at(self, offset: int, length: int) -> bytes:
        os.lseek(self._fd, offset, os.SEEK_SET)
        parts, left = [], length
        while left:
            data = os.read(self._fd, left)
            if not data:
                break
            parts.append(data)
            left -= len(data)
        return b"".join(parts)

    def write_at(self, offset: int, data: bytes) -> None:
        os.lseek(self._fd, offset, os.SEEK_SET)
        view = memoryview(data)
        while view:
            view = view[os.write(self._fd, view):]

    def flush(self) -> None:
        os.fsync(self._fd)

    def close(self) -> None:
        os.close(self._fd)


def copy(source, source_offset: int, target, target_offset: int, length: int, progress=lambda done: None,
         should_stop=lambda: False, start_at: int = 0) -> str:
    """搬 length 个字节，返回搬过去的内容的 SHA-256。start_at 用来接着上次停下的地方搬（这时不算校验值）。"""
    digest = hashlib.sha256()
    done = start_at
    while done < length:
        if should_stop():
            raise Cancelled
        size = min(CHUNK, length - done)
        data = source.read_at(source_offset + done, size)
        if len(data) != size:
            raise OSError("读到一半读不出来了，卡可能松了或者有坏块")
        target.write_at(target_offset + done, data)
        digest.update(data)
        done += size
        progress(done)
    target.flush()
    return digest.hexdigest()


def checksum(device, offset: int, length: int, progress=lambda done: None, should_stop=lambda: False) -> str:
    digest = hashlib.sha256()
    done = 0
    while done < length:
        if should_stop():
            raise Cancelled
        size = min(CHUNK, length - done)
        data = device.read_at(offset + done, size)
        if len(data) != size:
            raise OSError("核对时读不出来，卡可能松了或者有坏块")
        digest.update(data)
        done += size
        progress(done)
    return digest.hexdigest()
