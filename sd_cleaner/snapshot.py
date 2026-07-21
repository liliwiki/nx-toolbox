"""快照管理 - 操作前备份 SD 卡文件，支持回滚恢复"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime

from sd_cleaner.common import APPDATA_DIR

logger = logging.getLogger(__name__)

SNAPSHOTS_DIR = os.path.join(APPDATA_DIR, "backups")
SNAPSHOTS_INDEX = os.path.join(SNAPSHOTS_DIR, "index.json")
MAX_SNAPSHOTS = 10


@dataclass
class SnapshotMeta:
    """快照元信息。"""

    snapshot_id: str
    timestamp: str
    operation: str
    sd_path: str
    description: str
    file_count: int = 0
    total_size_mb: float = 0.0
    folder_name: str = ""

    @staticmethod
    def generate_id() -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")


class SnapshotManager:
    """快照管理器：创建、列出、恢复、删除快照。"""

    def __init__(self, snapshots_dir: str = SNAPSHOTS_DIR) -> None:
        self._dir = snapshots_dir
        self._index_path = os.path.join(self._dir, "index.json")
        self._index: list[dict] = []
        os.makedirs(self._dir, exist_ok=True)
        self._load_index()

    # ------------------------------------------------------------------
    # 索引管理
    # ------------------------------------------------------------------

    def _load_index(self) -> None:
        if not os.path.exists(self._index_path):
            self._index = []
            return
        try:
            with open(self._index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._index = data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("加载快照索引失败: %s", e)
            self._index = []

    def _save_index(self) -> None:
        try:
            with open(self._index_path, "w", encoding="utf-8") as f:
                json.dump(self._index, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error("保存快照索引失败: %s", e)

    # ------------------------------------------------------------------
    # 大小预估
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_clean_size(sd_path: str, files: list[str], folders: list[str]) -> float:
        """预估清理快照大小 (MB)，不实际复制文件。"""
        total = 0
        for folder in folders:
            path = os.path.join(sd_path, folder)
            if os.path.exists(path):
                total += _dir_size(path)
        for file in files:
            path = os.path.join(sd_path, file)
            if os.path.isfile(path):
                try:
                    total += os.path.getsize(path)
                except OSError:
                    pass
        return round(total / (1024 * 1024), 1)

    @staticmethod
    def estimate_update_size(sd_path: str, targets: list[str] | None = None) -> float:
        """预估更新快照大小 (MB)，不实际复制文件。"""
        if targets is None:
            targets = ["atmosphere", "bootloader", "switch", "payload.bin"]
        total = 0
        for target in targets:
            path = os.path.join(sd_path, target)
            if os.path.isdir(path):
                total += _dir_size(path)
            elif os.path.isfile(path):
                try:
                    total += os.path.getsize(path)
                except OSError:
                    pass
        return round(total / (1024 * 1024), 1)

    # ------------------------------------------------------------------
    # 创建快照
    # ------------------------------------------------------------------

    def create_snapshot_for_clean(
        self,
        sd_path: str,
        files_to_delete: list[str],
        folders_to_delete: list[str],
        progress_callback=None,
    ) -> str | None:
        """
        清理操作前创建快照：备份即将删除的文件和文件夹。

        Returns:
            快照 ID，失败返回 None。
        """
        meta = SnapshotMeta(
            snapshot_id=SnapshotMeta.generate_id(),
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            operation="清理SD卡",
            sd_path=sd_path,
            description=f"删除 {len(files_to_delete)} 个文件 + {len(folders_to_delete)} 个文件夹",
        )
        meta.folder_name = meta.snapshot_id

        snapshot_path = os.path.join(self._dir, meta.folder_name)
        os.makedirs(snapshot_path, exist_ok=True)

        total_items = len(files_to_delete) + len(folders_to_delete)
        copied = 0
        total_size = 0

        try:
            for folder in folders_to_delete:
                src = os.path.join(sd_path, folder)
                dst = os.path.join(snapshot_path, folder)
                if os.path.exists(src):
                    shutil.copytree(src, dst)
                    total_size += _dir_size(dst)
                    copied += 1
                    if progress_callback:
                        progress_callback(int(copied * 100 / total_items), f"备份: {folder}")

            for file in files_to_delete:
                src = os.path.join(sd_path, file)
                dst = os.path.join(snapshot_path, file)
                if os.path.exists(src):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    total_size += os.path.getsize(dst)
                    copied += 1
                    if progress_callback:
                        progress_callback(int(copied * 100 / total_items), f"备份: {file}")

        except OSError as e:
            logger.error("创建清理快照失败: %s", e)
            shutil.rmtree(snapshot_path, ignore_errors=True)
            return None

        meta.file_count = copied
        meta.total_size_mb = round(total_size / (1024 * 1024), 1)

        self._index.append(asdict(meta))
        self._enforce_limit()
        self._save_index()

        logger.info("创建清理快照: %s (%d 项, %.1f MB)", meta.snapshot_id, copied, meta.total_size_mb)
        return meta.snapshot_id

    def create_snapshot_for_update(
        self,
        sd_path: str,
        description: str = "更新整合包",
        targets: list[str] | None = None,
        progress_callback=None,
    ) -> str | None:
        """
        更新操作前创建快照：备份将被覆盖的关键目录。

        Args:
            targets: 要备份的目录名列表。默认备份 atmosphere, bootloader, switch, payload.bin。
        """
        if targets is None:
            targets = ["atmosphere", "bootloader", "switch", "payload.bin"]

        meta = SnapshotMeta(
            snapshot_id=SnapshotMeta.generate_id(),
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            operation=description,
            sd_path=sd_path,
            description=description,
        )
        meta.folder_name = meta.snapshot_id
        snapshot_path = os.path.join(self._dir, meta.folder_name)
        os.makedirs(snapshot_path, exist_ok=True)

        total_size = 0
        file_count = 0

        try:
            existing_targets = [t for t in targets if os.path.exists(os.path.join(sd_path, t))]
            for i, target in enumerate(existing_targets):
                src = os.path.join(sd_path, target)
                dst = os.path.join(snapshot_path, target)
                if progress_callback:
                    progress_callback(int((i + 1) * 100 / max(len(existing_targets), 1)), f"备份: {target}")

                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                    size = _dir_size(dst)
                    total_size += size
                    file_count += sum(len(fs) for _, _, fs in os.walk(dst))
                elif os.path.isfile(src):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    total_size += os.path.getsize(dst)
                    file_count += 1

        except OSError as e:
            logger.error("创建更新快照失败: %s", e)
            shutil.rmtree(snapshot_path, ignore_errors=True)
            return None

        if file_count == 0:
            shutil.rmtree(snapshot_path, ignore_errors=True)
            return None

        meta.file_count = file_count
        meta.total_size_mb = round(total_size / (1024 * 1024), 1)

        self._index.append(asdict(meta))
        self._enforce_limit()
        self._save_index()

        logger.info("创建更新快照: %s (%d 文件, %.1f MB)", meta.snapshot_id, file_count, meta.total_size_mb)
        return meta.snapshot_id

    # ------------------------------------------------------------------
    # 恢复快照
    # ------------------------------------------------------------------

    def restore_snapshot(
        self,
        snapshot_id: str,
        progress_callback=None,
    ) -> tuple[bool, str]:
        """
        从快照恢复文件到 SD 卡。

        Returns:
            (success, message)
        """
        meta = self._find_meta(snapshot_id)
        if meta is None:
            return False, f"快照 {snapshot_id} 不存在"

        snapshot_path = os.path.join(self._dir, meta.get("folder_name", snapshot_id))
        if not os.path.exists(snapshot_path):
            return False, "快照数据目录不存在"

        sd_path = meta.get("sd_path", "")
        if not sd_path or not os.path.exists(sd_path):
            return False, f"SD卡路径不存在: {sd_path}"

        restored = 0
        errors: list[str] = []

        try:
            items = os.listdir(snapshot_path)
            total = len(items)

            for i, item in enumerate(items):
                src = os.path.join(snapshot_path, item)
                dst = os.path.join(sd_path, item)

                if progress_callback:
                    progress_callback(int((i + 1) * 100 / max(total, 1)), f"恢复: {item}")

                try:
                    if os.path.isdir(src):
                        if os.path.exists(dst):
                            shutil.rmtree(dst)
                        shutil.copytree(src, dst)
                    else:
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
                    restored += 1
                except OSError as e:
                    errors.append(f"{item}: {e}")

        except OSError as e:
            return False, f"恢复失败: {e}"

        if errors:
            return True, f"已恢复 {restored} 项，{len(errors)} 项失败:\n" + "\n".join(errors[:5])
        return True, f"已成功恢复 {restored} 项"

    # ------------------------------------------------------------------
    # 列出 / 删除快照
    # ------------------------------------------------------------------

    def list_snapshots(self) -> list[dict]:
        """返回所有快照（最新在前）。"""
        return list(reversed(self._index))

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """删除指定快照。"""
        meta = self._find_meta(snapshot_id)
        if meta is None:
            return False

        folder = meta.get("folder_name", snapshot_id)
        snapshot_path = os.path.join(self._dir, folder)
        if os.path.exists(snapshot_path):
            shutil.rmtree(snapshot_path, ignore_errors=True)

        self._index = [m for m in self._index if m.get("snapshot_id") != snapshot_id]
        self._save_index()
        return True

    def delete_all_snapshots(self) -> None:
        """删除所有快照。"""
        for meta in self._index:
            folder = meta.get("folder_name", meta.get("snapshot_id", ""))
            path = os.path.join(self._dir, folder)
            if os.path.exists(path):
                shutil.rmtree(path, ignore_errors=True)
        self._index = []
        self._save_index()

    def get_total_size_mb(self) -> float:
        """获取所有快照占用的总大小 (MB)。"""
        return sum(m.get("total_size_mb", 0) for m in self._index)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _find_meta(self, snapshot_id: str) -> dict | None:
        for m in self._index:
            if m.get("snapshot_id") == snapshot_id:
                return m
        return None

    def _enforce_limit(self) -> None:
        """超过数量限制时删除最旧的快照。"""
        while len(self._index) > MAX_SNAPSHOTS:
            oldest = self._index.pop(0)
            folder = oldest.get("folder_name", oldest.get("snapshot_id", ""))
            path = os.path.join(self._dir, folder)
            if os.path.exists(path):
                shutil.rmtree(path, ignore_errors=True)
            logger.info("自动清理旧快照: %s", folder)


def _dir_size(path: str) -> int:
    """递归计算目录大小。"""
    total = 0
    try:
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total
