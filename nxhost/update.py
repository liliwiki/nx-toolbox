"""工具箱自身的在线更新。

对外只发布一个 exe（单文件安装程序），更新也用它：
读更新清单 → 下载新的 exe 并核对 SHA-256 → 关掉自己 → 由新的 exe 把安装目录换成新版并启动。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

from nxhost import shared, version

# 和 1.0 用同一份清单文件：1.0 读最外层的字段，2.0 读 "v2" 这一节
MANIFEST_URL = "https://download.liliwiki.com/toolbox/version.json"
MANIFEST_ENV = "NXTOOLBOX_UPDATE_URL"      # 测试时指向本地的清单
MANIFEST_SECTION = "v2"
UPDATE_DIR = shared.DATA_DIR / "update"
INSTALLER_NAME = "NXToolbox-update.exe"
USER_AGENT = "NXToolbox/2"
DOWNLOAD_BLOCK = 256 * 1024


class UpdateError(Exception):
    pass


def manifest_url() -> str:
    return os.environ.get(MANIFEST_ENV) or MANIFEST_URL


def check(current: str, timeout: float = 10) -> dict:
    """返回 {"current", "latest", "newer", "notes", "date", "url", "sha256", "size"}。"""
    req = urllib.request.Request(manifest_url(), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        manifest = json.loads(resp.read().decode("utf-8-sig"))[MANIFEST_SECTION]
    latest = manifest["version"]
    return {"current": current, "latest": latest, "newer": version.newer(latest, current),
            "notes": manifest.get("notes", ""), "date": manifest.get("date", ""),
            "url": manifest["url"], "sha256": manifest["sha256"].lower(), "size": int(manifest.get("size", 0))}


def download(info: dict, progress) -> Path:
    """下载新版的 exe 并核对校验值。progress(已下载, 总大小)。"""
    shutil.rmtree(UPDATE_DIR, ignore_errors=True)
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    target = UPDATE_DIR / INSTALLER_NAME
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
        raise UpdateError("下载的更新校验不通过，可能没下完整，请重试")
    return target


def launch_installer(installer: Path, target: Path) -> None:
    """让新的 exe 来替换安装目录。调用后当前进程应当立即退出。"""
    subprocess.Popen([str(installer), "--install-to", str(target), "--wait"], cwd=installer.parent,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)


def cleanup() -> None:
    """启动器启动时清掉上次更新下载的文件。它可能还没完全退出，删不掉就留到下次。"""
    shutil.rmtree(UPDATE_DIR, ignore_errors=True)
