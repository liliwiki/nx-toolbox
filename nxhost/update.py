"""工具箱自身的在线更新。

流程：读更新清单 → 下载压缩包并核对 SHA-256 → 解压到暂存目录 →
用暂存目录里的新程序来替换安装目录（正在运行的程序不能替换自己）→ 启动新版。
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.request
import zipfile
from ctypes import wintypes
from pathlib import Path

from nxhost import shared, version

MANIFEST_URL = "https://download.liliwiki.com/toolbox/v2/version.json"
MANIFEST_ENV = "NXTOOLBOX_UPDATE_URL"      # 测试时指向本地的清单
EXE_NAME = "NXToolbox.exe"
UPDATE_DIR = shared.DATA_DIR / "update"
STAGING_DIR = UPDATE_DIR / "staging"
BACKUP_NAME = ".update-backup"
USER_AGENT = "NXToolbox/2"
DOWNLOAD_BLOCK = 256 * 1024
WAIT_SECONDS = 20
MB_ICONERROR = 0x10
TH32CS_SNAPPROCESS = 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class UpdateError(Exception):
    pass


def manifest_url() -> str:
    return os.environ.get(MANIFEST_ENV) or MANIFEST_URL


def check(current: str, timeout: float = 10) -> dict:
    """返回 {"current", "latest", "newer", "notes", "date", "url", "sha256", "size"}。"""
    req = urllib.request.Request(manifest_url(), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        manifest = json.loads(resp.read().decode("utf-8-sig"))
    latest = manifest["version"]
    return {"current": current, "latest": latest, "newer": version.newer(latest, current),
            "notes": manifest.get("notes", ""), "date": manifest.get("date", ""),
            "url": manifest["url"], "sha256": manifest["sha256"].lower(), "size": int(manifest.get("size", 0))}


def download(info: dict, progress) -> Path:
    """下载更新包并核对校验值。progress(已下载, 总大小)。"""
    shutil.rmtree(UPDATE_DIR, ignore_errors=True)
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    target = UPDATE_DIR / "update.zip"
    digest = hashlib.sha256()
    req = urllib.request.Request(info["url"], headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(target, "wb") as out:
        total = int(resp.headers.get("Content-Length") or info["size"] or 0)
        done = 0
        while block := resp.read(DOWNLOAD_BLOCK):
            out.write(block)
            digest.update(block)
            done += len(block)
            progress(done, total)
    if digest.hexdigest() != info["sha256"]:
        target.unlink(missing_ok=True)
        raise UpdateError("下载的更新包校验不通过，可能没下完整，请重试")
    return target


def stage(archive: Path) -> Path:
    """解压到暂存目录，返回里面装着新程序的那个文件夹。"""
    shutil.rmtree(STAGING_DIR, ignore_errors=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(STAGING_DIR)
    exe = next(STAGING_DIR.rglob(EXE_NAME), None)
    if exe is None or not (exe.parent / version.APP_FILE).is_file():
        raise UpdateError("更新包里没有找到新版程序")
    return exe.parent


def running_from(folder: Path, exclude: int = 0) -> list[int]:
    """正在从这个文件夹运行的工具箱进程（启动器和各个工具窗口）。"""
    kernel32 = ctypes.windll.kernel32

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    entry = Entry()
    entry.dwSize = ctypes.sizeof(Entry)
    wanted = os.path.normcase(str(Path(folder, EXE_NAME)))
    found = []
    more = kernel32.Process32FirstW(wintypes.HANDLE(snapshot), ctypes.byref(entry))
    while more:
        pid = entry.th32ProcessID
        if entry.szExeFile.lower() == EXE_NAME.lower() and pid not in (exclude, os.getpid()):
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                buffer = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(wintypes.HANDLE(handle), 0, buffer, ctypes.byref(size)):
                    if os.path.normcase(buffer.value) == wanted:
                        found.append(pid)
                kernel32.CloseHandle(wintypes.HANDLE(handle))
        more = kernel32.Process32NextW(wintypes.HANDLE(snapshot), ctypes.byref(entry))
    kernel32.CloseHandle(wintypes.HANDLE(snapshot))
    return found


def launch_applier(staged: Path, target: Path) -> None:
    """用暂存目录里的新程序去替换安装目录，然后当前进程应当立即退出。"""
    subprocess.Popen([str(staged / EXE_NAME), "apply-update", "--target", str(target)], cwd=staged,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)


def apply(target: Path, source: Path) -> None:
    """在暂存目录的新程序里运行：等旧程序退出，换掉安装目录里的文件，启动新版。"""
    try:
        deadline = time.monotonic() + WAIT_SECONDS
        while running_from(target) and time.monotonic() < deadline:
            time.sleep(0.3)
        if running_from(target):
            raise UpdateError("工具箱还有窗口开着，没有更新。请全部关闭后重新检查更新。")
        _replace(target, source)
    except (UpdateError, OSError) as exc:
        ctypes.windll.user32.MessageBoxW(None, f"更新没有完成：{exc}\n\n原来的版本没有被改动，可以照常使用。", "NS 工具箱", MB_ICONERROR)
    subprocess.Popen([str(target / EXE_NAME)], cwd=target)


def _replace(target: Path, source: Path) -> None:
    """逐项换成新的。任何一步失败就把已经换掉的放回去。"""
    backup = target / BACKUP_NAME
    shutil.rmtree(backup, ignore_errors=True)
    backup.mkdir()
    moved: list[str] = []
    try:
        for item in source.iterdir():
            old = target / item.name
            if old.exists():
                os.replace(old, backup / item.name)
                moved.append(item.name)
            shutil.copytree(item, old) if item.is_dir() else shutil.copy2(item, old)
    except OSError:
        for name in moved:
            current = target / name
            shutil.rmtree(current, ignore_errors=True) if current.is_dir() else current.unlink(missing_ok=True)
            os.replace(backup / name, current)
        shutil.rmtree(backup, ignore_errors=True)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def cleanup(base: Path) -> None:
    """启动器启动时清掉上次更新留下的下载和暂存文件。"""
    if STAGING_DIR in Path(base).parents or Path(base) == STAGING_DIR:
        return
    shutil.rmtree(UPDATE_DIR, ignore_errors=True)
    shutil.rmtree(Path(base) / BACKUP_NAME, ignore_errors=True)

