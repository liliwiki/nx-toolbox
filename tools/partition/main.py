"""换卡工具：页面能调用的接口。"""

from __future__ import annotations

import shutil
from pathlib import Path

from core import disk, layout, migrate, userfs
from nxhost import shared
from nxhost.api import BaseApi
from nxhost.jobs import Cancelled, Job

TOOL_ID = "partition"
TOOL_NAME = "换卡"
STAGE_FOLDER = "NXToolbox 换卡"                 # 用户选的位置下面再建这个文件夹，不直接往别人的文件夹里放
DEFAULTS = {"verify": True, "stage_dir": ""}
OPTION_KEYS = ("verify",)
ERRORS = (migrate.MigrateError, layout.LayoutError, disk.DiskError, userfs.UserError)


class Api(BaseApi):
    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._job: Job | None = None
        self._seen: dict[int, dict] = {}        # 看过的旧卡，免得每次都重新数一遍文件

    # ---- 设置 ----

    def _settings(self) -> dict:
        return shared.tool_settings(TOOL_ID, DEFAULTS)

    def options(self) -> dict:
        return self._settings()

    def set_option(self, key: str, value) -> dict:
        if key not in OPTION_KEYS:
            return {"error": f"没有这个设置：{key}"}
        return shared.update_tool_settings(TOOL_ID, DEFAULTS, {key: bool(value)})

    def _stage_dir(self) -> Path:
        chosen = self._settings()["stage_dir"]
        return Path(chosen) if chosen else shared.DATA_DIR / "card"

    # ---- 卡 ----

    def cards(self) -> list[dict]:
        return disk.list_disks()

    def inspect(self, number: int) -> dict:
        try:
            self._seen[number] = migrate.inspect(int(number))
        except ERRORS as exc:
            return {"error": str(exc)}
        except OSError as exc:
            return {"error": f"读不了这张卡：{exc.strerror or exc}"}
        return self._seen[number]

    def stage(self) -> dict:
        """电脑上存放旧卡的位置：在哪、还剩多少空间、里面有没有存好的。"""
        folder = self._stage_dir()
        probe = folder
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        return {"dir": str(folder), "free": shutil.disk_usage(probe).free, "saved": migrate.stage_summary(folder)}

    def pick_stage_dir(self) -> dict:
        if self._window is None:
            return {"cancelled": True}
        import webview
        picked = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not picked:
            return {"cancelled": True}
        folder = Path(picked[0])
        folder = folder if folder.name == STAGE_FOLDER else folder / STAGE_FOLDER
        shared.update_tool_settings(TOOL_ID, DEFAULTS, {"stage_dir": str(folder)})
        return self.stage()

    def discard(self) -> dict:
        if self._busy():
            return {"error": "已经有任务在进行"}
        migrate.discard_stage(self._stage_dir())
        return self.stage()

    def _open_source(self, source: dict):
        if source.get("kind") == "stage":
            return migrate.StagedSource(self._stage_dir())
        return migrate.DiskSource(int(source["number"]))

    def check(self, source: dict, target_number: int, sizes: dict | None = None) -> dict:
        """新卡行不行；行的话告诉页面它会变成什么样。sizes：第几个分区 → 想要的新大小（字节）。"""
        try:
            target = next((d for d in disk.list_disks() if d["number"] == int(target_number)), None)
            if target is None:
                return {"error": "找不到这张卡了，可能被拔掉了"}
            if source.get("kind") == "stage":
                summary = migrate.stage_summary(self._stage_dir())
            else:
                summary = self._seen.get(int(source["number"])) or self.inspect(int(source["number"]))
            if not summary or summary.get("error"):
                return {"error": (summary or {}).get("error", "电脑上没有存好的旧卡内容")}
            opened = self._open_source(source)
            try:
                return {"ok": True, "target": target, **migrate.check_target(summary, opened.card, target, opened.number, sizes)}
            finally:
                opened.close()
        except ERRORS as exc:
            return {"error": str(exc)}

    # ---- 执行 ----

    def _busy(self) -> bool:
        return self._job is not None and self._job.snapshot()["running"]

    def _run(self, title: str, steps: list[str], work, half_done: str) -> dict:
        def logged(job: Job):
            try:
                result = work(job)
            except Cancelled:
                shared.oplog_add(TOOL_NAME, title, "已停止" + (half_done if job.snapshot()["danger"] else ""), ok=False)
                raise
            except Exception as exc:
                shared.oplog_add(TOOL_NAME, title, f"失败：{exc}", ok=False)
                raise
            shared.oplog_add(TOOL_NAME, title, result.pop("log", ""))
            return result

        self._job = Job(title, steps).start(logged)
        return {"ok": True, "steps": steps}

    def start_save(self, number: int) -> dict:
        if self._busy():
            return {"error": "已经有任务在进行"}
        summary = self._seen.get(int(number)) or self.inspect(int(number))
        if summary.get("error"):
            return summary
        folder = self._stage_dir()

        def work(job: Job) -> dict:
            result = migrate.save(job, int(number), folder)
            result["log"] = f"{summary['name']} · {result['bytes'] / 1073741824:.1f} GB 存到 {folder}"
            return result

        return self._run("把旧卡存到电脑", migrate.save_steps(summary), work, "")

    def start_write(self, source: dict, target_number: int, target_size: int, sizes: dict | None = None) -> dict:
        if self._busy():
            return {"error": "已经有任务在进行"}
        verify = bool(self._settings()["verify"])
        try:
            opened = self._open_source(source)
        except ERRORS as exc:
            return {"error": str(exc)}
        staged = source.get("kind") == "stage"
        summary = migrate.stage_summary(self._stage_dir()) if staged else (self._seen.get(opened.number) or migrate.inspect(opened.number))

        def work(job: Job) -> dict:
            try:
                result = migrate.write(job, opened, int(target_number), int(target_size), verify, sizes)
            finally:
                opened.close()
            result["log"] = (f"{opened.name} → {result['name']} · {result['files']} 个文件"
                             + (f" · {result['parts']} 个分区" if result["parts"] else ""))
            result["staged"] = staged
            return result

        return self._run("写入新卡", migrate.write_steps(summary, verify), work, "，新卡还没写完，重新开始一次即可")

    def progress(self) -> dict:
        return self._job.snapshot() if self._job else {"running": False, "idle": True}

    def pause(self, paused: bool) -> dict:
        if self._job:
            self._job.pause(bool(paused))
        return {"ok": True}

    def stop(self) -> dict:
        if self._job:
            self._job.cancel()
        return {"ok": True}
