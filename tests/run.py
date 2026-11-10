"""跑全部测试：.venv\\Scripts\\python tests\\run.py

每个文件夹单独起一个进程跑。各工具都有自己的 core 包，放在同一个进程里会互相顶掉。
测试只碰临时文件夹，设置和数据目录也指到临时位置，不会改动真实的存储卡和设置。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GROUPS = ("nxhost", "swap", "partition")


def main() -> int:
    failed = []
    for group in GROUPS:
        print(f"\n===== {group} =====", flush=True)
        code = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(HERE / group), "-t", str(HERE / group),
                               *sys.argv[1:]]).returncode
        if code:
            failed.append(group)
    print("\n全部通过" if not failed else f"\n没通过：{'、'.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
