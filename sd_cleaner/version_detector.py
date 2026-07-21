"""版本检测 - 从 SD 卡解析 Atmosphere 和固件版本信息"""

from __future__ import annotations

import logging
import os
import struct
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_PACKAGE3_MAGIC = b"PK31"
_PACKAGE3_LEGACY_MAGIC = b"FSS0"


@dataclass
class AtmosphereVersion:
    """Atmosphere 版本信息。"""

    major: int
    minor: int
    micro: int
    git_hash: str

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.micro}"

    @property
    def full(self) -> str:
        return f"{self.major}.{self.minor}.{self.micro}-{self.git_hash}"

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.micro)


@dataclass
class HOSVersion:
    """Switch 系统固件 (HOS) 版本信息。"""

    major: int
    minor: int
    micro: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.micro}"

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.micro)


@dataclass
class SDVersionInfo:
    """SD 卡上检测到的完整版本信息。"""

    atmosphere: AtmosphereVersion | None
    supported_hos: HOSVersion | None
    package3_exists: bool


def detect_versions(sd_path: str) -> SDVersionInfo:
    """
    从 SD 卡检测 Atmosphere 版本和支持的最高固件版本。

    通过解析 atmosphere/package3 文件头部获取：
    - 偏移 0x38: release_version (4 bytes, BBBB: relstep, micro, minor, major)
    - 偏移 0x34: supported_hos_version (同格式)
    - 偏移 0x3C: git_revision (uint32 LE, git hash 前 8 位)
    """
    pkg_path = os.path.join(sd_path, "atmosphere", "package3")

    if not os.path.exists(pkg_path):
        return SDVersionInfo(atmosphere=None, supported_hos=None, package3_exists=False)

    try:
        with open(pkg_path, "rb") as f:
            header = f.read(0x40)
    except OSError as e:
        logger.warning("读取 package3 失败: %s", e)
        return SDVersionInfo(atmosphere=None, supported_hos=None, package3_exists=True)

    if len(header) < 0x40:
        logger.warning("package3 文件过小: %d 字节", len(header))
        return SDVersionInfo(atmosphere=None, supported_hos=None, package3_exists=True)

    magic = header[:4]
    if magic != _PACKAGE3_MAGIC:
        legacy = header[0x20:0x24]
        if legacy != _PACKAGE3_LEGACY_MAGIC and magic != _PACKAGE3_LEGACY_MAGIC:
            logger.warning("package3 magic 不匹配: %s", magic)
            return SDVersionInfo(atmosphere=None, supported_hos=None, package3_exists=True)

    # release_version at 0x38: [relstep, micro, minor, major]
    rel_step, rel_micro, rel_minor, rel_major = struct.unpack_from("<BBBB", header, 0x38)

    # supported_hos_version at 0x34: [relstep, micro, minor, major]
    hos_step, hos_micro, hos_minor, hos_major = struct.unpack_from("<BBBB", header, 0x34)

    # git_revision at 0x3C: uint32 LE
    git_rev = struct.unpack_from("<I", header, 0x3C)[0]
    git_hash = format(git_rev, "08x")

    atmos = AtmosphereVersion(
        major=rel_major,
        minor=rel_minor,
        micro=rel_micro,
        git_hash=git_hash,
    )

    hos = HOSVersion(
        major=hos_major,
        minor=hos_minor,
        micro=hos_micro,
    )

    logger.info("检测到 Atmosphere %s, 支持 HOS %s", atmos, hos)
    return SDVersionInfo(atmosphere=atmos, supported_hos=hos, package3_exists=True)


def check_firmware_compatibility(
    sd_path: str, firmware_version: str
) -> tuple[bool, str]:
    """
    检查要安装的固件是否与当前 Atmosphere 版本兼容。

    Args:
        sd_path: SD 卡根目录
        firmware_version: 固件版本字符串，如 "19.0.0" 或 "21.2.0"

    Returns:
        (is_compatible, message)
    """
    info = detect_versions(sd_path)

    if not info.package3_exists:
        return True, "未检测到 Atmosphere，跳过兼容性检查"

    if info.atmosphere is None or info.supported_hos is None:
        return True, "无法解析 Atmosphere 版本，跳过兼容性检查"

    # 解析目标固件版本
    fw_tuple = _parse_version_string(firmware_version)
    if fw_tuple is None:
        return True, f"无法解析固件版本号: {firmware_version}"

    supported = info.supported_hos.as_tuple()

    if fw_tuple <= supported:
        return True, (
            f"✅ 兼容：固件 {firmware_version} ≤ "
            f"Atmosphere {info.atmosphere} 支持的最高版本 {info.supported_hos}"
        )

    return False, (
        f"⚠️ 可能不兼容：固件 {firmware_version} > "
        f"Atmosphere {info.atmosphere} 支持的最高版本 {info.supported_hos}\n"
        f"建议先更新 Atmosphere 整合包再安装此固件"
    )


def _parse_version_string(version: str) -> tuple[int, int, int] | None:
    """从版本字符串解析 (major, minor, micro) 元组。"""
    import re

    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
    if match:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return None
