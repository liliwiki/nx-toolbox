"""界面要靠 Microsoft Edge WebView2 运行库显示。Windows 10 LTSC 这类精简系统没有它，打开就是一片白。

所以在打开任何窗口之前先查：没有就用系统自带的提示框说明，问要不要装；要装就用浏览器打开微软官方的下载地址，
用户自己运行下载下来的安装引导程序，装好后再打开工具箱。

程序不再随带微软的安装引导程序：它是闭源组件，随包分发会挡住开源代码签名，自己下载再运行又像病毒的行为。
"""

from __future__ import annotations

import ctypes
import os
import webbrowser
from pathlib import Path

from nxhost import shared

# 微软官方的常青版安装引导程序直链（WebView2 分发文档里给的地址），浏览器打开就开始下载
BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
TITLE = "NS 工具箱"
MB_YESNO, MB_OK, MB_ICONINFO, MB_TOPMOST = 0x4, 0x0, 0x40, 0x40000
IDYES = 6
TEST_MISSING = "NXTOOLBOX_ASSUME_NO_WEBVIEW2"        # 测试用：假装没装


def installed() -> bool:
    if os.environ.get(TEST_MISSING) == "1":
        return False
    return shared.webview2_version() is not None


def _box(text: str, flags: int) -> int:
    return ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags | MB_TOPMOST)


def ensure(_base: Path) -> bool:
    """有 WebView2 返回 True；没有就引导去微软官网下载，返回 False（已经告诉用户怎么办了）。"""
    if installed():
        return True
    answer = _box("这台电脑缺少「Microsoft Edge WebView2 运行库」，NS 工具箱的界面要靠它才能显示。\n\n"
                  "点「是」会用浏览器从微软官网下载安装程序（MicrosoftEdgeWebview2Setup.exe），"
                  "下载好后运行它，装完再打开 NS 工具箱。", MB_YESNO | MB_ICONINFO)
    if answer == IDYES:
        webbrowser.open(BOOTSTRAPPER_URL)
    else:
        _box("没有安装 WebView2 运行库，NS 工具箱打不开。\n\n以后想用时再打开 NS 工具箱，会再问一次。", MB_OK | MB_ICONINFO)
    return False
