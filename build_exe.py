"""PyInstaller 打包脚本 — 构建 updater.exe + NS工具箱.exe，输出到 out/"""

import os
import shutil
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(PROJECT_DIR, "out")


def clean_all() -> None:
    """清理所有中间产物。"""
    targets = [
        "build", "dist", "__pycache__",
        os.path.join("sd_cleaner", "__pycache__"),
    ]
    for path in targets:
        if os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)
    for spec in [f for f in os.listdir(".") if f.endswith(".spec")]:
        try:
            os.remove(spec)
        except OSError:
            pass


def build_updater() -> None:
    """构建 updater.exe → out/"""
    print("=" * 50)
    print("构建 updater.exe ...")
    clean_all()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--windowed",
        "--name", "updater",
        "--clean",
        "updater.py",
    ]
    if subprocess.run(cmd).returncode != 0:
        print("updater.exe 构建失败！")
        sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    src = os.path.join("dist", "updater.exe")
    shutil.copy2(src, os.path.join(OUT_DIR, "updater.exe"))
    print(f"  -> out/updater.exe ({os.path.getsize(src) / 1048576:.0f} MB)")


def build_main() -> None:
    """构建 NS工具箱.exe → out/"""
    print("=" * 50)
    print("构建 NS工具箱.exe ...")
    clean_all()

    if not os.path.isdir("7z"):
        print("错误: 7z/ 不存在")
        sys.exit(1)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--windowed",
        "--name", "NS工具箱",
        "--icon", "icon.ico",
        "--add-data", f"7z{os.pathsep}7z",
        "--add-data", f"icon.ico{os.pathsep}.",
        "--add-data", f"version.json{os.pathsep}.",
        "--hidden-import", "SD_Cleaner",
        "--hidden-import", "update_checker",
        "--hidden-import", "sd_cleaner",
        "--hidden-import", "sd_cleaner.app",
        "--hidden-import", "sd_cleaner.common",
        "--hidden-import", "sd_cleaner.sd_manager",
        "--hidden-import", "sd_cleaner.archive",
        "--hidden-import", "sd_cleaner.package",
        "--hidden-import", "sd_cleaner.firmware",
        "--hidden-import", "sd_cleaner.snapshot",
        "--hidden-import", "sd_cleaner.version_detector",
        "--hidden-import", "sd_cleaner.op_logger",
        "--clean",
        "nx_toolbox.py",
    ]
    if subprocess.run(cmd).returncode != 0:
        print("NS工具箱.exe 构建失败！")
        sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    src = os.path.join("dist", "NS工具箱.exe")
    shutil.copy2(src, os.path.join(OUT_DIR, "NS工具箱.exe"))
    print(f"  -> out/NS工具箱.exe ({os.path.getsize(src) / 1048576:.0f} MB)")


def main() -> None:
    os.chdir(PROJECT_DIR)
    os.makedirs(OUT_DIR, exist_ok=True)

    build_updater()
    build_main()
    clean_all()

    print("=" * 50)
    print("构建完成！")
    for f in os.listdir(OUT_DIR):
        path = os.path.join(OUT_DIR, f)
        print(f"  out/{f} ({os.path.getsize(path) / 1048576:.0f} MB)")
    print("=" * 50)


if __name__ == "__main__":
    main()
