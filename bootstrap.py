"""单文件安装程序：用户下载到的那一个 exe。

它自带新版的全部文件。第一次运行、或者自带的版本比已安装的新时，解压到安装目录；
之后每次运行只是启动已经装好的程序，所以启动速度和直接运行一样。
1.0 的更新器会把它改名成 NS工具箱.exe 放在原来的位置，用起来和以前一样双击就行。
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from nxhost import version

INSTALL_ENV = "NXTOOLBOX_INSTALL_DIR"      # 测试时装到别处
INSTALL_DIR = Path(os.environ.get(INSTALL_ENV)
                   or Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Programs" / "NXToolbox")
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
PAYLOAD = "payload.zip"
EXE_NAME = "NXToolbox.exe"
MB_ICONERROR = 0x10


def needs_install() -> bool:
    installed = version.read(INSTALL_DIR).get("version")
    if not installed or not (INSTALL_DIR / EXE_NAME).is_file():
        return True
    return version.newer(version.read(BUNDLE_DIR).get("version", "0"), installed)


def install() -> None:
    """先解压到旁边的临时文件夹，成功后再整个换过去，中途失败不会留下半截的安装。"""
    staging = INSTALL_DIR.with_name(INSTALL_DIR.name + ".new")
    retired = INSTALL_DIR.with_name(INSTALL_DIR.name + ".old")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(retired, ignore_errors=True)
    staging.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BUNDLE_DIR / PAYLOAD) as bundle:
        bundle.extractall(staging)
    if INSTALL_DIR.exists():
        os.replace(INSTALL_DIR, retired)      # 旧版还开着的话这里会失败，旧版原样不动
    os.replace(staging, INSTALL_DIR)
    shutil.rmtree(retired, ignore_errors=True)


def main() -> None:
    try:
        if needs_install():
            install()
    except OSError as exc:
        ctypes.windll.user32.MessageBoxW(
            None, f"安装没有完成：{exc}\n\n如果 NS 工具箱的窗口还开着，请先全部关闭再试。", "NS 工具箱", MB_ICONERROR)
        if not (INSTALL_DIR / EXE_NAME).is_file():
            return
    subprocess.Popen([str(INSTALL_DIR / EXE_NAME), *sys.argv[1:]], cwd=INSTALL_DIR)


if __name__ == "__main__":
    main()
