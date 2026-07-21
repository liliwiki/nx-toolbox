"""NS工具箱 更新器 — 接管全部安装流程：等待→替换→清理→启动。复古游戏风格界面。"""
import os
import sys
import time
import shutil
import threading
import tkinter as tk


PIXEL_COLORS = {
    "bg": "#0f0f23",
    "card_bg": "#1a1a3e",
    "accent2": "#ffd93d",
    "green": "#6bff6b",
    "text": "#ffffff",
    "text_dim": "#6a6a9a",
}


def do_install(old_pid: int, old_exe: str, new_exe: str, old_mei: str, status_cb) -> None:
    """后台执行完整安装流程。"""
    old_backup = old_exe + ".old"

    # 1. 等旧进程死
    for i in range(6):
        time.sleep(0.5)
        status_cb(f"等待旧进程退出... ({i+1}/6)")

    # 2. 兜底强杀
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(1, False, old_pid)
        if handle:
            kernel32.TerminateProcess(handle, 0)
            kernel32.CloseHandle(handle)

    # 3. 删旧 .old（如果存在）
    if os.path.exists(old_backup):
        status_cb("正在删除旧版本...")
        for _ in range(10):
            try:
                os.remove(old_backup)
                break
            except OSError:
                time.sleep(0.5)

    # 4. rename 旧 → .old（旧进程已死，无锁）
    if os.path.exists(old_exe):
        status_cb("正在替换文件...")
        os.rename(old_exe, old_backup)

    # 5. rename 新 → 原名
    if os.path.exists(new_exe):
        os.rename(new_exe, old_exe)

    # 6. 清旧 _MEI
    if old_mei and os.path.isdir(old_mei):
        status_cb("正在清理临时文件...")
        try:
            shutil.rmtree(old_mei, ignore_errors=True)
        except OSError:
            pass

    # 7. 删 .old（替换后立即清理）
    if os.path.exists(old_backup):
        try:
            os.remove(old_backup)
        except OSError:
            pass

    # 8. 启动新版本
    status_cb("正在启动...")
    time.sleep(0.5)
    os.startfile(old_exe)


def main() -> None:
    if len(sys.argv) < 4:
        sys.exit(1)

    old_pid = int(sys.argv[1])
    old_exe = sys.argv[2]
    new_exe = sys.argv[3]
    old_mei = sys.argv[4] if len(sys.argv) > 4 else ""

    # --- GUI ---
    root = tk.Tk()
    root.title("NS工具箱 - 更新")
    root.resizable(False, False)
    root.configure(bg=PIXEL_COLORS["bg"])

    w, h = 400, 170
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    tk.Label(
        root, text="<  SYSTEM UPDATE  >",
        font=("Consolas", 14, "bold"), fg=PIXEL_COLORS["accent2"], bg=PIXEL_COLORS["bg"],
    ).pack(pady=(20, 5))

    status_var = tk.StringVar(value="正在准备...")
    tk.Label(
        root, textvariable=status_var,
        font=("Microsoft YaHei UI", 10), fg=PIXEL_COLORS["text"], bg=PIXEL_COLORS["bg"],
    ).pack(pady=(0, 10))

    from tkinter import ttk
    progress = ttk.Progressbar(root, mode="indeterminate", length=320)
    progress.pack(pady=(0, 5))
    progress.start(15)

    tk.Label(
        root, text="by 立里", font=("Consolas", 8),
        fg=PIXEL_COLORS["text_dim"], bg=PIXEL_COLORS["bg"],
    ).pack()

    def update_thread() -> None:
        def set_status(msg: str) -> None:
            root.after(0, lambda: status_var.set(msg))

        do_install(old_pid, old_exe, new_exe, old_mei, set_status)

        root.after(0, lambda: status_var.set("安装完成！"))
        root.after(0, progress.stop)
        root.after(800, root.destroy)

    threading.Thread(target=update_thread, daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()
