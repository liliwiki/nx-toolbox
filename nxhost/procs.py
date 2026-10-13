"""找出正在运行的工具箱进程。更新和安装时用来判断旧程序关掉了没有。"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from pathlib import Path

EXE_NAME = "NXToolbox.exe"
TH32CS_SNAPPROCESS = 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _Entry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


def running_from(folder: Path) -> list[int]:
    """正在从这个文件夹运行的工具箱进程（启动器和各个工具窗口），不含自己。"""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    snapshot = wintypes.HANDLE(kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0))
    entry = _Entry()
    entry.dwSize = ctypes.sizeof(_Entry)
    wanted = os.path.normcase(str(Path(folder, EXE_NAME)))
    found = []
    more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
    while more:
        pid = entry.th32ProcessID
        if entry.szExeFile.lower() == EXE_NAME.lower() and pid != os.getpid():
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                buffer = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(wintypes.HANDLE(handle), 0, buffer, ctypes.byref(size)):
                    if os.path.normcase(buffer.value) == wanted:
                        found.append(pid)
                kernel32.CloseHandle(wintypes.HANDLE(handle))
        more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    kernel32.CloseHandle(snapshot)
    return found


def wait_until_closed(folder: Path, seconds: float) -> bool:
    """等这个文件夹里的工具箱全部退出。到时间还没退完返回 False。"""
    deadline = time.monotonic() + seconds
    while running_from(folder):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.3)
    return True
