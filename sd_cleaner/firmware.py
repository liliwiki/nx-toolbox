"""固件管理 - 本地/在线固件列表、安装"""

from __future__ import annotations

import logging
import os
import re
import urllib.request

from sd_cleaner.archive import copy_tree
from sd_cleaner.common import FIRMWARE_DIR, FIRMWARE_EXTENSIONS, ONLINE_FIRMWARE_URL

logger = logging.getLogger(__name__)


def get_local_firmware() -> dict[str, str]:
    """
    获取本地固件列表。

    Returns:
        {显示名称: 完整路径}
    """
    firmware_dict: dict[str, str] = {}
    os.makedirs(FIRMWARE_DIR, exist_ok=True)

    try:
        for item in os.listdir(FIRMWARE_DIR):
            item_path = os.path.join(FIRMWARE_DIR, item)
            if os.path.isfile(item_path) and item.lower().endswith(FIRMWARE_EXTENSIONS):
                firmware_dict[f"[本地] {item}"] = item_path
            elif os.path.isdir(item_path):
                for sub_item in os.listdir(item_path):
                    if sub_item.lower().endswith(FIRMWARE_EXTENSIONS):
                        firmware_dict[f"[本地] {item}/{sub_item}"] = os.path.join(
                            item_path, sub_item
                        )
    except OSError as e:
        logger.warning("读取本地固件列表失败: %s", e)

    return firmware_dict


def get_online_firmware_list() -> dict[str, str]:
    """
    从在线目录获取固件列表。

    Returns:
        {显示名称: 下载URL}
    """
    firmware_dict: dict[str, str] = {}

    try:
        req = urllib.request.Request(
            ONLINE_FIRMWARE_URL,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8")

        pattern = r'href="([^"]+\.(?:zip|7z|rar))"'
        matches = re.findall(pattern, html, re.IGNORECASE)

        for href in matches:
            if href.startswith(".."):
                continue
            if href.startswith("/"):
                fname = href.rsplit("/", 1)[-1]
                if not fname:
                    continue
                download_url = (
                    "https://download.liliwiki.com"
                    + urllib.request.quote(urllib.request.unquote(href), safe="/:")
                )
            else:
                fname = href
                download_url = ONLINE_FIRMWARE_URL + fname

            display_name = urllib.request.unquote(fname)
            firmware_dict[f"[在线] {display_name}"] = download_url

    except (urllib.error.URLError, OSError) as e:
        logger.warning("获取在线固件列表失败: %s", e)

    return firmware_dict


def find_firmware_source(extract_dir: str) -> str | None:
    """
    在解压目录中查找固件文件的源目录。

    固件通常包含大量 .nca 文件。
    """
    if _has_nca_files(extract_dir):
        return extract_dir

    firmware_path = os.path.join(extract_dir, "firmware")
    if os.path.isdir(firmware_path):
        return firmware_path

    try:
        for item in os.listdir(extract_dir):
            item_path = os.path.join(extract_dir, item)
            if not os.path.isdir(item_path):
                continue
            if _has_nca_files(item_path):
                return item_path
            sub_firmware = os.path.join(item_path, "firmware")
            if os.path.isdir(sub_firmware):
                return sub_firmware
    except OSError:
        pass

    # 二级子目录
    try:
        for item in os.listdir(extract_dir):
            item_path = os.path.join(extract_dir, item)
            if not os.path.isdir(item_path):
                continue
            for sub_item in os.listdir(item_path):
                sub_path = os.path.join(item_path, sub_item)
                if not os.path.isdir(sub_path):
                    continue
                if _has_nca_files(sub_path):
                    return sub_path
                sub_firmware = os.path.join(sub_path, "firmware")
                if os.path.isdir(sub_firmware):
                    return sub_firmware
    except OSError:
        pass

    return None


def _has_nca_files(path: str) -> bool:
    """检查目录中是否有 .nca 文件。"""
    try:
        return any(item.lower().endswith(".nca") for item in os.listdir(path))
    except OSError:
        return False


def install_firmware_smart(
    extract_dir: str,
    sd_path: str,
    fw_filename: str,
    progress_callback=None,
    exclude_set: set[str] | None = None,
) -> None:
    """
    智能安装固件。

    根据压缩包名称创建固件文件夹，并将固件内容复制到 SD 卡。
    """
    firmware_folder_name = os.path.splitext(fw_filename)[0]
    target_firmware_dir = os.path.join(sd_path, firmware_folder_name)

    firmware_source = find_firmware_source(extract_dir)

    if firmware_source:
        os.makedirs(target_firmware_dir, exist_ok=True)
        copy_tree(firmware_source, target_firmware_dir, progress_callback=progress_callback, exclude_set=exclude_set)
    else:
        copy_tree(extract_dir, sd_path, progress_callback=progress_callback, exclude_set=exclude_set)


def delete_local_firmware(fw_path: str) -> tuple[bool, str]:
    """
    删除本地固件文件（及空的父文件夹）。

    Returns:
        (success, message)
    """
    try:
        fw_dir = os.path.dirname(fw_path)

        if os.path.exists(fw_path):
            os.remove(fw_path)

        if fw_dir != FIRMWARE_DIR and os.path.dirname(fw_dir) == FIRMWARE_DIR:
            if os.path.exists(fw_dir) and not os.listdir(fw_dir):
                os.rmdir(fw_dir)
                return True, "固件已删除（含文件夹）"

        if fw_dir == FIRMWARE_DIR:
            if os.path.exists(FIRMWARE_DIR) and not os.listdir(FIRMWARE_DIR):
                os.rmdir(FIRMWARE_DIR)
                return True, "固件已删除（含文件夹）"

        return True, "固件已删除"
    except OSError as e:
        return False, f"删除固件失败: {e}"
