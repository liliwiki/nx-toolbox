"""操作日志 - 记录清理/更新/固件安装等操作历史"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime

from sd_cleaner.common import APPDATA_DIR

logger = logging.getLogger(__name__)

LOG_FILE = os.path.join(APPDATA_DIR, "sd_cleaner_operations.json")
MAX_LOG_ENTRIES = 200


@dataclass
class OperationRecord:
    """单次操作记录。"""

    timestamp: str
    operation: str
    sd_path: str
    details: dict = field(default_factory=dict)
    success: bool = True
    error_msg: str = ""

    @staticmethod
    def now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class OperationLogger:
    """操作日志管理器。"""

    def __init__(self, log_path: str = LOG_FILE) -> None:
        self._path = log_path
        self._records: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            self._records = []
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._records = data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("加载操作日志失败: %s", e)
            self._records = []

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._records, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error("保存操作日志失败: %s", e)

    def log(
        self,
        operation: str,
        sd_path: str,
        details: dict | None = None,
        success: bool = True,
        error_msg: str = "",
    ) -> None:
        """记录一条操作日志。"""
        record = OperationRecord(
            timestamp=OperationRecord.now(),
            operation=operation,
            sd_path=sd_path,
            details=details or {},
            success=success,
            error_msg=error_msg,
        )
        self._records.append(asdict(record))

        if len(self._records) > MAX_LOG_ENTRIES:
            self._records = self._records[-MAX_LOG_ENTRIES:]

        self._save()

    def log_clean(
        self,
        sd_path: str,
        deleted_files: list[str],
        deleted_folders: list[str],
        preserved: list[str],
        success: bool = True,
        error_msg: str = "",
    ) -> None:
        """记录清理操作。"""
        self.log(
            operation="清理SD卡",
            sd_path=sd_path,
            details={
                "deleted_files": deleted_files,
                "deleted_folders": deleted_folders,
                "deleted_count": len(deleted_files) + len(deleted_folders),
                "preserved": preserved,
            },
            success=success,
            error_msg=error_msg,
        )

    def log_package_update(
        self,
        sd_path: str,
        source: str,
        package_name: str = "",
        success: bool = True,
        error_msg: str = "",
    ) -> None:
        """记录整合包更新操作。"""
        self.log(
            operation="更新整合包",
            sd_path=sd_path,
            details={"source": source, "package_name": package_name},
            success=success,
            error_msg=error_msg,
        )

    def log_firmware_install(
        self,
        sd_path: str,
        firmware_name: str,
        source: str,
        success: bool = True,
        error_msg: str = "",
    ) -> None:
        """记录固件安装操作。"""
        self.log(
            operation="安装固件",
            sd_path=sd_path,
            details={"firmware_name": firmware_name, "source": source},
            success=success,
            error_msg=error_msg,
        )

    def get_records(self, limit: int = 50) -> list[dict]:
        """获取最近的操作记录。"""
        return list(reversed(self._records[-limit:]))

    def get_records_for_display(self, limit: int = 20) -> str:
        """获取用于 UI 显示的格式化日志文本。"""
        records = self.get_records(limit)
        if not records:
            return "暂无操作记录"

        lines: list[str] = []
        for r in records:
            status = "✅" if r.get("success", True) else "❌"
            ts = r.get("timestamp", "")
            op = r.get("operation", "")
            sd = r.get("sd_path", "")
            line = f"{status} [{ts}] {op}"

            details = r.get("details", {})
            if op == "清理SD卡":
                count = details.get("deleted_count", 0)
                line += f" - 删除 {count} 项"
            elif op == "更新整合包":
                src = details.get("source", "")
                line += f" - {src}"
            elif op == "安装固件":
                fw = details.get("firmware_name", "")
                line += f" - {fw}"

            if not r.get("success", True):
                err = r.get("error_msg", "")
                if err:
                    line += f"\n   错误: {err}"

            lines.append(line)

        return "\n".join(lines)

    def clear(self) -> None:
        """清空所有日志。"""
        self._records = []
        self._save()
