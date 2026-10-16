"""Switch 系统分区用的加密：AES-128-XTS，每 0x4000 字节一个单元。

和标准 XTS 只有一处不同：单元的序号按大端放进 16 个字节（见 hekate 的 se_aes_crypt_xts_sec_nx）。
AES 本身用 Windows 自带的加密库算；XTS 那层的异或在这里做，用大整数一次处理一整个单元。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

UNIT = 0x4000
BLOCK = 16
_MASK = (1 << 128) - 1
_bcrypt = ctypes.WinDLL("bcrypt")
for _name in ("BCryptOpenAlgorithmProvider", "BCryptSetProperty", "BCryptGenerateSymmetricKey", "BCryptEncrypt", "BCryptDecrypt"):
    getattr(_bcrypt, _name).restype = ctypes.c_ulong
_bcrypt.BCryptEncrypt.argtypes = _bcrypt.BCryptDecrypt.argtypes = [
    wintypes.HANDLE, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
    ctypes.c_char_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]


class CryptoError(OSError):
    pass


class _Ecb:
    """AES-ECB，不填充。数据长度必须是 16 的整数倍。"""

    def __init__(self, key: bytes) -> None:
        algorithm, self._key = wintypes.HANDLE(), wintypes.HANDLE()
        mode = ctypes.create_unicode_buffer("ChainingModeECB")
        if (_bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(algorithm), "AES", None, 0)
                or _bcrypt.BCryptSetProperty(algorithm, "ChainingMode", mode, ctypes.sizeof(mode), 0)
                or _bcrypt.BCryptGenerateSymmetricKey(algorithm, ctypes.byref(self._key), None, 0, key, len(key), 0)):
            raise CryptoError("Windows 的加密组件用不了")

    def _run(self, function, data: bytes) -> bytes:
        out = ctypes.create_string_buffer(len(data))
        done = ctypes.c_ulong(0)
        if function(self._key, data, len(data), None, None, 0, out, len(data), ctypes.byref(done), 0) or done.value != len(data):
            raise CryptoError("加解密失败")
        return out.raw

    def encrypt(self, data: bytes) -> bytes:
        return self._run(_bcrypt.BCryptEncrypt, data)

    def decrypt(self, data: bytes) -> bytes:
        return self._run(_bcrypt.BCryptDecrypt, data)


def _double(tweak: int) -> int:
    """在 GF(2^128) 里乘 x，小端。"""
    return ((tweak << 1) & _MASK) ^ (0x87 if tweak >> 127 else 0)


class Xts:
    def __init__(self, crypt_key: bytes, tweak_key: bytes, unit: int = UNIT, big_endian_index: bool = True) -> None:
        self.unit = unit
        self._data, self._tweak = _Ecb(crypt_key), _Ecb(tweak_key)
        self._order = "big" if big_endian_index else "little"
        # 一个单元里每个 16 字节块的调整值连起来，是起始调整值的线性函数。
        # 先把起始值每一位单独为 1 时的整串算出来，用的时候按位异或起来就行。
        self._basis = []
        for bit in range(128):
            tweak, stream = 1 << bit, 0
            for block in range(unit // BLOCK):
                stream |= tweak << (128 * block)
                tweak = _double(tweak)
            self._basis.append(stream)

    def _stream(self, first: int, count: int) -> int:
        """从第 first 个单元起连续 count 个单元的调整值，拼成一个大整数。"""
        seeds = self._tweak.encrypt(b"".join(index.to_bytes(BLOCK, self._order) for index in range(first, first + count)))
        basis, parts = self._basis, []
        for unit in range(count):
            seed, stream = int.from_bytes(seeds[unit * BLOCK:(unit + 1) * BLOCK], "little"), 0
            while seed:
                low = seed & -seed
                stream ^= basis[low.bit_length() - 1]
                seed ^= low
            parts.append(stream.to_bytes(self.unit, "little"))
        return int.from_bytes(b"".join(parts), "little")

    def _run(self, function, first: int, data: bytes) -> bytes:
        if len(data) % self.unit:
            raise ValueError("长度必须是加密单元的整数倍")
        stream = self._stream(first, len(data) // self.unit)
        mixed = (int.from_bytes(data, "little") ^ stream).to_bytes(len(data), "little")
        return (int.from_bytes(function(mixed), "little") ^ stream).to_bytes(len(data), "little")

    def encrypt(self, first: int, data: bytes) -> bytes:
        return self._run(self._data.encrypt, first, data)

    def decrypt(self, first: int, data: bytes) -> bytes:
        return self._run(self._data.decrypt, first, data)
