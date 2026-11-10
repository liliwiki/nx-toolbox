"""测试共用：把程序目录加进导入路径，数据目录指到临时文件夹，按需加载某个工具。

每个测试文件开头 `import support`（tests/run.py 会把这个文件夹加进路径）。
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 设置、日志、快照都写到临时位置，不碰真实的 %LOCALAPPDATA%\NXToolbox
DATA = Path(tempfile.mkdtemp(prefix="nxtoolbox_test_"))
os.environ["LOCALAPPDATA"] = str(DATA)
atexit.register(shutil.rmtree, DATA, ignore_errors=True)


def temp_dir(test, prefix: str) -> Path:
    """测试用的临时文件夹，测试结束自动删掉。"""
    folder = Path(tempfile.mkdtemp(prefix=prefix))
    test.addCleanup(shutil.rmtree, folder, True)
    return folder


def load_tool(tool_id: str):
    """像真实运行时一样加载一个工具的接口对象。一个进程里只加载一个工具。"""
    import host
    from nxhost.api import Context
    tool = next(t for t in host.read_tools() if t["id"] == tool_id)
    return host.load_tool_api(tool, Context(base=host.BASE, frozen=False, process_t0=time.perf_counter(), tool=tool))


def write(root: Path, rel: str, data: bytes | str = b"x") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return path
