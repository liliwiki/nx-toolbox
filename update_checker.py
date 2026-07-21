"""联网自动更新模块 - 版本检查、下载、启动时替换"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


def _parse_version(version_str: str) -> tuple[int, ...]:
    return tuple(int(x) for x in version_str.strip().split("."))


def _get_old_exe_path() -> str:
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "NS工具箱.exe")


def check_for_update(
    current_version: str,
    manifest_url: str,
    timeout: int = 15,
) -> dict | None:
    """检查是否有新版本可用。"""
    try:
        req = urllib.request.Request(manifest_url, headers={"User-Agent": "NXToolbox"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        logger.warning("检查更新失败: %s", e)
        return None

    remote_version = data.get("version", "")
    download_url = data.get("download_url", "")

    if not remote_version or not download_url:
        logger.warning("更新清单格式无效")
        return None

    try:
        if _parse_version(remote_version) <= _parse_version(current_version):
            return None
    except ValueError:
        logger.warning("版本号解析失败")
        return None

    return {
        "version": remote_version,
        "download_url": download_url,
        "changelog": data.get("changelog", ""),
        "release_date": data.get("release_date", ""),
    }


def download_update(
    download_url: str,
    save_path: str,
    progress_callback=None,
    timeout: int = 120,
) -> bool:
    """下载更新文件并校验，带重试机制。"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(download_url, headers={
                "User-Agent": "NXToolbox",
                "Accept-Encoding": "identity",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                with open(save_path, "wb") as f:
                    while True:
                        buf = resp.read(65536)
                        if not buf:
                            break
                        f.write(buf)
                        downloaded += len(buf)
                        if progress_callback and total > 0:
                            pct = int(downloaded * 100 / total)
                            dl_mb = downloaded / 1048576
                            tot_mb = total / 1048576
                            progress_callback(pct, dl_mb, tot_mb)
                if total > 0 and downloaded < total:
                    logger.warning("下载不完整: %d / %d bytes", downloaded, total)
                    _cleanup_file(save_path)
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    return False
            if not _is_valid_pe(save_path):
                logger.warning("下载的文件不是有效的 PE 可执行文件")
                _cleanup_file(save_path)
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return False
            return True
        except (urllib.error.URLError, OSError) as e:
            logger.warning("下载尝试 %d/%d 失败: %s", attempt + 1, max_retries, e)
            _cleanup_file(save_path)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return False


