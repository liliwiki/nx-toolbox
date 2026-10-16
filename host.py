"""NS 工具箱宿主：同一个程序，按参数启动成启动器或某个工具。

    NXToolbox.exe                 启动器
    NXToolbox.exe run <工具id>    某个工具，独立进程、独立窗口
    NXToolbox.exe setup           安装界面，由安装程序解压后调用
    NXToolbox.exe uninstall       卸载界面，系统的"已安装的应用"里点卸载时调用
    python host.py dev <工具id>   开发用：在浏览器里跑这个工具的页面（launcher 表示启动器）
"""

from __future__ import annotations

import time

T0 = time.perf_counter()

import argparse
import base64
import ctypes
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
SOURCE_DIR = Path(__file__).resolve().parent
HOME = Path(sys.executable).parent if FROZEN else SOURCE_DIR      # 安装文件夹：NXToolbox.exe 所在的位置
BASE = Path(getattr(sys, "_MEIPASS", SOURCE_DIR))                 # 界面、工具、app.json 所在的位置（打包后是 _internal）
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from nxhost import install, procs, shared, update, version
from nxhost.api import BaseApi, Context
from nxhost.detect import PROGRAMS, detect

VERSION = version.read(BASE).get("version", "0.0.0")
TOOLS_DIR = BASE / "tools"
SW_SHOWNORMAL = 1
ERROR_ACCESS_DENIED = 5
DEV_PORT = 8770
LAUNCHER_SIZE, TOOL_SIZE, SETUP_SIZE = (800, 700), (1060, 780), (560, 640)


def host_cmd(*args: str) -> list[str]:
    if FROZEN:
        return [sys.executable, *args]
    return [sys.executable, str(BASE / "host.py"), *args]


