"""界面要靠 Microsoft Edge WebView2 运行库显示。Windows 10 LTSC 这类精简系统没有它，打开就是一片白。

所以在打开任何窗口之前先查：没有就用系统自带的提示框说明，问要不要装；要装就运行随程序带着的微软安装引导程序
（vendor/MicrosoftEdgeWebview2Setup.exe，它会联网下载运行库，自己会显示进度），装好再继续。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import webbrowser
from pathlib import Path

from nxhost import shared

BOOTSTRAPPER = "MicrosoftEdgeWebview2Setup.exe"
DOWNLOAD_PAGE = "https://developer.microsoft.com/microsoft-edge/webview2/"
TITLE = "NS 工具箱"
MB_YESNO, MB_OK, MB_ICONINFO, MB_ICONERROR, MB_TOPMOST = 0x4, 0x0, 0x40, 0x10, 0x40000
IDYES = 6
TEST_MISSING = "NXTOOLBOX_ASSUME_NO_WEBVIEW2"        # 测试用：假装没装


def installed() -> bool:
    if os.environ.get(TEST_MISSING) == "1":
        return False
    return shared.webview2_version() is not None


def _box(text: str, flags: int) -> int:
    return ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags | MB_TOPMOST)


def ensure(base: Path) -> bool:
    """有 WebView2 返回 True；没有就引导安装，装上了也返回 True，没装上返回 False（已经告诉用户怎么办了）。"""
    if installed():
        return True
    setup = Path(base) / "vendor" / BOOTSTRAPPER
    answer = _box("这台电脑缺少「Microsoft Edge WebView2 运行库」，NS 工具箱的界面要靠它才能显示。\n\n"
                  "现在安装吗？会打开微软的安装程序，需要联网，一般一两分钟就好。", MB_YESNO | MB_ICONINFO)
    if answer != IDYES:
        _box("没有安装 WebView2 运行库，NS 工具箱打不开。\n\n以后想用时再打开 NS 工具箱，会再问一次。", MB_OK | MB_ICONINFO)
        return False
    if setup.is_file():
        try:
            subprocess.run([str(setup), "/install"], check=False)       # 需要管理员权限的话它自己会弹授权
        except OSError:
            pass
    if installed():
        return True
    _box("WebView2 运行库没有装上。\n\n点「确定」会打开微软的下载页面，选「常青版独立安装程序」（Evergreen Standalone Installer）"
         "下载安装，装好后再打开 NS 工具箱。", MB_OK | MB_ICONERROR)
    webbrowser.open(DOWNLOAD_PAGE)
    return False
