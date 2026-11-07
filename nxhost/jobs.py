"""后台任务：在线程里跑，页面定时来问进度。所有工具共用。"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


class Cancelled(Exception):
    """用户停止了任务。任务代码在 Job.check() 里收到它，不用自己处理。"""


class Job:
    def __init__(self, title: str, steps: list[str]) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._state = {"title": title, "steps": steps, "step": 0, "frac": 0.0, "detail": "", "path": "", "item": "", "notes": [""] * len(steps),
                       "running": True, "danger": False, "cancelled": False, "error": None, "result": None}

    def start(self, work) -> "Job":
        threading.Thread(target=self._run, args=(work,), daemon=True).start()
        return self

    def _run(self, work) -> None:
        try:
            result = work(self)
            self._close_step()
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

    def _close_step(self) -> None:
        """当前这一步做完了：没写过结果的话，把它最后的数量留下来，页面上显示在这一步旁边。"""
        with self._lock:
            state = self._state
            if state["step"] < len(state["notes"]) and not state["notes"][state["step"]]:
                state["notes"][state["step"]] = state["detail"]

    def note(self, text: str) -> None:
        """给当前这一步写一句做完之后的结果，比如"18,234 个文件，61.00 GB"。"""
        with self._lock:
            self._state["notes"][self._state["step"]] = text

    def step(self, name: str, danger: bool | None = None) -> None:
        self._close_step()
        changes = {"step": self._state["steps"].index(name), "frac": 0.0, "detail": "", "path": "", "item": ""}
        if danger is not None:
            changes["danger"] = danger
        self._update(**changes)

    def progress(self, frac: float, detail: str = "", path: str = "", item: str = "") -> None:
        """frac 是这一步的进度；detail 是数量（如 1.2 / 3.0 GB）；item 是正在做哪一项；path 是正在处理的东西。"""
        self._update(frac=max(0.0, min(1.0, frac)), detail=detail, path=path, item=item)

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
