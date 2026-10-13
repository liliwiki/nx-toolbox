"""自动查找电脑上已经装好的第三方程序。

快速查找：注册表（App Paths、卸载信息）、Windows 记下的"运行过的程序"、常见安装文件夹。
彻底查找：再加上开始菜单和桌面的快捷方式，以及按线索优先的全盘搜索。

绿色软件和图吧工具箱这类合集不会写安装信息，靠后两种办法找：
只要用户运行过一次，Windows 就记下了完整路径；没运行过，就顺着名字像"工具箱"的文件夹往下找。
"""

from __future__ import annotations

import ctypes
import heapq
import os
import re
import string
import time
import winreg
from pathlib import Path

PROGRAMS: dict[str, dict] = {
    "diskgenius": {"name": "DiskGenius", "exe": ["DiskGenius.exe"], "keywords": ["diskgenius"]},
}

_UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
_APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
_COMMON_FOLDERS = ("Tools", "Program Files", "Program Files (x86)", "Software", "Apps", "软件", "工具", "绿色软件")
_DRIVE_FIXED = 3
_SKIP_DIRS = {"windows", "system volume information", "programdata", "users", "node_modules", "winsxs",
              "steamapps", "site-packages", "__pycache__"}

# Windows 记录"运行过的 exe"的两个地方，值的名字就是完整路径
_RUN_HISTORY = (
    r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache",
    r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Compatibility Assistant\Store",
)

# 全盘搜索：名字带这些词的文件夹先看，它的子文件夹也跟着优先
_HINTS = ("工具", "tool", "图吧", "tuba", "软件", "绿色", "portable", "装机", "维护",
          "硬盘", "磁盘", "分区", "disk", "partition", "常用", "下载", "download")
_SEARCH_DEPTH = 8
_SEARCH_SECONDS = 6.0
_SCORE_ROOT, _SCORE_KEYWORD, _SCORE_HINT, _SCORE_INHERIT = 1000.0, 100.0, 10.0, 0.6


def detect(program_id: str, deep: bool = False) -> dict:
    spec = PROGRAMS.get(program_id)
    if spec is None:
        return {"found": False, "error": f"不认识的程序：{program_id}"}
    steps = [("注册表 App Paths", _from_app_paths), ("注册表卸载信息", _from_uninstall),
             ("运行过的程序记录", _from_run_history), ("常见安装文件夹", _from_common_dirs)]
    if deep:
        steps += [("开始菜单和桌面快捷方式", _from_shortcuts), ("全盘搜索", _from_drives)]
    for how, finder in steps:
        path = finder(spec)
        if path:
            return {"found": True, "path": str(path), "how": how}
    return {"found": False, "tried": [how for how, _ in steps]}


def _exe_in(folder: Path, spec: dict) -> Path | None:
    for name in spec["exe"]:
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


def _matches(name: str, spec: dict) -> bool:
    lowered = name.lower()
    return any(k in lowered for k in spec["keywords"])


def _from_app_paths(spec: dict) -> Path | None:
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for name in spec["exe"]:
            try:
                with winreg.OpenKey(root, f"{_APP_PATHS}\\{name}") as key:
                    path = Path(winreg.QueryValue(key, None).strip('"'))
                    if path.is_file():
                        return path
            except OSError:
                continue
    return None


