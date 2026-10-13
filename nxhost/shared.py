"""跨进程共用的东西：设置文件、操作日志、权限、WebView2 版本。"""

from __future__ import annotations

import ctypes
import json
import os
import time
import winreg
from pathlib import Path

LEGACY_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "NXToolbox"
DATA_DIR = LEGACY_DIR / "v2"
SETTINGS_FILE = DATA_DIR / "settings.json"
OPLOG_FILE = DATA_DIR / "oplog.json"
OPLOG_LIMIT = 200

_WEBVIEW2_CLIENT = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    """启动器和提权后的工具都会写这些文件，先写临时文件再替换。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_settings() -> dict:
    return _read_json(SETTINGS_FILE, {})


def save_setting(key: str, value) -> dict:
    settings = load_settings()
    settings[key] = value
    _write_json(SETTINGS_FILE, settings)
    return settings


def tool_settings(tool_id: str, defaults: dict) -> dict:
    """每个工具自己的设置放在以工具 id 命名的那一节里。"""
    return {**defaults, **load_settings().get(tool_id, {})}


def update_tool_settings(tool_id: str, defaults: dict, patch: dict) -> dict:
    merged = {**tool_settings(tool_id, defaults), **patch}
    save_setting(tool_id, merged)
    return merged


def oplog_add(tool: str, title: str, detail: str = "", ok: bool = True) -> None:
    records = _read_json(OPLOG_FILE, [])
    records.append({"time": time.strftime("%Y-%m-%d %H:%M"), "tool": tool, "title": title, "detail": detail, "ok": ok})
    _write_json(OPLOG_FILE, records[-OPLOG_LIMIT:])


def oplog_list(limit: int = 100) -> list[dict]:
    return list(reversed(_read_json(OPLOG_FILE, [])[-limit:]))


def oplog_clear() -> None:
    _write_json(OPLOG_FILE, [])


def mark_ready(tool_id: str) -> None:
    """工具窗口就绪时留个记号，启动器据此知道可以收起"正在打开"的动画。"""
    _write_json(DATA_DIR / "run" / f"{tool_id}.json", {"time": time.time()})


def ready_since(tool_id: str, since: float) -> bool:
    return _read_json(DATA_DIR / "run" / f"{tool_id}.json", {}).get("time", 0) >= since


def webview2_version() -> str | None:
    """没装 WebView2 时返回 None，启动器据此提示安装。"""
    for root, sub in ((winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + _WEBVIEW2_CLIENT),
                      (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\" + _WEBVIEW2_CLIENT),
                      (winreg.HKEY_CURRENT_USER, "SOFTWARE\\" + _WEBVIEW2_CLIENT)):
        try:
            with winreg.OpenKey(root, sub) as key:
                version = winreg.QueryValueEx(key, "pv")[0]
                if version and version != "0.0.0.0":
                    return version
        except OSError:
            continue
    return None
