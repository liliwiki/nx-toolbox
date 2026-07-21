"""SD 卡操作 - 检测、清理、备份、恢复、弹出"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field, asdict

from sd_cleaner.common import (
    EMULATOR_PATHS,
    MOD_PATCH_KEYWORDS,
    THEME_TITLE_IDS,
)

logger = logging.getLogger(__name__)


@dataclass
class PreserveOptions:
    """保留选项，由 UI 层传入。"""
    keep_keys_certs: bool = True
    keep_emu_and_nintendo: bool = True
    keep_switchroot: bool = False
    keep_jksv: bool = False
    keep_emuiibo: bool = False
    keep_cheats_mods: bool = False
    keep_themes: bool = False
    keep_emulators: bool = False
    custom_preserve_items: list[str] | None = None

    def __post_init__(self):
        if self.custom_preserve_items is None:
            self.custom_preserve_items = []


# --- SD 卡检测 ---

def is_switch_sd(path: str) -> bool:
    has_atmosphere = os.path.exists(os.path.join(path, "atmosphere"))
    has_bootloader = os.path.exists(os.path.join(path, "bootloader"))
    has_payload = os.path.exists(os.path.join(path, "payload.bin"))
    if has_atmosphere and has_bootloader and has_payload: return True
    has_emummc = os.path.exists(os.path.join(path, "emuMMC")) or os.path.exists(os.path.join(path, "emummc"))
    has_nintendo = os.path.exists(os.path.join(path, "Nintendo")) or os.path.exists(os.path.join(path, "nintendo"))
    return has_emummc or has_nintendo
# --- 保护列表 ---


def detect_sd_cards() -> list[str]:
    """扫描所有盘符/挂载点，返回检测到的 Switch SD 卡路径列表。"""
    found: list[str] = []
    if sys.platform == "win32":
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            path = f"{letter}:\\"
            if os.path.exists(path):
                try:
                    if is_switch_sd(path):
                        found.append(path)
                except PermissionError:
                    pass
    else:
        for mount in ("/media", "/mnt", "/Volumes"):
            if not os.path.exists(mount):
                continue
            try:
                for item in os.listdir(mount):
                    path = os.path.join(mount, item)
                    if os.path.isdir(path) and is_switch_sd(path):
                        found.append(path)
            except PermissionError:
                pass
    return found


def get_protected_folders(options, sd_path=""):
    protected = []
    if options.keep_emu_and_nintendo:
        protected.extend(["emuMMC", "emummc", "Nintendo", "nintendo"])
    if options.keep_switchroot:
        protected.extend(["switchroot", "Switchroot"])
        if sd_path and os.path.exists(sd_path):
            try:
                for item in os.listdir(sd_path):
                    item_lower = item.lower()
                    if item_lower.startswith("l4t-") and item not in protected:
                        protected.append(item)
                    if item_lower in ("lakka", "ubuntu", "android", "lineage") and item not in protected:
                        protected.append(item)
            except OSError:
                pass
    if options.keep_jksv:
        protected.extend(["JKSV", "jksv"])
    if options.keep_emuiibo:
        protected.append("emuiibo")
    return protected

def get_protected_switch_items(options):
    protected = []
    if options.keep_keys_certs:
        protected.append("prod.keys")
    if options.keep_emulators:
        existing_lower = {p.lower() for p in protected}
        for emu_info in EMULATOR_PATHS.values():
            for item in emu_info.get("switch_paths", []):
                if item and item.lower() not in existing_lower:
                    protected.append(item)
                    existing_lower.add(item.lower())
    return protected

def get_items_to_delete(sd_path, options):
    if not os.path.exists(sd_path):
        return [], []
    protected = get_protected_folders(options, sd_path)
    protected_lower = {p.lower() for p in protected}
    to_delete_files = []
    to_delete_folders = []
    for item in os.listdir(sd_path):
        if item.lower() in protected_lower:
            continue
        item_path = os.path.join(sd_path, item)
        if os.path.isdir(item_path):
            to_delete_folders.append(item)
        else:
            to_delete_files.append(item)
    return to_delete_files, to_delete_folders

def _rmtree_force(path):
    def _on_rmtree_error(func, p, exc_info):
        try:
            os.chmod(p, 0o777)
            func(p)
        except OSError as e2:
            logger.warning("强制删除 %s 失败: %s", p, e2)
    shutil.rmtree(path, onerror=_on_rmtree_error)

def _remove_force(path):
    try:
        os.chmod(path, 0o777)
        os.remove(path)
    except OSError as e:
        logger.warning("删除 %s 失败: %s", path, e)

def delete_switch_folder_selectively(switch_path, protected_items_lower):
    if not os.path.exists(switch_path):
        return
    for item in os.listdir(switch_path):
        if item.lower() in protected_items_lower:
            continue
        item_path = os.path.join(switch_path, item)
        try:
            if os.path.isdir(item_path):
                _rmtree_force(item_path)
            else:
                _remove_force(item_path)
        except (PermissionError, OSError) as e:
            logger.warning("删除 %s 失败: %s", item_path, e)

def calculate_delete_size(sd_path, files, folders):
    total_size = 0
    for folder in folders:
        folder_path = os.path.join(sd_path, folder)
        if not os.path.exists(folder_path):
            continue
        try:
            for dirpath, _, filenames in os.walk(folder_path):
                for filename in filenames:
                    try:
                        total_size += os.path.getsize(os.path.join(dirpath, filename))
                    except OSError:
                        pass
        except PermissionError:
            pass
    for file in files:
        file_path = os.path.join(sd_path, file)
        try:
            total_size += os.path.getsize(file_path)
        except OSError:
            pass
    return total_size

def _normalize_path(path):
    return path.replace("\\", "/").lower().strip("/")

def is_path_protected(rel_path, whitelist):
    norm = _normalize_path(rel_path)
    if norm in whitelist:
        return True
    parts = norm.split("/")
    for i in range(1, len(parts)):
        if "/".join(parts[:i]) in whitelist:
            return True
    return False

def _folder_has_whitelist_entries(folder_name, whitelist):
    norm = _normalize_path(folder_name)
    prefix = norm + "/"
    return any(wl.startswith(prefix) for wl in whitelist)

def delete_folder_with_whitelist(folder_path, whitelist, folder_rel):
    if not os.path.isdir(folder_path):
        return
    for item in os.listdir(folder_path):
        item_abs = os.path.join(folder_path, item)
        item_rel = f"{folder_rel}/{item}" if folder_rel else item
        if is_path_protected(item_rel, whitelist):
            continue
        try:
            if os.path.isdir(item_abs):
                _rmtree_force(item_abs)
            else:
                _remove_force(item_abs)
        except (PermissionError, OSError) as e:
            logger.warning("删除 %s 失败: %s", item_abs, e)

def build_whitelist_paths(options, sd_path):
    whitelist = set()
    root_protected = get_protected_folders(options, sd_path)
    for folder in root_protected:
        whitelist.add(_normalize_path(folder))
    contents_path = os.path.join(sd_path, "atmosphere", "contents")
    if options.keep_cheats_mods and os.path.isdir(contents_path):
        for title_id in os.listdir(contents_path):
            title_path = os.path.join(contents_path, title_id)
            if not os.path.isdir(title_path):
                continue
            cheats_path = os.path.join(title_path, "cheats")
            if os.path.isdir(cheats_path):
                whitelist.add(_normalize_path(f"atmosphere/contents/{title_id}/cheats"))
            for mod_type in ("romfs", "exefs"):
                mod_path = os.path.join(title_path, mod_type)
                if os.path.isdir(mod_path):
                    whitelist.add(_normalize_path(f"atmosphere/contents/{title_id}/{mod_type}"))
    if options.keep_cheats_mods:
        exefs_patches_path = os.path.join(sd_path, "atmosphere", "exefs_patches")
        if os.path.isdir(exefs_patches_path):
            for patch_folder in os.listdir(exefs_patches_path):
                patch_path = os.path.join(exefs_patches_path, patch_folder)
                if os.path.isdir(patch_path) and any(kw in patch_folder.lower() for kw in MOD_PATCH_KEYWORDS):
                    whitelist.add(_normalize_path(f"atmosphere/exefs_patches/{patch_folder}"))
    if options.keep_themes and os.path.isdir(contents_path):
        for title_id in THEME_TITLE_IDS:
            theme_path = os.path.join(contents_path, title_id)
            if os.path.isdir(theme_path):
                whitelist.add(_normalize_path(f"atmosphere/contents/{title_id}"))
    if options.keep_switchroot:
        ini_path = os.path.join(sd_path, "bootloader", "ini")
        if os.path.isdir(ini_path):
            whitelist.add(_normalize_path("bootloader/ini"))
    if options.keep_emulators:
        switch_base = os.path.join(sd_path, "switch")
        for emu_info in EMULATOR_PATHS.values():
            for p in emu_info.get("root_paths", []):
                if p and os.path.exists(os.path.join(sd_path, p)):
                    whitelist.add(_normalize_path(p))
            if os.path.isdir(switch_base):
                for p in emu_info.get("switch_paths", []):
                    if os.path.exists(os.path.join(switch_base, p)):
                        whitelist.add(_normalize_path(f"switch/{p}"))
    if options.keep_keys_certs:
        keys_file = os.path.join(sd_path, "switch", "prod.keys")
        if os.path.isfile(keys_file):
            whitelist.add(_normalize_path("switch/prod.keys"))
        auto_backups = os.path.join(sd_path, "atmosphere", "automatic_backups")
        if os.path.isdir(auto_backups):
            whitelist.add(_normalize_path("atmosphere/automatic_backups"))
    for item in options.custom_preserve_items or []:
        norm = _normalize_path(item)
        if norm:
            whitelist.add(norm)
    return whitelist

def get_sd_space(sd_path):
    try:
        if os.name == "nt":
            import ctypes
            free_bytes = ctypes.c_ulonglong(0)
            total_bytes = ctypes.c_ulonglong(0)
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p(sd_path), None,
                ctypes.pointer(total_bytes), ctypes.pointer(free_bytes))
            total_gb = total_bytes.value / (1024 ** 3)
            free_gb = free_bytes.value / (1024 ** 3)
            used_gb = total_gb - free_gb
        else:
            total, used, free = shutil.disk_usage(sd_path)
            total_gb = total / (1024 ** 3)
            free_gb = free / (1024 ** 3)
            used_gb = used / (1024 ** 3)
        return (total_gb, used_gb, free_gb)
    except OSError as e:
        logger.warning("无法获取 SD 卡空间: %s", e)
        return None

def eject_sd_card(sd_path):
    drive_letter = os.path.splitdrive(sd_path)[0]
    if not drive_letter:
        return False, "无法识别SD卡盘符"
    if not drive_letter.endswith(":"):
        drive_letter += ":"
    try:
        import ctypes
        from ctypes import wintypes
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        FILE_SHARE_READ = 0x1
        FILE_SHARE_WRITE = 0x2
        OPEN_EXISTING = 3
        IOCTL_STORAGE_EJECT_MEDIA = 0x2D4808
        FSCTL_LOCK_VOLUME = 0x00090018
        FSCTL_DISMOUNT_VOLUME = 0x00090020
        kernel32 = ctypes.windll.kernel32
        volume_path = f"\\\\\\.\\{drive_letter}"
        handle = kernel32.CreateFileW(volume_path, GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
        if handle == -1:
            handle = kernel32.CreateFileW(volume_path, GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
        if handle == -1:
            return False, f"无法打开驱动器 {drive_letter}，请确保没有程序正在使用该驱动器"
        try:
            bytes_returned = wintypes.DWORD()
            kernel32.DeviceIoControl(handle, FSCTL_LOCK_VOLUME, None, 0, None, 0,
                ctypes.byref(bytes_returned), None)
            kernel32.DeviceIoControl(handle, FSCTL_DISMOUNT_VOLUME, None, 0, None, 0,
                ctypes.byref(bytes_returned), None)
            result = kernel32.DeviceIoControl(handle, IOCTL_STORAGE_EJECT_MEDIA,
                None, 0, None, 0, ctypes.byref(bytes_returned), None)
            if result:
                return True, f"SD卡 ({drive_letter}) 已安全弹出"
            error_code = kernel32.GetLastError()
            return False, f"弹出失败，错误代码: {error_code}"
        finally:
            kernel32.CloseHandle(handle)
    except ImportError:
        return False, "此功能仅支持 Windows"
    except OSError as e:
        return False, f"弹出 SD 卡时出错: {e}"

def get_sd_state_snapshot(sd_path):
    if not sd_path or not os.path.exists(sd_path):
        return None
    try:
        items = []
        for item in os.listdir(sd_path):
            item_path = os.path.join(sd_path, item)
            try:
                stat = os.stat(item_path)
                size = stat.st_size if os.path.isfile(item_path) else 0
                items.append((item, stat.st_mtime, size))
            except OSError:
                items.append((item, 0, 0))
        return tuple(sorted(items))
    except OSError:
        return None