def _from_uninstall(spec: dict) -> Path | None:
    views = ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY), (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
             (winreg.HKEY_CURRENT_USER, 0))
    for root, view in views:
        try:
            parent = winreg.OpenKey(root, _UNINSTALL, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with parent:
            for index in range(winreg.QueryInfoKey(parent)[0]):
                try:
                    with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as key:
                        if not _matches(_value(key, "DisplayName"), spec):
                            continue
                        for field in ("InstallLocation", "DisplayIcon", "UninstallString"):
                            found = _exe_near(_value(key, field), spec)
                            if found:
                                return found
                except OSError:
                    continue
    return None


def _value(key, name: str) -> str:
    try:
        return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return ""


def _exe_near(raw: str, spec: dict) -> Path | None:
    """注册表里的值可能是文件夹，也可能是带引号、带参数的 exe 路径。"""
    raw = raw.strip().strip('"').split(",")[0]
    if not raw:
        return None
    path = Path(raw)
    folder = path if path.is_dir() else path.parent
    return _exe_in(folder, spec) if folder.is_dir() else None


def _fixed_drives() -> list[Path]:
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    drives = [f"{letter}:\\" for i, letter in enumerate(string.ascii_uppercase) if mask >> i & 1]
    return [Path(d) for d in drives if ctypes.windll.kernel32.GetDriveTypeW(d) == _DRIVE_FIXED]


def _subdirs(folder: Path) -> list[Path]:
    try:
        return [Path(e.path) for e in os.scandir(folder) if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []


def _from_common_dirs(spec: dict) -> Path | None:
    roots = [Path(os.environ[v]) for v in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432") if os.environ.get(v)]
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "Programs")
    for drive in _fixed_drives():
        roots += [drive / name for name in _COMMON_FOLDERS]
    for root in dict.fromkeys(roots):
        for folder in _subdirs(root):
            if _matches(folder.name, spec):
                found = _exe_in(folder, spec) or next((_exe_in(sub, spec) for sub in _subdirs(folder) if _exe_in(sub, spec)), None)
                if found:
                    return found
    return None


def _from_shortcuts(spec: dict) -> Path | None:
    """不走 COM，直接在 .lnk 的字节里找目标 exe 的路径。"""
    folders = [Path(os.environ.get(v, "")) / tail for v, tail in (
        ("APPDATA", r"Microsoft\Windows\Start Menu"), ("ProgramData", r"Microsoft\Windows\Start Menu"),
        ("USERPROFILE", "Desktop"), ("PUBLIC", "Desktop"))]
    names = "|".join(re.escape(n) for n in spec["exe"])
    pattern = re.compile(rf"[A-Za-z]:\\[^\x00:*?\"<>|\r\n]*?(?:{names})", re.IGNORECASE)
    for folder in folders:
        if not folder.is_dir():
            continue
        for link in folder.rglob("*.lnk"):
            try:
                data = link.read_bytes()
            except OSError:
                continue
            for text in (data.decode("utf-16-le", "ignore"), data.decode("mbcs", "ignore")):
                for match in pattern.findall(text):
                    if Path(match).is_file():
                        return Path(match)
    return None


def _from_run_history(spec: dict) -> Path | None:
    names = tuple(n.lower() for n in spec["exe"])
    for sub in _RUN_HISTORY:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            continue
        with key:
            for index in range(winreg.QueryInfoKey(key)[1]):
                try:
                    value = winreg.EnumValue(key, index)[0]
                except OSError:
                    continue
                # MuiCache 的名字形如 "E:\...\DiskGenius.exe.FriendlyAppName"
                lowered = value.lower()
                for name in names:
                    end = lowered.find(name)
                    if end != -1:
                        path = Path(value[:end + len(name)])
                        if path.name.lower() == name and path.is_file():
                            return path
    return None


def _score(name: str, spec: dict) -> float:
    lowered = name.lower()
    if any(k in lowered for k in spec["keywords"]):
        return _SCORE_KEYWORD
    return _SCORE_HINT if any(h in lowered for h in _HINTS) else 0.0


def _from_drives(spec: dict) -> Path | None:
    """按线索优先搜索所有本地磁盘：不要求文件夹叫什么名字，看到目标 exe 就算找到。"""
    names = {n.lower() for n in spec["exe"]}
    deadline = time.monotonic() + _SEARCH_SECONDS
    # 堆里是 (负的分数, 层数, 序号, 路径)，分数高的、浅的先看
    # 每个盘的根目录都先看一遍，免得一个盘里的大文件夹把别的盘挤到后面
    queue: list[tuple[float, int, int, str]] = [(-_SCORE_ROOT, 0, i, str(d)) for i, d in enumerate(_fixed_drives())]
    heapq.heapify(queue)
    counter = len(queue)
    while queue and time.monotonic() < deadline:
        negative, depth, _, folder = heapq.heappop(queue)
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if entry.name.lower() in names and entry.is_file():
                return Path(entry.path)
        if depth >= _SEARCH_DEPTH:
            continue
        for entry in entries:
            name = entry.name
            if name[0] in "$." or name.lower() in _SKIP_DIRS or not entry.is_dir(follow_symlinks=False):
                continue
            counter += 1
            inherited = 0.0 if depth == 0 else -negative * _SCORE_INHERIT
            score = max(_score(name, spec), inherited)
            heapq.heappush(queue, (-score, depth + 1, counter, entry.path))
    return None
