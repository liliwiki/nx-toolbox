"""打包并自测，产物放在 release/。

    .venv\\Scripts\\python build.py              完整打包
    .venv\\Scripts\\python build.py --no-build   只重新自测已有的 dist

产物：
    dist/NXToolbox/                         解压即用的程序文件夹
    release/NXToolbox-<版本>.zip            在线更新用的压缩包
    release/NXToolbox-<版本>-setup.exe      用户下载的单文件安装程序
    release/version.json                    2.0 的更新清单
    release/v1-version.json                 给 1.0 看的更新清单（让 1.0 提示升级）
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist" / "NXToolbox"
EXE = DIST / "NXToolbox.exe"
RELEASE = ROOT / "release"
WORK = ROOT / "build"
OUTSIDE = ("launcher", "ui", "tools", "app.json")   # 不打进 exe，放在旁边，之后可以单独替换
DOWNLOAD_BASE = "https://download.liliwiki.com/toolbox/v2/"
SELFTEST_TIMEOUT = 60
RUNS = 3

APP = json.loads((ROOT / "app.json").read_text(encoding="utf-8"))
VERSION = APP["version"]
ZIP_NAME = f"NXToolbox-{VERSION}.zip"
SETUP_NAME = f"NXToolbox-{VERSION}-setup.exe"

# PyInstaller 只带宿主用到的标准库。工具是之后才加载的，所以要把整个标准库带上。
STDLIB_SKIP = {"tkinter", "idlelib", "test", "lib2to3", "turtle", "turtledemo", "ensurepip", "venv", "distutils",
               "pydoc_data", "antigravity", "this"}


def pyinstaller(*args: str) -> None:
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
                    "--workpath", str(WORK), "--specpath", str(WORK), *args], check=True)


def build_app() -> None:
    names = sorted(n for n in sys.stdlib_module_names if n not in STDLIB_SKIP and not n.startswith("_"))
    stdlib = [arg for name in names for arg in ("--hidden-import", name, "--collect-submodules", name)]
    pyinstaller("--onedir", "--name", "NXToolbox", "--distpath", str(ROOT / "dist"), *stdlib, str(ROOT / "host.py"))
    for name in OUTSIDE:
        source, target = ROOT / name, DIST / name
        if source.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, target)


def build_zip() -> Path:
    RELEASE.mkdir(exist_ok=True)
    target = RELEASE / ZIP_NAME
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(DIST.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                bundle.write(path, path.relative_to(DIST).as_posix())
    return target


def build_setup(payload: Path) -> Path:
    """单文件安装程序：把更新包整个装在里面。"""
    staged = WORK / "payload.zip"
    WORK.mkdir(exist_ok=True)
    shutil.copy2(payload, staged)
    pyinstaller("--onefile", "--name", SETUP_NAME.removesuffix(".exe"), "--distpath", str(RELEASE),
                "--add-data", f"{staged};.", "--add-data", f"{ROOT / 'app.json'};.", str(ROOT / "bootstrap.py"))
    return RELEASE / SETUP_NAME


def write_manifests(payload: Path) -> None:
    notes = (ROOT / "release-notes.txt").read_text(encoding="utf-8").strip()
    today = time.strftime("%Y-%m-%d")
    manifest = {"version": VERSION, "channel": APP.get("channel", "stable"), "date": today, "notes": notes,
                "url": DOWNLOAD_BASE + ZIP_NAME, "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
                "size": payload.stat().st_size}
    (RELEASE / "version.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # 1.0 只认纯数字的版本号，所以这里不带 preview 后缀
    legacy = {"version": VERSION.split("-")[0], "download_url": DOWNLOAD_BASE + SETUP_NAME,
              "changelog": f"{VERSION}\n\n{notes}", "release_date": today}
    (RELEASE / "v1-version.json").write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")


def folder_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1048576


def selftest(*args: str) -> dict:
    """启动一次，等页面就绪后自己退出，返回耗时。"""
    out = Path(tempfile.gettempdir()) / f"nxtoolbox_selftest_{time.time_ns()}.json"
    subprocess.run([str(EXE), *args, "--selftest", str(out)], timeout=SELFTEST_TIMEOUT, check=False)
    if not out.exists():
        return {"error": "窗口没有就绪"}
    info = json.loads(out.read_text(encoding="utf-8"))
    out.unlink()
    return info


def main() -> None:
    if "--no-build" not in sys.argv:
        build_app()
        payload = build_zip()
        build_setup(payload)
        write_manifests(payload)
    print(f"\n版本 {VERSION}")
    print(f"程序文件夹 {folder_mb(DIST):.1f} MB")
    for path in sorted(RELEASE.glob("*")):
        print(f"  release/{path.name}  {path.stat().st_size / 1048576:.1f} MB" if path.suffix in (".zip", ".exe") else f"  release/{path.name}")
    for label, args in (("启动器", ()), ("换包", ("run", "swap"))):
        runs = [selftest(*args) for _ in range(RUNS)]
        print(f"{label}：进程启动到页面就绪 {[r.get('since_process_ms', r.get('error')) for r in runs]} 毫秒")


if __name__ == "__main__":
    main()