def read_tools() -> list[dict]:
    tools = []
    for manifest in sorted(TOOLS_DIR.glob("*/tool.json")):
        try:
            tool = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        tool["id"] = manifest.parent.name
        tool["dir"] = str((manifest.parent / tool.get("code", ".")).resolve())
        tool["ready"] = (Path(tool["dir"]) / "main.py").is_file()
        try:
            tool["icon"] = json.loads((manifest.parent / "icon.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            tool["icon"] = None
        tools.append(tool)
    return sorted(tools, key=lambda t: t.get("order", 99))


def render_page(page: Path) -> str:
    """把共用的样式和脚本塞进页面，工具页面不用关心它们放在哪。"""
    html = page.read_text(encoding="utf-8")
    for mark, name in (("/*BASE_CSS*/", "base.css"), ("/*BASE_JS*/", "base.js")):
        html = html.replace(mark, (BASE / "ui" / name).read_text(encoding="utf-8"))
    return html


def _folder_mb(path: Path) -> float:
    total = 0
    for folder, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    return round(total / 1048576, 1)


class LauncherApi(BaseApi):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self._update_info: dict | None = None
        self._update = {"state": "idle", "frac": 0.0, "detail": "", "error": ""}
        self._installer: Path | None = None

    # ---- 在线更新 ----

    def check_update(self) -> dict:
        try:
            self._update_info = update.check(VERSION)
        except Exception as exc:
            return {"error": f"检查不了更新：{exc}"}
        return {k: self._update_info[k] for k in ("current", "latest", "newer", "notes", "date", "size")}

    def start_update(self) -> dict:
        """后台下载新版，页面用 update_progress 跟进。"""
        if not FROZEN:
            return {"error": "源码方式运行时不能更新"}
        if self._update_info is None or not self._update_info["newer"]:
            return {"error": "没有可用的更新"}
        if self._update["state"] == "downloading":
            return {"ok": True}
        self._update = {"state": "downloading", "frac": 0.0, "detail": "", "error": ""}
        info = self._update_info

        def work() -> None:
            try:
                def progress(done: int, total: int) -> None:
                    self._update.update(frac=done / total if total else 0.0,
                                        detail=f"{done / 1048576:.1f} / {total / 1048576:.1f} MB")
                self._installer = update.download(info, progress)
                self._update.update(state="ready", frac=1.0)
            except Exception as exc:
                self._update.update(state="error", error=str(exc))

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def update_progress(self) -> dict:
        return dict(self._update)

    def apply_update(self) -> dict:
        """关掉自己，让新版来替换文件。别的工具窗口还开着就先不动。"""
        if self._update["state"] != "ready" or self._installer is None:
            return {"error": "更新还没有准备好"}
        if procs.running_from(HOME):
            return {"error": "还有工具窗口开着。请先把它们关掉，再点一次。"}
        update.launch_installer(self._installer, HOME)
        if self._window is not None:
            threading.Timer(0.2, self._window.destroy).start()
        return {"ok": True}

    # ---- 首页 ----

    def home(self) -> dict:
        hidden = set(shared.load_settings().get("hidden_tools", []))
        return {"version": VERSION, "tools": [{**t, "hidden": t["id"] in hidden} for t in read_tools()],
                "logs": len(shared.oplog_list())}

    def launch(self, tool_id: str) -> dict:
        tool = next((t for t in read_tools() if t["id"] == tool_id), None)
        if tool is None or not tool["ready"]:
            return {"ok": False, "error": "这个工具还没有做好"}
        cmd = host_cmd("run", tool_id, "--t0", repr(time.time()))
        if tool.get("admin") and not shared.is_admin():
            # 只给这一个工具提权，启动器自己保持普通权限
            code = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", cmd[0], subprocess.list2cmdline(cmd[1:]), str(HOME), SW_SHOWNORMAL)
            if code <= 32:
                denied = code == ERROR_ACCESS_DENIED
                return {"ok": False, "error": "没有获得管理员权限" if denied else f"启动失败（{code}）"}
            return {"ok": True, "elevated": True}
        subprocess.Popen(cmd, cwd=HOME)
        return {"ok": True, "elevated": False}

    def is_ready(self, tool_id: str, since: float) -> bool:
        return shared.ready_since(tool_id, since)

    def set_tool_hidden(self, tool_id: str, hidden: bool) -> dict:
        current = set(shared.load_settings().get("hidden_tools", []))
        current.add(tool_id) if hidden else current.discard(tool_id)
        shared.save_setting("hidden_tools", sorted(current))
        return self.home()

    def programs(self) -> list[dict]:
        tools = [t for t in read_tools() if t["ready"]]
        return [{"id": pid, "name": spec["name"],
                 "used_by": [t["name"] for t in tools if pid in t.get("programs", [])]}
                for pid, spec in PROGRAMS.items()
                if any(pid in t.get("programs", []) for t in tools)]

    def detect(self, program_id: str, deep: bool = False) -> dict:
        start = time.perf_counter()
        result = detect(program_id, deep=deep)
        result["ms"] = round((time.perf_counter() - start) * 1000)
        return result

    def storage(self) -> dict:
        """各个工具占了多少空间。占用的位置由工具在 tool.json 的 storage 里自己声明。"""
        items = []
        for tool in (t for t in read_tools() if t["ready"]):
            for entry in tool.get("storage", []):
                moved = shared.load_settings().get(tool["id"], {}).get(entry.get("setting", ""))    # 用户改过位置的话以设置为准
                folder = Path(moved) if moved else shared.LEGACY_DIR / entry["path"]
                items.append({"tool": tool["name"], "name": entry["name"], "where": entry.get("where", ""), "mb": _folder_mb(folder)})
        return {"items": items, "data_dir": str(shared.DATA_DIR)}

    def logs(self) -> list[dict]:
        return shared.oplog_list()

    def clear_logs(self) -> list[dict]:
        shared.oplog_clear()
        return []


def _icon_uri() -> str:
    data = (BASE / "ui" / "icon.png").read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


class SetupApi(BaseApi):
    """安装界面。source 是安装程序解压出来的新版文件夹，也就是当前程序自己所在的位置。"""

    def __init__(self, ctx: Context, source: Path) -> None:
        super().__init__(ctx)
        self._source = source
        self._target: Path | None = None
        self._state = {"state": "idle", "frac": 0.0, "error": ""}

    def info(self) -> dict:
        existing = install.installed()
        return {"version": VERSION, "existing": existing, "icon": _icon_uri(),
                "dir": existing["dir"] if existing else str(install.default_dir()),
                "desktop": existing["desktop"] if existing else True,
                "startmenu": existing["startmenu"] if existing else True}

    def browse(self) -> dict:
        if self._window is None:
            return {"cancelled": True}
        import webview
        picked = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return {"dir": str(install.normalize_target(picked[0]))} if picked else {"cancelled": True}

    def start(self, folder: str, desktop: bool, startmenu: bool) -> dict:
        target = Path(folder)
        reason = install.check_target(target)
        if reason:
            return {"error": reason}
        self._target = target
        self._state = {"state": "installing", "frac": 0.0, "error": ""}

        def work() -> None:
            try:
                install.install(self._source, target, bool(desktop), bool(startmenu),
                                lambda frac: self._state.update(frac=frac))
                self._state.update(state="done", frac=1.0)
            except Exception as exc:
                self._state.update(state="error", error=str(exc))

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def progress(self) -> dict:
        return dict(self._state)

    def finish(self, launch: bool) -> dict:
        if launch and self._target is not None:
            subprocess.Popen([str(self._target / procs.EXE_NAME)], cwd=self._target)
        if self._window is not None:
            threading.Timer(0.2, self._window.destroy).start()
        return {"ok": True}


class UninstallApi(BaseApi):
    """卸载界面。manage 表示是双击安装程序进来的（已经装好了），这时多给一个"打开"。"""

    def __init__(self, ctx: Context, manage: bool) -> None:
        super().__init__(ctx)
        self._manage = manage

    def info(self) -> dict:
        return {"version": VERSION, "dir": str(HOME), "data_dir": str(shared.LEGACY_DIR), "icon": _icon_uri(),
                "manage": self._manage}

    def open(self) -> dict:
        subprocess.Popen([str(HOME / procs.EXE_NAME)], cwd=HOME)
        return self.finish()

    def start(self, remove_data: bool) -> dict:
        if procs.running_from(HOME):
            return {"error": "NS 工具箱还有窗口开着，请先全部关掉"}
        install.unregister()
        if remove_data:
            shutil.rmtree(shared.LEGACY_DIR, ignore_errors=True)
        install.remove_folder_after_exit(HOME)
        return {"ok": True}

    def finish(self) -> dict:
        if self._window is not None:
            threading.Timer(0.2, self._window.destroy).start()
        return {"ok": True}


def load_tool_api(tool: dict, ctx: Context) -> BaseApi:
    """工具代码放在程序外面的 tools 文件夹里，运行时再加载。"""
    sys.path.insert(0, tool["dir"])
    spec = importlib.util.spec_from_file_location(f"nxtool_{tool['id']}", Path(tool["dir"]) / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Api(ctx)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", nargs="?", default="launcher", choices=["launcher", "run", "dev", "setup", "uninstall"])
    parser.add_argument("tool", nargs="?")
    parser.add_argument("--t0", type=float, help="启动器点下按钮的时刻，用来量启动耗时")
    parser.add_argument("--selftest", help="窗口就绪后把耗时写进这个文件并退出")
    parser.add_argument("--port", type=int, default=DEV_PORT)
    parser.add_argument("--source", help="setup：新版文件所在的文件夹")
    parser.add_argument("--manage", action="store_true", help="uninstall：从安装程序进来的，多给一个打开按钮")
    args = parser.parse_args()

    ctx = Context(base=BASE, frozen=FROZEN, process_t0=T0, click_t0=args.t0, selftest=args.selftest)
    screen = args.cmd if args.cmd in ("setup", "uninstall") else args.tool if args.tool in ("setup", "uninstall") else None
    if screen == "setup":
        api = SetupApi(ctx, Path(args.source) if args.source else HOME)
        title, page, size = f"安装 {install.APP_NAME}", BASE / "setup" / "index.html", SETUP_SIZE
    elif screen == "uninstall":
        api = UninstallApi(ctx, args.manage)
        title, page, size = install.APP_NAME if args.manage else f"卸载 {install.APP_NAME}", BASE / "setup" / "uninstall.html", SETUP_SIZE
    elif args.tool and args.tool != "launcher":
        tool = next((t for t in read_tools() if t["id"] == args.tool and t["ready"]), None)
        if tool is None:
            sys.exit(f"没有这个工具：{args.tool}")
        ctx.tool = tool
        api = load_tool_api(tool, ctx)
        title, page, size = tool["name"], Path(tool["dir"]) / "index.html", tuple(tool.get("window", TOOL_SIZE))
    else:
        if args.cmd == "launcher" and FROZEN:
            update.cleanup()
        api = LauncherApi(ctx)
        title, page, size = "NS 工具箱", BASE / "launcher" / "index.html", LAUNCHER_SIZE

    if args.cmd == "dev":
        if FROZEN:
            sys.exit("dev 只能在源码方式下用")
        from nxhost.devserver import serve
        serve(api, render_page(page), args.port)
        return

    import webview

    dark = shared.load_settings().get("theme") == "dark"
    window = webview.create_window(
        title, html=render_page(page), js_api=api,
        width=size[0], height=size[1], min_size=(min(620, size[0]), 480),
        background_color="#000000" if dark else "#f5f5f7")
    api._window = window
    webview.start(gui="edgechromium")


if __name__ == "__main__":
    main()
