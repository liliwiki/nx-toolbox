"""换包工具：页面能调用的接口。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from core import engine, firmware, package, rules, sdcard, snapshots
from core.jobs import Job
from core.package import Cancelled
from nxhost import shared
from nxhost.api import BaseApi

TOOL_ID = "swap"
TOOL_NAME = "换包"
DEFAULT_URL = "https://download.liliwiki.com/backup/xlcj/atmosphere.zip"
DEFAULTS = {"url": DEFAULT_URL, "snapshot": True, "read_sd": True, "groups": {}, "user": {}}
OPTION_KEYS = ("url", "snapshot", "read_sd")
MAX_ITEMS = 300

TITLE_INFO_FILE = "toolbox.json"    # 插件自带的说明文件，里面有给人看的名字


def _label(verdict: rules.Verdict) -> tuple[str, str]:
    why = rules.WHY[verdict.why]
    if verdict.keep:
        return "keep", f"{why} · 保留" + ("，同名文件会更新" if verdict.overwritten else "")
    return "del", f"{why} · 清除"


def _hint(card_path: str, path: str) -> str:
    """atmosphere/contents 下的模块如果带了 toolbox.json，就用里面的名字；没有就不显示。"""
    parts = path.replace("\\", "/").split("/")
    if len(parts) != 3 or [p.lower() for p in parts[:2]] != ["atmosphere", "contents"]:
        return ""
    try:
        name = json.loads(Path(card_path, path, TITLE_INFO_FILE).read_text(encoding="utf-8-sig")).get("name")
    except (OSError, ValueError, AttributeError):
        return ""
    return name.strip() if isinstance(name, str) else ""


def _outcome(nodes: list[rules.Node]) -> dict:
    kept = sum(n.verdict.keep for n in nodes)
    gone = len(nodes) - kept
    if not gone:
        return {"cls": "keep", "text": "保留", "checked": "true"}
    if not kept:
        return {"cls": "del", "text": "清除", "checked": "false"}
    return {"cls": "mix", "text": f"保留 {kept} 项 · 清除 {gone} 项", "checked": "mixed"}


class Api(BaseApi):
    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._listing: package.Listing | None = None
        self._source_error = ""
        self._job: Job | None = None

    # ---- 设置 ----

    def _settings(self) -> dict:
        return shared.tool_settings(TOOL_ID, DEFAULTS)

    def _save(self, **patch) -> dict:
        return shared.update_tool_settings(TOOL_ID, DEFAULTS, patch)

    @staticmethod
    def _public(settings: dict) -> dict:
        """给页面看的设置。默认下载地址不外露，用空字符串表示"用默认的"。"""
        return {**settings, "url": "" if settings["url"] == DEFAULT_URL else settings["url"]}

    def options(self) -> dict:
        return self._public(self._settings())

    def set_option(self, key: str, value) -> dict:
        if key not in OPTION_KEYS:
            return {"error": f"没有这个设置：{key}"}
        if key == "url":
            value = str(value).strip() or DEFAULT_URL
        return self._public(self._save(**{key: value}))

    # ---- 存储卡 ----

    def cards(self) -> list[dict]:
        return sdcard.detect()

    def _dialog(self, kind: str, file_types: tuple = ()) -> str | None:
        if self._window is None:
            return None
        import webview
        mode = getattr(webview.FileDialog, kind)
        picked = self._window.create_file_dialog(mode, file_types=file_types) if file_types else self._window.create_file_dialog(mode)
        return picked[0] if picked else None

    def pick_card(self, path: str | None = None) -> dict:
        path = path or self._dialog("FOLDER")
        if not path:
            return {"cancelled": True}
        reason = sdcard.check_target(path)
        return {"error": reason} if reason else {"card": sdcard.info(path)}

    def card(self, path: str) -> dict:
        reason = sdcard.check_target(path)
        return {"error": reason} if reason else {"card": sdcard.info(path)}

    def eject(self, path: str) -> dict:
        ok, message = sdcard.eject(path)
        return {"ok": ok, "message": message}

    # ---- 整合包来源 ----

    def _source_state(self) -> dict:
        listing = self._listing
        if listing is None:
            return {"kind": "none", "error": self._source_error}
        return {"kind": listing.kind, "name": listing.name, "ams": listing.ams, "hos": listing.hos,
                "files": len(listing.files), "mb": round(listing.size / 1048576, 1), "error": ""}

    def use_source(self, kind: str, path: str | None = None) -> dict:
        """kind: online / local / none。本地压缩包如果其实是固件，返回 {"firmware": 路径}。"""
        self._source_error = ""
        try:
            if kind == "online":
                self._listing = None
                self._listing = package.list_online(self._settings()["url"])
            elif kind == "local":
                path = path or self._dialog("OPEN", ("压缩包 (*.zip;*.7z;*.rar)",))
                if not path:
                    return {**self._source_state(), "cancelled": True}
                what = package.classify(path)
                if what == "firmware":
                    return {**self._source_state(), "firmware": path}
                if what != "package":
                    return {**self._source_state(), "error": "这不是整合包：里面没有 atmosphere、bootloader 或 payload.bin"}
                self._listing = package.list_local(path)
            else:
                self._listing = None
        except Exception as exc:
            self._source_error = f"读不到整合包的内容：{exc}"
        return self._source_state()

    def source(self) -> dict:
        return self._source_state()

    # ---- 规则与计划 ----

    def _rules(self, card_path: str) -> rules.RuleSet:
        settings = self._settings()
        white: list[tuple[str, str]] = []
        black: list[tuple[str, str]] = []
        if settings["read_sd"]:
            sd_white, sd_black = rules.sd_lists(card_path)
            white += [(p, "SD 卡") for p in sd_white]
            black += [(p, "SD 卡") for p in sd_black]
        if self._listing:
            white += [(p, "整合包") for p in self._listing.lists["white"]]
            black += [(p, "整合包") for p in self._listing.lists["black"]]
        files = self._listing.files if self._listing else None
        return rules.RuleSet(settings["groups"], settings["user"], white, black, files)

    def plan(self, card_path: str) -> dict:
        reason = sdcard.check_target(card_path)
        if reason:
            return {"error": reason}
        ruleset = self._rules(card_path)
        nodes = rules.scan(card_path, ruleset)
        groups = []
        for meta in rules.GROUPS:
            mine = [n for n in nodes if n.verdict.group == meta["id"]]
            if not mine:
                continue
            items = []
            for node in mine[:MAX_ITEMS]:
                cls, text = _label(node.verdict)
                items.append({"path": node.path, "dir": node.is_dir, "keep": node.verdict.keep, "cls": cls, "text": text,
                              "hint": _hint(card_path, node.path)})
            groups.append({**meta, **_outcome(mine), "count": len(mine), "items": items,
                           "more": max(0, len(mine) - MAX_ITEMS)})
        deletes = [n for n in nodes if not n.verdict.keep]
        installs = [f for f in self._listing.files if ruleset.will_install(f)] if self._listing else []
        backup = sum(n.size for n in nodes if n.verdict.group != "emu" and (not n.verdict.keep or n.verdict.overwritten))
        return {"groups": groups, "source": self._source_state(),
                "totals": {"delete": sum(n.files for n in deletes), "install": len(installs),
                           "snapshot_mb": round(backup / 1048576, 1)}}

    def set_group(self, group_id: str, on: bool) -> dict:
        if group_id not in rules.GROUP_DEFAULTS:
            return {"error": f"这个类别不能开关：{group_id}"}
        settings = self._settings()
        probe = rules.RuleSet(settings["groups"], {}, [], [], self._listing.files if self._listing else None)
        # 整组重新勾选时，把组里单独设过的项一起还原
        user = {p: v for p, v in settings["user"].items() if probe.group_of(rules.norm(p)) != group_id}
        self._save(groups={**settings["groups"], group_id: bool(on)}, user=user)
        return {"ok": True}

    def set_rule(self, card_path: str, path: str, keep: bool) -> dict:
        return self.set_rules(card_path, [path], keep)

    def set_rules(self, card_path: str, paths: list[str], keep: bool) -> dict:
        """把这些路径设成保留或清除。结果和不设规则时一样的，就不留规则。"""
        targets = {rules.norm(p): p for p in paths}
        user = {p: v for p, v in self._settings()["user"].items() if rules.norm(p) not in targets}
        self._save(user=user)
        ruleset = self._rules(card_path)
        for key, path in targets.items():
            if ruleset.evaluate(key).keep != bool(keep):
                user[path] = "keep" if keep else "del"
        self._save(user=user)
        return {"ok": True}

    def children(self, card_path: str, path: str) -> list[dict]:
        """列出一个文件夹里面的东西，给页面一层层点进去用。path 为空表示卡的根目录。"""
        path = path.strip("/\\")
        folder = engine._inside(card_path, path) if path else card_path
        ruleset = self._rules(card_path)
        try:
            entries = sorted(os.scandir(folder), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
        except OSError:
            return []
        result = []
        if not path:
            entries = [e for e in entries if e.name.lower() not in rules.IGNORED_ROOTS]
        for entry in entries[:MAX_ITEMS]:
            rel = f"{path}/{entry.name}" if path else entry.name
            verdict = ruleset.evaluate(rules.norm(rel))
            cls, text = _label(verdict)
            result.append({"path": rel, "dir": entry.is_dir(follow_symlinks=False), "keep": verdict.keep,
                           "cls": cls, "text": text, "hint": _hint(card_path, rel)})
        return result

    def clear_rule(self, path: str) -> dict:
        settings = self._settings()
        self._save(user={p: v for p, v in settings["user"].items() if rules.norm(p) != rules.norm(path)})
        return {"ok": True}

    def rule_list(self, card_path: str) -> list[dict]:
        rows = self._rules(card_path).listing()
        for row in rows:
            row["hit"] = os.path.exists(os.path.join(card_path, row["path"]))
        return rows

    # ---- 执行 ----

    def _busy(self) -> bool:
        return self._job is not None and self._job.snapshot()["running"]

    def _run(self, title: str, steps: list[str], work, card_path: str) -> dict:
        def logged(job: Job):
            try:
                result = work(job)
            except Cancelled:
                shared.oplog_add(TOOL_NAME, title, "已停止" + ("，可从快照恢复" if job.snapshot()["danger"] else ""), ok=False)
                raise
            except Exception as exc:
                shared.oplog_add(TOOL_NAME, title, f"失败：{exc}", ok=False)
                raise
            shared.oplog_add(TOOL_NAME, title, result.pop("log", card_path))
            return result

        self._job = Job(title, steps).start(logged)
        return {"ok": True, "steps": steps}

    def start(self, card_path: str) -> dict:
        reason = sdcard.check_target(card_path)
        if reason:
            return {"error": reason}
        if self._busy():
            return {"error": "已经有任务在进行"}
        listing, ruleset = self._listing, self._rules(card_path)
        nodes = rules.scan(card_path, ruleset)
        has_deletes = any(not n.verdict.keep for n in nodes)
        if not listing and not has_deletes:
            return {"error": "没有需要清理的东西"}
        snapshot = bool(self._settings()["snapshot"])
        steps = engine.plan_steps(listing, snapshot, has_deletes)

        def work(job: Job) -> dict:
            result = engine.run_update(job, card_path, listing, nodes, ruleset, snapshot)
            source = "仅清理" if not listing else ("在线" if listing.kind == "online" else "本地")
            result["log"] = f"{card_path} · {source} · 清掉 {result['deleted']} · 装入 {result['installed']}"
            result["card"] = sdcard.info(card_path)
            return result

        return self._run("更新整合包" if listing else "清理存储卡", steps, work, card_path)

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

    # ---- 固件 ----

    def firmware_list(self, card_path: str | None = None) -> dict:
        hos = sdcard.info(card_path)["hos"] if card_path and os.path.isdir(card_path) else None

        def mark(items: list[dict]) -> list[dict]:
            for item in items:
                item["compatible"] = None if not (hos and item["version"]) else (
                    tuple(map(int, item["version"].split("."))) <= tuple(map(int, hos.split("."))))
            return items

        result = {"local": mark(firmware.local()), "online": [], "online_error": "", "hos": hos}
        try:
            result["online"] = mark(firmware.online())
        except Exception as exc:
            result["online_error"] = f"拿不到在线固件列表：{exc}"
        return result

    def firmware_add(self, path: str | None = None) -> dict:
        path = path or self._dialog("OPEN", ("压缩包 (*.zip;*.7z;*.rar)",))
        if not path:
            return {"cancelled": True}
        if package.classify(path) != "firmware":
            return {"error": "这个压缩包里没有固件文件（.nca）"}
        return {"path": firmware.add_local(path)}

    def firmware_delete(self, path: str) -> dict:
        try:
            firmware.delete_local(path)
        except OSError as exc:
            return {"error": str(exc)}
        return {"ok": True}

    def firmware_install(self, card_path: str, item: dict) -> dict:
        reason = sdcard.check_target(card_path)
        if reason:
            return {"error": reason}
        if self._busy():
            return {"error": "已经有任务在进行"}

        def work(job: Job) -> dict:
            result = firmware.install(job, card_path, item)
            result["log"] = f"{item['name']} → {card_path}"
            return result

        return self._run("安装固件", firmware.steps(item), work, card_path)

    # ---- 快照 ----

    def snapshots(self) -> dict:
        return {"items": snapshots.list_all(), "mb": snapshots.total_mb(), "max": snapshots.MAX_SNAPSHOTS}

    def snapshot_restore(self, snapshot_id: str, card_path: str) -> dict:
        reason = sdcard.check_target(card_path)
        if reason:
            return {"error": reason}
        if self._busy():
            return {"error": "已经有任务在进行"}

        def work(job: Job) -> dict:
            job.step("恢复文件", danger=True)
            count = snapshots.restore(snapshot_id, card_path, job.progress, job.check)
            return {"files": count, "log": f"{count} 个文件 → {card_path}"}

        return self._run("恢复快照", ["恢复文件"], work, card_path)

    def snapshot_delete(self, snapshot_id: str) -> dict:
        snapshots.delete(snapshot_id)
        return self.snapshots()

    def snapshot_delete_all(self) -> dict:
        snapshots.delete_all()
        return self.snapshots()
