"""压缩包处理 - 类型检测、解压、文件树复制"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import zipfile
from typing import Callable

from sd_cleaner.common import get_7z_path

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], None]  # (percent, message)


def detect_archive_type(file_path: str, file_name_lower: str) -> str | None:
    """
    检测压缩包类型。

    Returns:
        'firmware' | 'package' | None
    """
    firmware_keywords = ["firmware", "fw-", "fw_", "固件"]
    if any(kw in file_name_lower for kw in firmware_keywords):
        return "firmware"

    package_keywords = ["atmosphere", "ams-", "ams_", "hekate", "整合包", "pack", "cfw"]
    if any(kw in file_name_lower for kw in package_keywords):
        return "package"

    return _detect_type_by_content(file_path)


def _detect_type_by_content(file_path: str) -> str | None:
    """通过压缩包内容判断类型。"""
    try:
        file_lower = file_path.lower()

        if file_lower.endswith(".zip"):
            with zipfile.ZipFile(file_path, "r") as zf:
                namelist = [n.lower() for n in zf.namelist()]
                return _analyze_archive_content(namelist)

        if file_lower.endswith((".7z", ".rar")):
            sz_path = get_7z_path()
            if not os.path.exists(sz_path):
                return None

            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            result = subprocess.run(
                [sz_path, "l", "-slt", file_path],
                capture_output=True, text=True,
                timeout=60,
                creationflags=creationflags,
            )
            if result.returncode == 0:
                namelist = [
                    line[7:].strip().lower()
                    for line in result.stdout.split("\n")
                    if line.startswith("Path = ")
                ]
                return _analyze_archive_content(namelist)
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        logger.debug("检测压缩包内容失败: %s", e)
    return None


def _analyze_archive_content(namelist: list[str]) -> str | None:
    """分析压缩包内容列表判断类型。"""
    normalized = [n.replace("\\", "/").lower().strip("/") for n in namelist]

    has_nca = any(n.endswith(".nca") for n in normalized)
    has_atmosphere = any(
        "/atmosphere/" in f"/{n}/" or n == "atmosphere" or n.startswith("atmosphere/")
        for n in normalized
    )
    has_bootloader = any(
        "/bootloader/" in f"/{n}/" or n == "bootloader" or n.startswith("bootloader/")
        for n in normalized
    )
    has_hekate_ini = any(
        n.endswith("hekate_ipl.ini") or "/hekate_ipl.ini" in n
        for n in normalized
    )

    if has_nca:
        nca_count = sum(1 for n in normalized if n.endswith(".nca"))
        if nca_count > 10:
            return "firmware"

    if has_atmosphere and has_bootloader and has_hekate_ini:
        return "package"

    return None


def extract_archive(file_path: str, dest_dir: str) -> None:
    """
    解压压缩包到目标目录，支持 zip/7z/rar。

    Raises:
        zipfile.BadZipFile: zip 文件损坏
        RuntimeError: 解压失败
        FileNotFoundError: 7z.exe 不存在
    """
    file_lower = file_path.lower()

    if file_lower.endswith(".zip"):
        with zipfile.ZipFile(file_path, "r") as zip_ref:
            zip_ref.extractall(dest_dir)
        return

    if file_lower.endswith((".7z", ".rar")):
        sz_path = get_7z_path()
        if not os.path.exists(sz_path):
            raise FileNotFoundError("内嵌的 7z.exe 不存在，无法解压 7z/rar 格式文件")

        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        result = subprocess.run(
            [sz_path, "x", file_path, f"-o{dest_dir}", "-y"],
            capture_output=True, text=True,
            creationflags=creationflags,
        )
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "解压失败"
            raise RuntimeError(f"解压失败: {error_msg}")
        return

    raise RuntimeError(f"不支持的文件格式: {os.path.splitext(file_path)[1]}")


def _is_excluded(abs_path: str, src_root: str, exclude_set: set[str]) -> bool:
    """检查绝对路径是否在排除列表中（包括其父级路径）。"""
    rel = os.path.relpath(abs_path, src_root).replace("\\", "/").lower().strip("/")
    if rel in exclude_set:
        return True
    parts = rel.split("/")
    for i in range(1, len(parts)):
        if "/".join(parts[:i]) in exclude_set:
            return True
    return False


def copy_tree(
    src: str,
    dst: str,
    progress_callback: ProgressCallback | None = None,
    exclude_set: set[str] | None = None,
) -> None:
    """复制目录树，覆盖已存在的文件。exclude_set 中的路径（及子级）将被跳过。"""
    if exclude_set is None:
        exclude_set = set()

    if progress_callback is not None:
        # 预计算总文件数（排除要跳过的文件）
        total_files = 0
        for root, _, files in os.walk(src):
            for f in files:
                fpath = os.path.join(root, f)
                if not _is_excluded(fpath, src, exclude_set):
                    total_files += 1
        current_file = 0

        def _copy_with_progress(s: str, d: str) -> None:
            nonlocal current_file
            for item in os.listdir(s):
                src_item = os.path.join(s, item)
                dst_item = os.path.join(d, item)
                if _is_excluded(src_item, src, exclude_set):
                    continue
                if os.path.isdir(src_item):
                    if os.path.exists(dst_item):
                        _copy_with_progress(src_item, dst_item)
                    else:
                        dir_files = sum(
                            1 for r, _, fs in os.walk(src_item)
                            for f in fs
                            if not _is_excluded(os.path.join(r, f), src, exclude_set)
                        )
                        shutil.copytree(src_item, dst_item)
                        current_file += dir_files
                        pct = int(current_file * 100 / total_files) if total_files else 0
                        progress_callback(pct, item)
                else:
                    os.makedirs(os.path.dirname(dst_item), exist_ok=True)
                    shutil.copy2(src_item, dst_item)
                    current_file += 1
                    pct = int(current_file * 100 / total_files) if total_files else 0
                    progress_callback(pct, item)

        _copy_with_progress(src, dst)
    else:
        _copy_simple(src, dst, exclude_set, src)


def _copy_simple(
    src: str, dst: str, exclude_set: set[str], src_root: str
) -> None:
    """无进度的简单递归复制，支持排除列表。"""
    for item in os.listdir(src):
        s = os.path.join(src, item)
        if _is_excluded(s, src_root, exclude_set):
            continue
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            if os.path.exists(d):
                _copy_simple(s, d, exclude_set, src_root)
            else:
                shutil.copytree(s, d)
        else:
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
