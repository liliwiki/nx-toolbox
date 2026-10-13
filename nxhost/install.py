"""安装和卸载：复制文件、快捷方式、在系统的"已安装的应用"里登记。

只写当前用户的位置（HKCU、用户自己的桌面和开始菜单），所以不需要管理员权限。
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import winreg
from ctypes import wintypes
from pathlib import Path

from nxhost import procs, version

APP_NAME = "NS 工具箱"
PUBLISHER = "LiliWiki"
FOLDER_NAME = "NXToolbox"
INSTALL_ENV = "NXTOOLBOX_INSTALL_DIR"      # 测试时换默认安装位置
ID_ENV = "NXTOOLBOX_INSTALL_ID"            # 测试时换登记用的名字，不碰真实的安装记录
INSTALL_ID = os.environ.get(ID_ENV) or FOLDER_NAME
SHORTCUT_NAME = APP_NAME if INSTALL_ID == FOLDER_NAME else f"{APP_NAME} ({INSTALL_ID})"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{INSTALL_ID}"
CSIDL_DESKTOPDIRECTORY, CSIDL_PROGRAMS = 0x10, 0x02
WAIT_SECONDS = 20
REMOVE_TRIES = 20
CREATE_NO_WINDOW = 0x08000000


def default_dir() -> Path:
    return Path(os.environ.get(INSTALL_ENV)
                or Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Programs" / FOLDER_NAME)


def normalize_target(picked: str) -> Path:
    """用户选的文件夹不直接当安装目录用，统一装进它下面的 NXToolbox，免得把别的东西换掉。"""
    path = Path(picked)
    return path if path.name.lower() == FOLDER_NAME.lower() else path / FOLDER_NAME


def installed() -> dict | None:
    """已经登记过的安装。程序文件不在了就当作没装。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            folder = Path(winreg.QueryValueEx(key, "InstallLocation")[0])
            flags = {name: bool(_value(key, name, 1)) for name in ("DesktopShortcut", "StartMenuShortcut")}
    except OSError:
        return None
    if not (folder / procs.EXE_NAME).is_file():
        return None
    return {"dir": str(folder), "version": version.read(folder).get("version", ""),
            "desktop": flags["DesktopShortcut"], "startmenu": flags["StartMenuShortcut"]}


def _value(key, name: str, default):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return default


def _special_folder(csidl: int) -> Path:
    buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer)
    return Path(buffer.value)


def shortcut_paths() -> dict[str, Path]:
    return {"desktop": _special_folder(CSIDL_DESKTOPDIRECTORY) / f"{SHORTCUT_NAME}.lnk",
            "startmenu": _special_folder(CSIDL_PROGRAMS) / f"{SHORTCUT_NAME}.lnk"}


def _create_shortcut(link: Path, target: Path) -> None:
    # 路径放在环境变量里传给 PowerShell，不用操心引号和中文
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:NX_LINK); "
              "$s.TargetPath = $env:NX_TARGET; $s.WorkingDirectory = $env:NX_DIR; "
              "$s.IconLocation = $env:NX_TARGET + ',0'; $s.Description = $env:NX_NAME; $s.Save()")
    env = {**os.environ, "NX_LINK": str(link), "NX_TARGET": str(target), "NX_DIR": str(target.parent), "NX_NAME": APP_NAME}
    link.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env, check=True,
                   capture_output=True, creationflags=CREATE_NO_WINDOW)


def check_target(target: Path) -> str | None:
    """返回不能装到这里的原因；可以就返回 None。"""
    if target.exists() and any(target.iterdir()) and not version.read(target):
        return "这个文件夹里已经有别的东西了，请换一个位置"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        probe = target.parent / f".nxtoolbox-write-test-{os.getpid()}"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError:
        return "这个位置不能写入，请换一个位置"
    return None


def copy_files(source: Path, target: Path, progress) -> None:
    """先复制到旁边的临时文件夹，成功后再整个换过去，中途失败不会留下半截的安装。"""
    staging = target.with_name(target.name + ".new")
    retired = target.with_name(target.name + ".old")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(retired, ignore_errors=True)
    files = [p for p in source.rglob("*") if p.is_file()]
    for index, path in enumerate(files, 1):
        dest = staging / path.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        progress(index / len(files))
    if target.exists():
        os.replace(target, retired)      # 旧版还开着的话这里会失败，旧版原样不动
    os.replace(staging, target)
    shutil.rmtree(retired, ignore_errors=True)


def register(target: Path, desktop: bool, startmenu: bool) -> None:
    """建快捷方式，并登记到"已安装的应用"，这样可以从系统设置里卸载。"""
    exe = target / procs.EXE_NAME
    for name, link in shortcut_paths().items():
        if {"desktop": desktop, "startmenu": startmenu}[name]:
            _create_shortcut(link, exe)
        else:
            link.unlink(missing_ok=True)
    size_kb = sum(p.stat().st_size for p in target.rglob("*") if p.is_file()) // 1024
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in (("DisplayName", APP_NAME), ("DisplayVersion", version.read(target).get("version", "")),
                            ("Publisher", PUBLISHER), ("InstallLocation", str(target)), ("DisplayIcon", str(exe)),
                            ("UninstallString", f'"{exe}" uninstall')):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name, value in (("EstimatedSize", size_kb), ("NoModify", 1), ("NoRepair", 1),
                            ("DesktopShortcut", int(desktop)), ("StartMenuShortcut", int(startmenu))):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)


def install(source: Path, target: Path, desktop: bool, startmenu: bool, progress) -> None:
    if not procs.wait_until_closed(target, WAIT_SECONDS):
        raise OSError("NS 工具箱还有窗口开着，请先全部关掉")
    copy_files(source, target, progress)
    register(target, desktop, startmenu)


def unregister() -> None:
    for link in shortcut_paths().values():
        link.unlink(missing_ok=True)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass


def remove_folder_after_exit(folder: Path) -> None:
    """卸载程序自己就在这个文件夹里运行，要等它退出之后才删得掉。"""
    # 每隔一秒试一次，删掉了就结束；最多试 REMOVE_TRIES 次。整条命令用字符串传，免得路径的引号被转义
    attempt = f'ping -n 2 127.0.0.1 >nul & rmdir /s /q "{folder}" 2>nul & if not exist "{folder}" exit /b 0'
    command = f'cmd /c "for /l %i in (1,1,{REMOVE_TRIES}) do ({attempt})"'
    subprocess.Popen(command, cwd=folder.parent, close_fds=True, creationflags=CREATE_NO_WINDOW)
