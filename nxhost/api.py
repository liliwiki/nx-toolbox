"""页面能调用的接口的公共部分。工具的 Api 继承 BaseApi。"""

from __future__ import annotations

import json
import os
import platform
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from nxhost import shared


@dataclass
class Context:
    base: Path
    frozen: bool
    process_t0: float
    click_t0: float | None = None
    selftest: str | None = None
    tool: dict | None = None


class BaseApi:
    def __init__(self, ctx: Context) -> None:
        # 下划线开头的属性不会暴露给页面
        self._ctx = ctx
        self._window = None

    def ready(self) -> dict:
        """页面加载完第一件事就是调它，顺便量启动耗时。"""
        ctx = self._ctx
        if ctx.tool:
            shared.mark_ready(ctx.tool["id"])
        info = {
            "pid": os.getpid(),
            "admin": shared.is_admin(),
            "frozen": ctx.frozen,
            "python": platform.python_version(),
            "webview2": shared.webview2_version(),
            "base": str(ctx.base),
            "settings": shared.load_settings(),
            "tool": ctx.tool,
            "since_process_ms": round((time.perf_counter() - ctx.process_t0) * 1000),
            "since_click_ms": round((time.time() - ctx.click_t0) * 1000) if ctx.click_t0 else None,
        }
        if ctx.selftest:
            probe = getattr(self, "probe_stdlib", None)
            if probe:
                info["stdlib"] = probe()
            Path(ctx.selftest).write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
            threading.Timer(0.3, self._window.destroy).start()
        return info

    def settings(self) -> dict:
        return shared.load_settings()

    def set_setting(self, key: str, value) -> dict:
        return shared.save_setting(key, value)