def _cleanup_file(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _write_changelog(text: str) -> None:
    try:
        local_appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        appdata_dir = os.path.join(local_appdata, "NXToolbox")
        os.makedirs(appdata_dir, exist_ok=True)
        path = os.path.join(appdata_dir, "update_changelog.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def get_pending_changelog() -> str | None:
    """读取待显示的更新内容，读取后删除文件。"""
    try:
        local_appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(local_appdata, "NXToolbox", "update_changelog.txt")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        os.remove(path)
        return text or None
    except OSError:
        return None


def _is_valid_pe(path: str) -> bool:
    """验证文件是否为有效的 PE 可执行文件（完整 PE 头校验）。"""
    try:
        if os.path.getsize(path) < 1024:
            return False
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return False
            f.seek(0x3C)
            pe_offset_bytes = f.read(4)
            if len(pe_offset_bytes) < 4:
                return False
            pe_offset = int.from_bytes(pe_offset_bytes, "little")
            if pe_offset < 64 or pe_offset > 4096:
                return False
            f.seek(pe_offset)
            if f.read(4) != b"PE\x00\x00":
                return False
            machine = f.read(2)
            if machine not in (b"\x4c\x01", b"\x64\x86"):  # x86 or x64
                return False
        return True
    except OSError:
        return False


def install_update(new_exe_path: str, old_exe_path: str, changelog: str = "") -> bool:
    """
    一键更新：下载 → 校验 → 立刻替换 → 自重启。

    用户看到：进度条 → 闪一下 → 新版本运行（零手动操作）。
    旧版本保留为 .old 备份，下次成功启动后自动清理。
    """
    if not os.path.exists(new_exe_path):
        logger.warning("新版本文件不存在: %s", new_exe_path)
        return False

    if changelog:
        _write_changelog(changelog)

    # 将新 exe 移到程序目录
    app_dir = os.path.dirname(old_exe_path)
    pending_exe = os.path.join(app_dir, "NS工具箱_new.exe")

    try:
        if os.path.exists(pending_exe):
            os.remove(pending_exe)
        shutil.move(new_exe_path, pending_exe)
    except OSError:
        try:
            shutil.copy2(new_exe_path, pending_exe)
            os.remove(new_exe_path)
        except OSError as e:
            logger.warning("移动更新文件失败: %s", e)
            return False

    if not _is_valid_pe(pending_exe):
        logger.warning("新 exe PE 校验失败，已删除")
        _cleanup_file(pending_exe)
        return False

    # === 启动 updater.exe → 退出（rename/清理/启动全由 updater 接管） ===
    logger.info("开始更新...")
    try:
        app_dir = os.path.dirname(old_exe_path)

        # 1. 确保 updater.exe 可用（没有就从服务器下载）
        updater_exe = os.path.join(app_dir, "updater.exe")
        if not os.path.exists(updater_exe):
            updater_url = "https://download.liliwiki.com/toolbox/updater.exe"
            logger.info("下载 updater.exe: %s", updater_url)
            import tempfile
            tmp = os.path.join(tempfile.gettempdir(), "updater_tmp.exe")
            if not download_update(updater_url, tmp):
                raise RuntimeError("下载 updater.exe 失败")
            shutil.move(tmp, updater_exe)

        # 2. 新 exe 已在 pending_exe 路径（程序目录下 NS工具箱_new.exe）
        #    updater 接管后续：等旧进程死 → rename → 清理 → 启动

        # 3. 启动 updater（传参：旧PID 旧exe路径 新exe路径 旧_MEI）
        subprocess.Popen(
            [updater_exe,
             str(os.getpid()),
             old_exe_path,
             pending_exe,
             getattr(sys, "_MEIPASS", "")],
            creationflags=subprocess.DETACHED_PROCESS if os.name == "nt" else 0,
            close_fds=True,
        )

        # 4. 退出
        os._exit(0)
    except OSError as e:
        logger.warning("更新失败: %s", e)
        return False


def try_cleanup_artifacts() -> bool:
    """兜底清理：批处理应该已经做完了，这里只处理遗漏。"""
    exe_path = _get_old_exe_path()
    app_dir = os.path.dirname(exe_path)
    temp_dir = os.environ.get("TEMP", "")
    current_mei = getattr(sys, "_MEIPASS", "")

    # 1. 删 updater.exe（updater 已完成任务，新进程清理它）
    updater_exe = os.path.join(app_dir, "updater.exe")
    if os.path.exists(updater_exe) and os.path.normcase(updater_exe) != os.path.normcase(sys.executable):
        for _ in range(5):
            try:
                os.remove(updater_exe)
                break
            except OSError:
                time.sleep(0.3)

    # 2. 兜底：删 .old（updater 应该已经删了）
    old_backup = exe_path + ".old"
    if os.path.exists(old_backup) and os.path.normcase(old_backup) != os.path.normcase(sys.executable):
        for _ in range(3):
            try:
                os.remove(old_backup)
                break
            except OSError:
                time.sleep(0.3)

    # 3. 兜底：清旧 _MEI*
    import glob
    if temp_dir:
        for mei_path in glob.glob(os.path.join(temp_dir, "_MEI*")):
            if os.path.isdir(mei_path) and os.path.normcase(mei_path) != os.path.normcase(current_mei):
                try:
                    shutil.rmtree(mei_path, ignore_errors=True)
                except OSError:
                    pass

    # 4. 兜底：删残留文件
    for fname in ("NS工具箱_new.exe", "nx_update.bat"):
        fp = os.path.join(app_dir, fname)
        if os.path.exists(fp) and os.path.normcase(fp) != os.path.normcase(sys.executable):
            try:
                os.remove(fp)
            except OSError:
                pass

    if temp_dir:
        for fname in ("nx_update.bat", "nx_cleanup.bat", "nx_cleanup.vbs"):
            fp = os.path.join(temp_dir, fname)
            if os.path.exists(fp) and os.path.normcase(fp) != os.path.normcase(sys.executable):
                try:
                    os.remove(fp)
                except OSError:
                    pass

    return True
