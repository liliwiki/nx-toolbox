"""整合包管理 - 结构检测与安装"""

from __future__ import annotations

import os

from sd_cleaner.archive import copy_tree


def find_package_root(extract_dir: str, max_depth: int = 5) -> str | None:
    """
    查找整合包的正确根目录。

    有效的根目录应包含 atmosphere / bootloader / payload.bin 之一。
    使用 os.walk() 递归搜索，最大深度 max_depth 层。
    """
    if is_valid_package_root(extract_dir):
        return extract_dir

    try:
        for dirpath, dirnames, _ in os.walk(extract_dir):
            depth = dirpath[len(extract_dir):].count(os.sep)
            if depth >= max_depth:
                dirnames.clear()  # 不再深入
                continue
            for d in dirnames:
                full = os.path.join(dirpath, d)
                if is_valid_package_root(full):
                    return full
    except OSError:
        pass

    return None


def is_valid_package_root(path: str) -> bool:
    """检查路径是否是有效的整合包根目录。"""
    return (
        os.path.exists(os.path.join(path, "atmosphere"))
        or os.path.exists(os.path.join(path, "bootloader"))
        or os.path.exists(os.path.join(path, "payload.bin"))
    )


def install_package_smart(
    extract_dir: str,
    sd_path: str,
    progress_callback=None,
    exclude_set: set[str] | None = None,
) -> None:
    """智能安装整合包：查找正确根目录并复制到 SD 卡。"""
    package_root = find_package_root(extract_dir)
    if package_root is None:
        raise RuntimeError(
            "无法识别整合包结构。解压目录中未找到 atmosphere/bootloader/payload.bin，"
            "请确认选择了正确的整合包文件。"
        )
    copy_tree(package_root, sd_path, progress_callback=progress_callback, exclude_set=exclude_set)
