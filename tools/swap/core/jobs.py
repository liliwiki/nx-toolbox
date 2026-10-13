"""后台任务：在线程里跑，页面定时来问进度。"""

from __future__ import annotations

import logging
import threading

from core.package import Cancelled

logger = logging.getLogger(__name__)


class Job:
    def __init__(self, title: str, steps: list[str]) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._state = {"title": title, "steps": steps, "step": 0, "frac": 0.0, "detail": "", "path": "",
                       "running": True, "danger": False, "cancelled": False, "error": None, "result": None}

    def start(self, work) -> "Job":
        threading.Thread(target=self._run, args=(work,), daemon=True).start()
        return self

    def _run(self, work) -> None:
        try:
            result = work(self)
            self._update(result=result, frac=1.0)
        except Cancelled:
            self._update(cancelled=True)
        except Exception as exc:  # 任何失败都要让页面知道，不能让线程悄悄死掉
            logger.exception("任务失败")
            self._update(error=str(exc) or type(exc).__name__)
        finally:
            self._update(running=False)

    def _update(self, **changes) -> None:
        with self._lock:
            self._state.update(changes)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    # ---- 任务代码调用 ----

    def step(self, name: str, danger: bool | None = None) -> None:
        changes = {"step": self._state["steps"].index(name), "frac": 0.0, "detail": "", "path": ""}
        if danger is not None:
            changes["danger"] = danger
        self._update(**changes)

    def progress(self, frac: float, detail: str = "", path: str = "") -> None:
        self._update(frac=max(0.0, min(1.0, frac)), detail=detail, path=path)

    def check(self) -> None:
        """可以安全中断的地方调一下：暂停时在这里等，取消时在这里抛出。"""
        self._resume.wait()
        if self._cancel.is_set():
            raise Cancelled

    # ---- 页面调用 ----

    def pause(self, paused: bool) -> None:
        self._resume.clear() if paused else self._resume.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._resume.set()
