"""版本号：读取、比较。启动器、更新和单文件安装程序都用它，所以不依赖别的模块。"""

from __future__ import annotations

import json
import re
from pathlib import Path

APP_FILE = "app.json"
_RELEASE = 1      # 正式版排在同号的预览版后面：2.0.0-preview.9 < 2.0.0


def parse(version: str) -> tuple:
    """"2.0.0-preview.1" → ((2, 0, 0), 0, 1)；"2.0.0" → ((2, 0, 0), 1, 0)。"""
    core, _, pre = version.strip().partition("-")
    numbers = tuple(int(x) for x in core.split("."))
    if not pre:
        return numbers, _RELEASE, 0
    serial = re.search(r"(\d+)$", pre)
    return numbers, 0, int(serial.group(1)) if serial else 0


def newer(candidate: str, current: str) -> bool:
    try:
        return parse(candidate) > parse(current)
    except ValueError:
        return False


def read(folder: Path) -> dict:
    """读程序文件夹里的 app.json；没有或读不了就当作没安装。"""
    try:
        return json.loads(Path(folder, APP_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
