"""通用模块 — 常量、配置、工具函数"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# ============================================================================
# 常量
# ============================================================================

VERSION = "1.0.0"

# 网络资源
DEFAULT_PACKAGE_URL = "https://download.liliwiki.com/backup/xlcj/atmosphere.zip"
ONLINE_FIRMWARE_URL = "https://download.liliwiki.com/firmware/NS/"
UPDATE_MANIFEST_URL = "https://download.liliwiki.com/toolbox/version.json"

# 本地路径（支持 PyInstaller 打包）
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = sys._MEIPASS  # type: ignore[attr-defined]
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    BUNDLE_DIR = BASE_DIR

_APPDATA = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
APPDATA_DIR = os.path.join(_APPDATA, "NXToolbox")
CONFIG_FILE = os.path.join(APPDATA_DIR, "sd_cleaner_config.json")
FIRMWARE_DIR = os.path.join(APPDATA_DIR, "固件")

# 支持的文件格式
FIRMWARE_EXTENSIONS = (".zip", ".7z", ".rar")

# MOD 补丁关键词
MOD_PATCH_KEYWORDS = [
    "60fps", "60FPS", "graphics", "Graphics", "GRAPHICS",
    "fps", "FPS", "mod", "MOD", "Mod", "120fps", "120FPS",
]

# 主题 TitleID 配置
THEME_TITLE_IDS = [
    "0100000000001000",
    "0100000000001007",
    "0100000000001013",
]

# 模拟器路径配置
EMULATOR_PATHS: dict[str, dict] = {
    "ONS": {"name": "ONScripter", "root_paths": ["onsemu"], "switch_paths": ["onsyuri.nro", "onsyuri"]},
    "Tiicu": {"name": "Tiicu", "root_paths": ["tiicu"], "switch_paths": ["tiicu.nro", "tiicu"]},
    "PPSSPP": {"name": "PPSSPP", "root_paths": [], "switch_paths": ["ppsspp", "ppsspp.nro"]},
    "RetroArch": {"name": "RetroArch", "root_paths": ["retroarch"], "switch_paths": ["retroarch", "retroarch_switch.nro", "retroarch_switch", "retroarch.nro"]},
    "ROM": {"name": "ROM游戏", "root_paths": ["rom", "ROM", "roms", "ROMS"], "switch_paths": []},
}

# ============================================================================
# 配置
# ============================================================================

@dataclass
class AppConfig:
    """应用配置数据"""
    package_url: str = DEFAULT_PACKAGE_URL
    sd_path: str = ""
    keep_keys_certs: bool = True
    keep_emu_and_nintendo: bool = True
    keep_switchroot: bool = False
    keep_jksv: bool = False
    keep_emuiibo: bool = False
    keep_cheats_mods: bool = False
    keep_themes: bool = False
    keep_emulators: bool = False
    custom_preserve_items: list[str] = field(default_factory=list)
    is_dark_theme: bool = False
    auto_snapshot: bool = True


def load_config(path: str = CONFIG_FILE) -> AppConfig:
    """从 JSON 文件加载配置。"""
    config = AppConfig()
    if not os.path.exists(path):
        return config
    try:
        with open(path, "r", encoding="utf-8") as f:
            data: dict = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("加载配置失败: %s", e)
        return config
    for key in ("package_url", "sd_path", "keep_keys_certs", "keep_emu_and_nintendo",
                "keep_switchroot", "keep_jksv", "keep_emuiibo", "keep_cheats_mods",
                "keep_themes", "keep_emulators", "custom_preserve_items",
                "is_dark_theme", "auto_snapshot"):
        if key in data:
            setattr(config, key, data[key])
    if "keep_cheats_mods" not in data:
        if data.get("keep_cheats") or data.get("keep_mods"):
            config.keep_cheats_mods = True
    return config


def save_config(config: AppConfig, path: str = CONFIG_FILE) -> None:
    """将 AppConfig 保存为 JSON 文件。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(config), f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.error("保存配置失败: %s", e)

# ============================================================================
# 工具函数
# ============================================================================

def get_7z_path() -> str:
    """获取内嵌 7z.exe 路径，支持 PyInstaller 打包。"""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "7z", "7z.exe")
    return os.path.join(BASE_DIR, "7z", "7z.exe")


def format_size(size_bytes: int) -> str:
    """格式化文件大小为可读字符串。"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    return f"{size_bytes / 1024 ** 3:.2f} GB"


def extract_version_number(name: str) -> tuple[int, int, int]:
    """从固件名称中提取版本号元组，用于排序。"""
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", name)
    if match:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return (9999, 0, 0)


def sort_firmware_names(names: list[str]) -> list[str]:
    """按版本号从小到大排序固件名称。"""
    return sorted(names, key=extract_version_number)
