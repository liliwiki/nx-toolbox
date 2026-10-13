"""单文件安装程序：对外发布的就是这一个 exe。

它自带新版的全部文件。第一次运行、或者自带的比已安装的新时，解压到安装目录；
之后每次运行只是启动已经装好的程序，所以启动速度和直接运行一样。
在线更新也是下载它：带 --install-to 运行时，它等旧程序退出，把那个目录换成新版再启动。
1.0 的更新器会把它改名成 NS工具箱.exe 放在原来的位置，用起来和以前一样双击就行。
"""

from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from nxhost import procs, version

INSTALL_ENV = "NXTOOLBOX_INSTALL_DIR"      # 测试时装到别处
DEFAULT_DIR = Path(os.environ.get(INSTALL_ENV)
                   or Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Programs" / "NXToolbox")
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
PAYLOAD = "payload.zip"
WAIT_SECONDS = 20
MB_ICONERROR = 0x10


def needs_install(target: Path) -> bool:
    installed, bundled = version.read(target), version.read(BUNDLE_DIR)
    if not installed.get("version") or not (target / procs.EXE_NAME).is_file():
        return True
    if version.newer(bundled.get("version", "0"), installed["version"]):
        return True
    # 同一个版本号重新打过包，也换成自带的这一份
    return bundled.get("version") == installed["version"] and bundled.get("build") != installed.get("build")


def install(target: Path) -> None:
    """先解压到旁边的临时文件夹，成功后再整个换过去，中途失败不会留下半截的安装。"""
    staging = target.with_name(target.name + ".new")
    retired = target.with_name(target.name + ".old")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(retired, ignore_errors=True)
    staging.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BUNDLE_DIR / PAYLOAD) as bundle:
        bundle.extractall(staging)
    if target.exists():
        os.replace(target, retired)      # 旧版还开着的话这里会失败，旧版原样不动
    os.replace(staging, target)
    shutil.rmtree(retired, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--install-to", help="在线更新：要换成新版的安装目录")
    parser.add_argument("--wait", action="store_true", help="先等那个目录里的旧程序退出")
    args, passthrough = parser.parse_known_args()
    target = Path(args.install_to) if args.install_to else DEFAULT_DIR
    try:
        if args.wait and not procs.wait_until_closed(target, WAIT_SECONDS):
            raise OSError("工具箱还有窗口开着")
        if needs_install(target):
            install(target)
    except OSError as exc:
        ctypes.windll.user32.MessageBoxW(
            None, f"安装没有完成：{exc}\n\n请把 NS 工具箱的窗口全部关掉再试。原来的版本没有被改动。", "NS 工具箱", MB_ICONERROR)
        if not (target / procs.EXE_NAME).is_file():
            return
    subprocess.Popen([str(target / procs.EXE_NAME), *passthrough], cwd=target)


if __name__ == "__main__":
    main()
