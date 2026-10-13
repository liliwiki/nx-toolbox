"""安装程序：对外发布的就是这一个 exe，它自带新版的全部文件。

- 没装过，或者自带的比已装的新：打开安装界面（选位置、快捷方式），装好后可以从系统设置里卸载。
- 已经装好了同一个版本：打开装好的程序的卸载界面（上面也能直接打开工具箱）。平时用快捷方式打开。
- 带 --install-to 运行：在线更新用，不显示界面，等旧程序退出后把那个目录换成新版再启动。

1.0 的更新器会把它改名成 NS工具箱.exe 放在原来的位置，所以装好之后再双击它，界面上也要有打开工具箱的按钮。
"""

from __future__ import annotations

import argparse
import ctypes
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from nxhost import install, procs, version

BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
PAYLOAD = "payload.zip"
MB_ICONERROR = 0x10


def is_current(target: Path) -> bool:
    """装好的这一份是不是和自带的一样新（同版本号、同一次打包）。"""
    installed, bundled = version.read(target), version.read(BUNDLE_DIR)
    if not installed.get("version") or not (target / procs.EXE_NAME).is_file():
        return False
    if version.newer(bundled.get("version", "0"), installed["version"]):
        return False
    return not (bundled.get("version") == installed["version"] and bundled.get("build") != installed.get("build"))


def unpack() -> Path:
    folder = Path(tempfile.mkdtemp(prefix="NXToolboxSetup-"))
    with zipfile.ZipFile(BUNDLE_DIR / PAYLOAD) as bundle:
        bundle.extractall(folder)
    return folder


def silent_update(target: Path, passthrough: list[str]) -> None:
    """在线更新：不问任何问题，保持原来的快捷方式设置。"""
    source = unpack()
    try:
        existing = install.installed() or {}
        install.install(source, target, existing.get("desktop", True), existing.get("startmenu", True), lambda _f: None)
    except OSError as exc:
        ctypes.windll.user32.MessageBoxW(
            None, f"更新没有完成：{exc}\n\n原来的版本没有被改动，可以照常使用。", install.APP_NAME, MB_ICONERROR)
    finally:
        shutil.rmtree(source, ignore_errors=True)
    if (target / procs.EXE_NAME).is_file():
        subprocess.Popen([str(target / procs.EXE_NAME), *passthrough], cwd=target)


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--install-to", help="在线更新：要换成新版的安装目录")
    parser.add_argument("--wait", action="store_true", help="保留给旧版本的调用，现在总是会等旧程序退出")
    args, passthrough = parser.parse_known_args()
    if args.install_to:
        silent_update(Path(args.install_to), passthrough)
        return

    existing = install.installed()
    target = Path(existing["dir"]) if existing else install.default_dir()
    if is_current(target):
        subprocess.Popen([str(target / procs.EXE_NAME), "uninstall", "--manage"], cwd=target)
        return
    # 用解压出来的新程序自己来显示安装界面，装完它会把自己复制到安装位置
    source = unpack()
    try:
        subprocess.run([str(source / procs.EXE_NAME), "setup", "--source", str(source)], cwd=source)
    finally:
        shutil.rmtree(source, ignore_errors=True)


if __name__ == "__main__":
    main()
