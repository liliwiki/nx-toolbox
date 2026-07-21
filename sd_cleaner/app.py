"""主应用类 - tkinter UI 与业务逻辑对接"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from sd_cleaner.common import (
    BUNDLE_DIR,
    DEFAULT_PACKAGE_URL,
    EMULATOR_PATHS,
    FIRMWARE_DIR,
    MOD_PATCH_KEYWORDS,
    THEME_TITLE_IDS,
    VERSION,
    AppConfig, load_config, save_config,
    format_size, sort_firmware_names,
)
from sd_cleaner.archive import (
    copy_tree,
    detect_archive_type,
    extract_archive,
)
from sd_cleaner.sd_manager import (
    PreserveOptions,
    build_whitelist_paths,
    delete_folder_with_whitelist,
    _folder_has_whitelist_entries,
    detect_sd_cards,
    get_protected_switch_items,
    delete_switch_folder_selectively,
    get_items_to_delete,
    calculate_delete_size,
    get_sd_space,
    get_sd_state_snapshot,
    eject_sd_card,
    _rmtree_force,
    _remove_force,
)
from sd_cleaner.firmware import (
    get_local_firmware,
    get_online_firmware_list,
    install_firmware_smart,
    delete_local_firmware,
)
from sd_cleaner.package import find_package_root, install_package_smart
from sd_cleaner.version_detector import detect_versions, check_firmware_compatibility
from sd_cleaner.op_logger import OperationLogger
from sd_cleaner.snapshot import SnapshotManager

logger = logging.getLogger(__name__)


class SDCleanerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.withdraw()
        self.root.title(f"NS换包工具 v{VERSION} - by 立里")
        self.root.geometry("620x520")
        self.root.resizable(True, True)
        self.root.minsize(580, 480)

        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - 620) // 2
        y = (sh - 520) // 2
        self.root.geometry(f"620x520+{x}+{y}")
        self.root.deiconify()

        self._icon_path = os.path.join(BUNDLE_DIR, "icon.ico")
        if os.path.exists(self._icon_path):
            try:
                self.root.iconbitmap(self._icon_path)
            except tk.TclError:
                pass

        # 加载配置
        self._cfg = load_config()

        # 主题
        self.is_dark_theme = tk.BooleanVar(value=self._cfg.is_dark_theme)

        # 向导
        self.current_step = 0
        self.step_names = ["检测SD卡", "选择保留", "清理", "更新整合包", "完成"]
        self.step_frames: dict[int, ttk.Frame] = {}
        self.step_labels: list[ttk.Label] = []

        # 配置变量
        self.sd_path = tk.StringVar(value=self._cfg.sd_path)
        self.keep_keys_certs = tk.BooleanVar(value=self._cfg.keep_keys_certs)
        self.keep_emu_and_nintendo = tk.BooleanVar(value=self._cfg.keep_emu_and_nintendo)
        self.keep_switchroot = tk.BooleanVar(value=self._cfg.keep_switchroot)
        self.keep_jksv = tk.BooleanVar(value=self._cfg.keep_jksv)
        self.keep_emuiibo = tk.BooleanVar(value=self._cfg.keep_emuiibo)
        self.keep_cheats_mods = tk.BooleanVar(value=self._cfg.keep_cheats_mods)
        self.keep_themes = tk.BooleanVar(value=self._cfg.keep_themes)
        self.keep_emulators = tk.BooleanVar(value=self._cfg.keep_emulators)
        self.custom_preserve_items: list[str] = list(self._cfg.custom_preserve_items)
        self.package_url = tk.StringVar(value=self._cfg.package_url)
        self.auto_snapshot = tk.BooleanVar(value=self._cfg.auto_snapshot)

        self.is_cleaning = False
        self.is_downloading = False
        self.last_sd_state: tuple | None = None
        self.auto_refresh_interval = 2000
        self.current_installing_firmware: dict | None = None
        self._op_logger = OperationLogger()
        self._snapshot_mgr = SnapshotManager()

        self._create_widgets()
        self.root.after(100, self._setup_drag_drop)
        self.root.after(500, self._start_auto_refresh)
        self.root.after(300, self._wizard_start)

    def _set_window_icon(self, window: tk.Toplevel) -> None:
        """为弹出窗口设置应用图标。"""
        if os.path.exists(self._icon_path):
            try:
                window.iconbitmap(self._icon_path)
            except tk.TclError:
                pass

    def _center_window(self, window: tk.Toplevel, width: int, height: int) -> None:
        """将弹出窗口居中到屏幕中央（无闪烁）。"""
        window.withdraw()
        window.update_idletasks()
        sw = window.winfo_screenwidth()
        sh = window.winfo_screenheight()
        x = (sw - width) // 2
        y = (sh - height) // 2
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.deiconify()

    # ------------------------------------------------------------------
    # 快照询问
    # ------------------------------------------------------------------

    def _ask_snapshot_before_clean(self, sd_path: str, files: list[str], folders: list[str]) -> bool:
        """在主线程中询问用户是否为清理操作创建快照。返回 True 表示创建。"""
        if not self.auto_snapshot.get():
            return False
        est_mb = self._snapshot_mgr.estimate_clean_size(sd_path, files, folders)
        if est_mb < 1:
            return True
        return messagebox.askyesno(
            "💾 创建快照",
            f"是否在清理前创建快照？\n\n预计快照大小: {est_mb:.0f} MB\n"
            f"快照可用于在清理后恢复文件。\n\n"
            f"选\"否\"将跳过快照直接清理。",
        )

    def _ask_snapshot_before_update(self, sd_path: str, desc: str, targets: list[str] | None = None) -> bool:
        """在主线程中询问用户是否为更新操作创建快照。返回 True 表示创建。"""
        if not self.auto_snapshot.get():
            return False
        est_mb = self._snapshot_mgr.estimate_update_size(sd_path, targets)
        if est_mb < 1:
            return True
        return messagebox.askyesno(
            "💾 创建快照",
            f"是否在{desc}前创建快照？\n\n预计快照大小: {est_mb:.0f} MB\n"
            f"快照可用于在更新后恢复到之前的状态。\n\n"
            f"选\"否\"将跳过快照直接操作。",
        )

    # ------------------------------------------------------------------
    # 配置同步
    # ------------------------------------------------------------------

    def _get_preserve_options(self) -> PreserveOptions:
        return PreserveOptions(
            keep_keys_certs=self.keep_keys_certs.get(),
            keep_emu_and_nintendo=self.keep_emu_and_nintendo.get(),
            keep_switchroot=self.keep_switchroot.get(),
            keep_jksv=self.keep_jksv.get(),
            keep_emuiibo=self.keep_emuiibo.get(),
            keep_cheats_mods=self.keep_cheats_mods.get(),
            keep_themes=self.keep_themes.get(),
            keep_emulators=self.keep_emulators.get(),
            custom_preserve_items=list(self.custom_preserve_items),
        )

    def _save_config(self) -> None:
        self._cfg.package_url = self.package_url.get()
        self._cfg.sd_path = self.sd_path.get()
        self._cfg.keep_keys_certs = self.keep_keys_certs.get()
        self._cfg.keep_emu_and_nintendo = self.keep_emu_and_nintendo.get()
        self._cfg.keep_switchroot = self.keep_switchroot.get()
        self._cfg.keep_jksv = self.keep_jksv.get()
        self._cfg.keep_emuiibo = self.keep_emuiibo.get()
        self._cfg.keep_cheats_mods = self.keep_cheats_mods.get()
        self._cfg.keep_themes = self.keep_themes.get()
        self._cfg.keep_emulators = self.keep_emulators.get()
        self._cfg.custom_preserve_items = list(self.custom_preserve_items)
        self._cfg.is_dark_theme = self.is_dark_theme.get()
        self._cfg.auto_snapshot = self.auto_snapshot.get()
        save_config(self._cfg)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _create_widgets(self) -> None:
        self.main_frame = ttk.Frame(self.root, padding="10")
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        # 步骤指示器
        step_bar = ttk.Frame(self.main_frame)
        step_bar.pack(fill=tk.X, pady=(0, 10))

        self.step_labels = []
        for i, name in enumerate(self.step_names):
            if i > 0:
                ttk.Label(step_bar, text=" → ", foreground="gray").pack(side=tk.LEFT)
            lbl = ttk.Label(step_bar, text=f" {i + 1}.{name} ", font=("Microsoft YaHei UI", 9))
            lbl.pack(side=tk.LEFT, padx=1)
            self.step_labels.append(lbl)

        self.settings_btn = ttk.Button(step_bar, text="⚙️", width=3, command=self._show_settings_menu)
        self.settings_btn.pack(side=tk.RIGHT)

        # 内容区域
        self.content_frame = ttk.Frame(self.main_frame)
        self.content_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        self._create_step1()
        self._create_step2()
        self._create_step3()
        self._create_step4()
        self._create_step5()

        # 底部栏
        bottom = ttk.Frame(self.main_frame)
        bottom.pack(fill=tk.X, side=tk.BOTTOM)

        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X, pady=(0, 5))

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(bottom, textvariable=self.status_var, relief=tk.SUNKEN).pack(fill=tk.X)

    # ==================== 向导步骤页面 ====================

    def _create_step1(self) -> None:
        frame = ttk.Frame(self.content_frame)
        self.step_frames[1] = frame

        ttk.Label(frame, text="🔍 检测SD卡", font=("Microsoft YaHei UI", 14, "bold")).pack(pady=(20, 10))
        self.detect_status_label = ttk.Label(frame, text="等待检测...", font=("Microsoft YaHei UI", 10))
        self.detect_status_label.pack(pady=(0, 20))

        path_frame = ttk.LabelFrame(frame, text="SD卡路径", padding="10")
        path_frame.pack(fill=tk.X, padx=20, pady=(0, 20))
        ttk.Entry(path_frame, textvariable=self.sd_path, width=50).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        ttk.Button(path_frame, text="自动检测", command=self._auto_detect_sd).pack(side=tk.RIGHT, padx=(0, 5))
        ttk.Button(path_frame, text="浏览...", command=self._browse_folder).pack(side=tk.RIGHT)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=20)
        ttk.Button(btn_frame, text="下一步 →", command=lambda: self._wizard_go_to(2), width=15).pack()

    def _create_step2(self) -> None:
        frame = ttk.Frame(self.content_frame)
        self.step_frames[2] = frame

        self.space_frame = ttk.Frame(frame)
        self.space_frame.pack(fill=tk.X, pady=(0, 5))
        self.space_label = ttk.Label(self.space_frame, text="SD卡空间: 请先选择SD卡")
        self.space_label.pack(side=tk.LEFT)

        self.version_frame = ttk.Frame(frame)
        self.version_frame.pack(fill=tk.X, pady=(0, 5))
        self.version_label = ttk.Label(self.version_frame, text="版本信息: 请先选择SD卡")
        self.version_label.pack(side=tk.LEFT)

        options_frame = ttk.LabelFrame(frame, text="保留选项", padding="10")
        options_frame.pack(fill=tk.X, pady=(0, 5))
        options_left = ttk.Frame(options_frame)
        options_left.pack(side=tk.LEFT, anchor=tk.N, padx=(0, 20))
        options_right = ttk.Frame(options_frame)
        options_right.pack(side=tk.LEFT, anchor=tk.N)

        keys_cb = ttk.Checkbutton(options_left, text="保留密钥和联网证书 (prod.keys/automatic_backups)", variable=self.keep_keys_certs)
        keys_cb.pack(anchor=tk.W, pady=2)
        keys_cb.config(state="disabled")

        self.emu_nintendo_check = ttk.Checkbutton(options_left, text="保留虚拟系统和正版数据 (emuMMC/Nintendo)", variable=self.keep_emu_and_nintendo, command=self._on_emu_nintendo_toggle)
        self.emu_nintendo_check.pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(options_left, text="保留其他系统 (switchroot/ini)", variable=self.keep_switchroot, command=self._on_option_changed).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(options_left, text="保留JKSV存档 (JKSV)", variable=self.keep_jksv, command=self._on_option_changed).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(options_left, text="保留Amiibo模拟 (emuiibo)", variable=self.keep_emuiibo, command=self._on_option_changed).pack(anchor=tk.W, pady=2)

        ttk.Checkbutton(options_right, text="保留金手指和MOD (cheats/romfs/exefs/exefs_patches)", variable=self.keep_cheats_mods, command=self._on_option_changed).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(options_right, text="保留主题 (contents/01000000000010xx)", variable=self.keep_themes, command=self._on_option_changed).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(options_right, text="保留模拟器 (ONS/Tiicu/PPSSPP/RetroArch/ROM)", variable=self.keep_emulators, command=self._on_option_changed).pack(anchor=tk.W, pady=2)

        custom_frame = ttk.Frame(options_right)
        custom_frame.pack(anchor=tk.W, pady=2, fill=tk.X)
        self.custom_preserve_btn = ttk.Button(custom_frame, text="📁 自定义保留...", command=self._show_custom_preserve_dialog, width=15)
        self.custom_preserve_btn.pack(side=tk.LEFT)
        self.custom_count_label = ttk.Label(custom_frame, text=f"(已选 {len(self.custom_preserve_items)} 项)", foreground="gray")
        self.custom_count_label.pack(side=tk.LEFT, padx=(10, 0))

        preview_frame = ttk.LabelFrame(frame, text="操作预览", padding="5")
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))
        preview_scroll = ttk.Scrollbar(preview_frame)
        preview_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview_text = tk.Text(preview_frame, height=8, wrap=tk.WORD, yscrollcommand=preview_scroll.set, state=tk.DISABLED)
        self.preview_text.pack(fill=tk.BOTH, expand=True)
        preview_scroll.config(command=self.preview_text.yview)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="← 上一步", command=lambda: self._wizard_go_to(1)).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="跳过清理 →", command=lambda: self._wizard_go_to(4)).pack(side=tk.LEFT, padx=5)
        self.clean_btn = tk.Button(btn_frame, text="🗑️ 开始清理", command=self._start_clean, bg="#dc3545", fg="white", activebackground="#c82333", activeforeground="white", font=("Microsoft YaHei UI", 9, "bold"), relief=tk.RAISED, padx=8, pady=1)
        self.clean_btn.pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="预览", width=6, command=self._preview_delete).pack(side=tk.RIGHT, padx=5)

    def _create_step3(self) -> None:
        frame = ttk.Frame(self.content_frame)
        self.step_frames[3] = frame
        ttk.Label(frame, text="🗑️ 正在清理SD卡...", font=("Microsoft YaHei UI", 14, "bold")).pack(pady=(40, 20))
        self.clean_progress_label = ttk.Label(frame, text="清理中，请勿拔出SD卡", font=("Microsoft YaHei UI", 10))
        self.clean_progress_label.pack(pady=10)
        ttk.Label(frame, text="清理完成后将自动进入下一步", foreground="gray").pack(pady=(20, 0))

    def _create_step4(self) -> None:
        frame = ttk.Frame(self.content_frame)
        self.step_frames[4] = frame
        ttk.Label(frame, text="📥 更新整合包", font=("Microsoft YaHei UI", 14, "bold")).pack(pady=(30, 10))
        ttk.Label(frame, text="选择整合包更新方式", font=("Microsoft YaHei UI", 10)).pack(pady=(0, 20))
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=10)
        self.update_btn = ttk.Button(btn_frame, text="📥 在线更新", command=self._start_update_package, width=15)
        self.update_btn.pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="📂 本地更新", command=self._local_update_from_wizard, width=15).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="跳过 →", command=lambda: self._wizard_go_to(5), width=10).pack(side=tk.LEFT, padx=10)
        self.update_progress_label = ttk.Label(frame, text="", font=("Microsoft YaHei UI", 9), foreground="gray")
        self.update_progress_label.pack(pady=(20, 0))

    def _create_step5(self) -> None:
        frame = ttk.Frame(self.content_frame)
        self.step_frames[5] = frame
        ttk.Label(frame, text="✅ 操作完成", font=("Microsoft YaHei UI", 14, "bold")).pack(pady=(30, 10))
        self.finish_summary_label = ttk.Label(frame, text="SD卡已准备就绪", font=("Microsoft YaHei UI", 10))
        self.finish_summary_label.pack(pady=(0, 10))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=10)
        self.eject_btn = ttk.Button(btn_frame, text="⏏️ 弹出SD卡", command=self._eject_sd_card, width=15)
        self.eject_btn.pack(side=tk.LEFT, padx=5)
        self.firmware_btn = ttk.Button(btn_frame, text="📦 更新固件", command=self._show_firmware_menu, width=15)
        self.firmware_btn.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="📋 日志", command=self._show_op_log, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="📸 快照", command=self._show_snapshot_manager, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="完成", command=self._on_finish_click, width=8).pack(side=tk.LEFT, padx=5)

    def _on_finish_click(self) -> None:
        """点击完成后关闭窗口。如果从工具箱启动，正确返回工具箱。"""
        # 获取已注册的 WM_DELETE_WINDOW 协议处理器
        # 工具箱在启动时通过 protocol("WM_DELETE_WINDOW", callback) 注册了回调
        protocol_cmd = self.root.protocol("WM_DELETE_WINDOW")
        if protocol_cmd:
            # 有协议处理器（从工具箱启动），调用它来正确返回
            self.root.tk.eval(protocol_cmd)
        else:
            # 独立模式，直接关闭
            self.root.destroy()

    # ==================== 向导导航 ====================

    def _show_step(self, step_num: int) -> None:
        for frm in self.step_frames.values():
            frm.pack_forget()
        if step_num in self.step_frames:
            self.step_frames[step_num].pack(in_=self.content_frame, fill=tk.BOTH, expand=True)
            self.current_step = step_num
        for i, lbl in enumerate(self.step_labels):
            if i + 1 == step_num:
                lbl.configure(font=("Microsoft YaHei UI", 9, "bold"), foreground="#0078d4")
            elif i + 1 < step_num:
                lbl.configure(font=("Microsoft YaHei UI", 9), foreground="green")
            else:
                lbl.configure(font=("Microsoft YaHei UI", 9), foreground="gray")

    def _wizard_start(self) -> None:
        self._show_step(1)
        self.detect_status_label.config(text="正在自动检测SD卡...")
        self.root.update()

        found_drives = detect_sd_cards()

        if len(found_drives) == 1:
            self.sd_path.set(found_drives[0])
            self._save_config()
            self.detect_status_label.config(text=f"✅ 已检测到SD卡: {found_drives[0]}")
            self.status_var.set(f"已检测到SD卡: {found_drives[0]}")
            self.root.after(800, lambda: self._wizard_go_to(2))
        elif len(found_drives) > 1:
            self.detect_status_label.config(text=f"检测到 {len(found_drives)} 个SD卡，请选择后点击下一步")
            self._show_drive_selection(found_drives)
        else:
            self.detect_status_label.config(text="❌ 未检测到SD卡，请手动选择路径后点击下一步")
            self.status_var.set("未检测到SD卡")

    def _wizard_go_to(self, step_num: int) -> None:
        if step_num == 2:
            sd_path = self.sd_path.get()
            if not sd_path or not os.path.exists(sd_path):
                messagebox.showwarning("警告", "请先选择有效的SD卡路径")
                return
            self._show_step(2)
            self._update_sd_space_info()
            self._preview_delete()
        else:
            self._show_step(step_num)

    # ==================== 设置菜单 ====================

    def _show_settings_menu(self) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="更改在线更新整合包网址", command=self._open_url_settings)
        menu.add_separator()
        menu.add_command(label="添加固件", command=self._add_firmware)
        menu.add_separator()
        snapshot_label = "✅ 操作前询问创建快照" if self.auto_snapshot.get() else "❌ 操作前询问创建快照"
        menu.add_command(label=snapshot_label, command=self._toggle_auto_snapshot)
        menu.add_separator()
        menu.add_command(label="🔄 检查更新", command=self._check_update)
        menu.add_separator()
        theme_menu = tk.Menu(menu, tearoff=0)
        theme_menu.add_command(label="☀ 浅色模式", command=lambda: self._apply_theme(False))
        theme_menu.add_command(label="🌙 深色模式", command=lambda: self._apply_theme(True))
        menu.add_cascade(label="🎨 切换主题", menu=theme_menu)
        try:
            x = self.settings_btn.winfo_rootx() - 160
            y = self.settings_btn.winfo_rooty() + self.settings_btn.winfo_height()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _toggle_auto_snapshot(self) -> None:
        new_val = not self.auto_snapshot.get()
        self.auto_snapshot.set(new_val)
        self._save_config()
        state = "开启" if new_val else "关闭"
        self.status_var.set(f"快照功能已{state}")

    def _check_update(self) -> None:
        import threading
        from sd_cleaner.common import VERSION, UPDATE_MANIFEST_URL

        def do_check() -> None:
            from update_checker import check_for_update
            result = check_for_update(VERSION, UPDATE_MANIFEST_URL)
            if result is None:
                self.root.after(0, lambda: messagebox.showinfo(
                    "检查更新", "当前已是最新版本", parent=self.root))
                return
            new_ver = result["version"]
            changelog = result.get("changelog", "")
            download_url = result["download_url"]

            def show_result() -> None:
                msg = f"发现新版本: v{new_ver}\n当前版本: v{VERSION}"
                if changelog:
                    msg += f"\n\n更新内容:\n{changelog}"
                msg += "\n\n是否立即下载并安装更新？"
                if messagebox.askyesno("更新可用", msg, parent=self.root):
                    self._do_update_download(download_url, new_ver)

            self.root.after(0, show_result)

        threading.Thread(target=do_check, daemon=True).start()

    def _do_update_download(self, download_url: str, new_ver: str) -> None:
        import tempfile
        import threading
        from update_checker import _get_old_exe_path, download_update, install_update

        old_exe = _get_old_exe_path()
        new_exe = os.path.join(tempfile.gettempdir(), "NS工具箱_update.exe")

        if os.path.exists(new_exe):
            try:
                os.remove(new_exe)
            except OSError:
                pass

        self.status_var.set(f"正在下载更新 v{new_ver} ...")
        self.progress.config(mode="determinate")
        self.progress["value"] = 0

        def prog_cb(pct: int, dl_mb: float, tot_mb: float) -> None:
            self.root.after(0, lambda: self.progress.config(value=pct))
            self.root.after(0, lambda: self.status_var.set(
                f"下载更新: {dl_mb:.1f} MB / {tot_mb:.1f} MB ({pct}%)"))

        def do_download() -> None:
            ok = download_update(download_url, new_exe, progress_callback=prog_cb)
            if ok:
                self.root.after(0, lambda: self.status_var.set("正在安装，程序将自动重启..."))
                # install_update 会立刻替换并自重启（os._exit），不会返回
                ok2 = install_update(new_exe, old_exe)
                if not ok2:
                    self.root.after(0, lambda: messagebox.showerror(
                        "错误", "启动更新程序失败，请手动替换文件", parent=self.root))
            else:
                self.root.after(0, lambda: messagebox.showerror(
                    "下载失败", "下载更新失败，请检查网络连接后重试", parent=self.root))

        threading.Thread(target=do_download, daemon=True).start()

    def _apply_theme(self, is_dark: bool) -> None:
        self.is_dark_theme.set(is_dark)
        self._save_config()
        style = ttk.Style()
        if is_dark:
            bg, fg, frame_bg = "#2b2b2b", "#ffffff", "#3c3c3c"
            entry_bg, select_bg, btn_bg = "#404040", "#0078d4", "#404040"
            self.root.configure(bg=bg)
            style.theme_use("clam")
            style.configure("TFrame", background=bg)
            style.configure("TLabelframe", background=bg)
            style.configure("TLabelframe.Label", background=bg, foreground=fg)
            style.configure("TLabel", background=bg, foreground=fg)
            style.configure("TCheckbutton", background=bg, foreground=fg)
            style.configure("TButton", background=btn_bg, foreground=fg)
            style.configure("TEntry", fieldbackground=entry_bg, foreground=fg)
            style.configure("TProgressbar", background=select_bg, troughcolor=frame_bg)
            style.configure("TScrollbar", background=btn_bg, troughcolor=bg)
            style.map("TCheckbutton", background=[("active", frame_bg)], foreground=[("active", fg)])
            style.map("TButton", background=[("active", "#505050")])
            self.preview_text.configure(bg=entry_bg, fg=fg, insertbackground=fg, selectbackground=select_bg)
            self.status_var.set("已切换到深色模式")
        else:
            bg, fg = "#f0f0f0", "#000000"
            entry_bg, select_bg = "#ffffff", "#0078d4"
            self.root.configure(bg=bg)
            try:
                if "vista" in style.theme_names():
                    style.theme_use("vista")
                elif "clam" in style.theme_names():
                    style.theme_use("clam")
            except tk.TclError:
                pass
            style.configure("TFrame", background=bg)
            style.configure("TLabelframe", background=bg)
            style.configure("TLabelframe.Label", background=bg, foreground=fg)
            style.configure("TLabel", background=bg, foreground=fg)
            style.configure("TCheckbutton", background=bg, foreground=fg)
            style.configure("TButton")
            style.configure("TEntry", fieldbackground=entry_bg, foreground=fg)
            style.configure("TProgressbar")
            style.map("TCheckbutton", background=[("active", bg)], foreground=[("active", fg)])
            self.preview_text.configure(bg=entry_bg, fg=fg, insertbackground=fg, selectbackground=select_bg)
            self.status_var.set("已切换到浅色模式")

    def _add_firmware(self) -> None:
        os.makedirs(FIRMWARE_DIR, exist_ok=True)
        files = filedialog.askopenfilenames(
            title="选择固件文件",
            filetypes=[("压缩包", "*.zip *.7z *.rar"), ("ZIP压缩包", "*.zip"), ("7Z压缩包", "*.7z"), ("RAR压缩包", "*.rar")],
        )
        if not files:
            return
        success_count = 0
        for file_path in files:
            try:
                file_name = os.path.basename(file_path)
                dest_path = os.path.join(FIRMWARE_DIR, file_name)
                if os.path.exists(dest_path):
                    if not messagebox.askyesno("文件已存在", f"固件 {file_name} 已存在，是否覆盖？"):
                        continue
                shutil.copy2(file_path, dest_path)
                success_count += 1
            except OSError as e:
                logger.warning("复制固件失败: %s", e)
        if success_count > 0:
            self.status_var.set(f"已添加 {success_count} 个固件")
            messagebox.showinfo("完成", f"成功添加 {success_count} 个固件到固件文件夹")

    def _open_url_settings(self) -> None:
        win = tk.Toplevel(self.root)
        self._set_window_icon(win)
        win.title("更改在线更新整合包网址")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        self._center_window(win, 580, 200)

        frame = ttk.Frame(win, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="在线更新整合包地址:").pack(anchor=tk.W)
        temp_url = tk.StringVar(value=self.package_url.get())
        ttk.Entry(frame, textvariable=temp_url, width=65).pack(fill=tk.X, pady=(5, 10))
        ttk.Label(frame, text="💡 默认地址: " + DEFAULT_PACKAGE_URL[:50] + "...", foreground="gray").pack(anchor=tk.W, pady=(0, 15))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X)

        def save_and_close() -> None:
            url = temp_url.get().strip()
            if not url:
                messagebox.showwarning("警告", "请输入有效的下载地址", parent=win)
                return
            if not url.startswith(("http://", "https://")):
                messagebox.showwarning("警告", "请输入有效的HTTP/HTTPS地址", parent=win)
                return
            self.package_url.set(url)
            self._save_config()
            self.status_var.set("整合包网址已更新")
            win.destroy()

        ttk.Button(btn_frame, text="保存", command=save_and_close, width=10).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text="恢复默认", command=lambda: temp_url.set(DEFAULT_PACKAGE_URL), width=10).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text="取消", command=win.destroy, width=10).pack(side=tk.RIGHT)

    # ==================== SD 卡检测 ====================

    def _auto_detect_sd(self) -> None:
        self.status_var.set("正在检测SD卡...")
        self.root.update()
        found_drives = detect_sd_cards()

        if not found_drives:
            self.status_var.set("未检测到SD卡")
            self.detect_status_label.config(text="❌ 未检测到SD卡，请手动选择路径")
            messagebox.showinfo("检测结果", "未检测到Switch的SD卡\n\n请确保：\n1. SD卡已正确连接电脑\n2. SD卡包含atmosphere或Nintendo文件夹")
        elif len(found_drives) == 1:
            self.sd_path.set(found_drives[0])
            self._save_config()
            self.status_var.set(f"已检测到SD卡: {found_drives[0]}")
            self.detect_status_label.config(text=f"✅ 已检测到SD卡: {found_drives[0]}")
            self._preview_delete()
        else:
            self.detect_status_label.config(text=f"检测到 {len(found_drives)} 个SD卡，请选择")
            self._show_drive_selection(found_drives)

    def _browse_folder(self) -> None:
        folder = filedialog.askdirectory(title="选择SD卡根目录")
        if folder:
            self.sd_path.set(folder)
            self._save_config()
            self._preview_delete()

    def _show_drive_selection(self, drives: list[str]) -> None:
        win = tk.Toplevel(self.root)
        self._set_window_icon(win)
        win.title("选择SD卡")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        self._center_window(win, 350, 250)
        frame = ttk.Frame(win, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="检测到多个SD卡，请选择：").pack(anchor=tk.W)
        listbox = tk.Listbox(frame, height=5)
        listbox.pack(fill=tk.BOTH, expand=True, pady=10)
        for drive in drives:
            listbox.insert(tk.END, drive)
        if drives:
            listbox.selection_set(0)

        def on_select() -> None:
            sel = listbox.curselection()
            if sel:
                self.sd_path.set(drives[sel[0]])
                self._save_config()
                self.status_var.set(f"已选择SD卡: {drives[sel[0]]}")
                win.destroy()
                self._preview_delete()

        ttk.Button(frame, text="确定", command=on_select).pack()

    # ==================== 保留选项事件 ====================

    def _on_emu_nintendo_toggle(self) -> None:
        if not self.keep_emu_and_nintendo.get():
            result = messagebox.askyesno(
                "⚠️ 警告",
                "取消勾选将会删除以下重要数据：\n\n• emuMMC 文件夹 (虚拟系统)\n• Nintendo 文件夹 (正版游戏存档和数据)\n\n这些数据一旦删除将无法恢复！\n\n确定要取消保留吗？",
                icon="warning",
            )
            if not result:
                self.keep_emu_and_nintendo.set(True)
        if self.sd_path.get():
            self._preview_delete()

    def _on_option_changed(self) -> None:
        self._save_config()
        if self.sd_path.get():
            self._preview_delete()

    # ==================== 预览 ====================

    def _preview_delete(self) -> None:
        sd_path = self.sd_path.get()
        if not sd_path:
            self._update_preview("请先选择SD卡路径")
            return
        if not os.path.exists(sd_path):
            self._update_preview("所选路径不存在")
            return

        self._update_sd_space_info()
        options = self._get_preserve_options()
        files, folders = get_items_to_delete(sd_path, options)

        lines: list[str] = [f"SD卡路径: {sd_path}\n", "-" * 50 + "\n"]

        protected_items = self._build_protected_summary(sd_path)
        if protected_items:
            lines.append("🛡️ 将被保留的内容:\n")
            lines.append(f"  ✓ {', '.join(protected_items)}\n\n")

        if folders:
            lines.append(f"📁 将删除 {len(folders)} 个文件夹:\n")
            for folder in sorted(folders):
                lines.append(f"  ✗ {folder}/\n")
            lines.append("\n")

        if files:
            lines.append(f"📄 将删除 {len(files)} 个文件:\n")
            for file in sorted(files):
                lines.append(f"  ✗ {file}\n")
            lines.append("\n")

        if not files and not folders:
            lines.append("✨ 没有需要删除的内容\n")
        else:
            total = len(files) + len(folders)
            lines.append(f"\n⚠️ 总计将删除 {total} 个项目")

        self._update_preview("".join(lines))

    def _build_protected_summary(self, sd_path: str) -> list[str]:
        """构建保留内容摘要列表。"""
        items: list[str] = []

        if self.keep_keys_certs.get():
            for p in [os.path.join(sd_path, "switch", "prod.keys"), os.path.join(sd_path, "atmosphere", "automatic_backups")]:
                if os.path.exists(p):
                    items.append("密钥和联网证书")
                    break

        if self.keep_emu_and_nintendo.get():
            for f in ("emuMMC", "emummc"):
                if os.path.exists(os.path.join(sd_path, f)):
                    items.append("虚拟系统")
                    break
            for f in ("Nintendo", "nintendo"):
                if os.path.exists(os.path.join(sd_path, f)):
                    items.append("正版数据")
                    break

        if self.keep_switchroot.get():
            switchroot_path = None
            for f in ("switchroot", "Switchroot"):
                p = os.path.join(sd_path, f)
                if os.path.exists(p):
                    switchroot_path = p
                    break
            if switchroot_path:
                system_map = {"android": "Android", "Android": "Android", "ubuntu": "Ubuntu", "Ubuntu": "Ubuntu", "lakka": "Lakka", "Lakka": "Lakka", "lineage": "LineageOS", "Lineage": "LineageOS"}
                found_systems: list[str] = []
                try:
                    for item in os.listdir(switchroot_path):
                        name = system_map.get(item)
                        if name and name not in found_systems:
                            found_systems.append(name)
                except OSError:
                    pass
                try:
                    for item in os.listdir(sd_path):
                        item_lower = item.lower()
                        if item_lower.startswith("l4t-") or item_lower in ("lakka", "ubuntu", "android", "lineage"):
                            found_systems.append(item)
                except OSError:
                    pass
                items.extend(found_systems if found_systems else ["其他系统"])
            else:
                try:
                    for item in os.listdir(sd_path):
                        item_lower = item.lower()
                        if item_lower.startswith("l4t-") or item_lower in ("lakka", "ubuntu", "android", "lineage"):
                            items.append(f"其他系统({item})")
                            break
                except OSError:
                    pass

        if self.keep_jksv.get():
            for f in ("JKSV", "jksv"):
                if os.path.exists(os.path.join(sd_path, f)):
                    items.append("JKSV存档")
                    break

        if self.keep_emuiibo.get():
            for f in ("emuiibo", "Emuiibo"):
                if os.path.exists(os.path.join(sd_path, f)):
                    items.append("Amiibo")
                    break

        if self.keep_cheats_mods.get():
            contents_path = os.path.join(sd_path, "atmosphere", "contents")
            exefs_patches_path = os.path.join(sd_path, "atmosphere", "exefs_patches")
            if os.path.exists(contents_path):
                for title_id in os.listdir(contents_path):
                    if os.path.exists(os.path.join(contents_path, title_id, "cheats")):
                        items.append("金手指")
                        break
            has_mod = False
            if os.path.exists(contents_path):
                for title_id in os.listdir(contents_path):
                    title_path = os.path.join(contents_path, title_id)
                    if os.path.isdir(title_path):
                        if any(os.path.exists(os.path.join(title_path, mt)) for mt in ("romfs", "exefs")):
                            has_mod = True
                            break
            if not has_mod and os.path.exists(exefs_patches_path):
                has_mod = any(
                    any(kw in pf for kw in MOD_PATCH_KEYWORDS)
                    for pf in os.listdir(exefs_patches_path)
                )
            if has_mod:
                items.append("MOD")

        if self.keep_emulators.get():
            found_emus: list[str] = []
            for emu_info in EMULATOR_PATHS.values():
                found = any(os.path.exists(os.path.join(sd_path, p)) for p in emu_info.get("root_paths", []))
                if not found:
                    found = any(os.path.exists(os.path.join(sd_path, "switch", p)) for p in emu_info.get("switch_paths", []))
                if found and emu_info["name"] not in found_emus:
                    found_emus.append(emu_info["name"])
            if found_emus:
                items.append(f"模拟器({'/'.join(found_emus)})")

        if self.keep_themes.get():
            contents_path = os.path.join(sd_path, "atmosphere", "contents")
            if os.path.exists(contents_path):
                if any(os.path.exists(os.path.join(contents_path, tid)) for tid in THEME_TITLE_IDS):
                    items.append("主题")

        if self.custom_preserve_items:
            items.append(f"自定义({len(self.custom_preserve_items)}项)")

        return items

    def _update_preview(self, text: str) -> None:
        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete(1.0, tk.END)
        self.preview_text.insert(tk.END, text)
        self.preview_text.config(state=tk.DISABLED)

    # ==================== 清理操作 ====================

    def _start_clean(self) -> None:
        if self.is_cleaning:
            return
        sd_path = self.sd_path.get()
        if not sd_path or not os.path.exists(sd_path):
            messagebox.showwarning("警告", "请先选择有效的SD卡路径")
            return

        options = self._get_preserve_options()
        files, folders = get_items_to_delete(sd_path, options)
        if not files and not folders:
            messagebox.showinfo("提示", "没有需要删除的内容")
            return

        self.status_var.set("正在计算文件大小...")
        self.root.update()
        delete_size = calculate_delete_size(sd_path, files, folders)
        size_str = format_size(delete_size)

        total = len(files) + len(folders)
        snap_hint = "\n\n💾 清理前可创建快照用于恢复" if self.auto_snapshot.get() else ""
        confirm_msg = f"确定要删除 {total} 个项目吗？\n\n文件夹: {len(folders)} 个\n文件: {len(files)} 个\n预计释放空间: {size_str}{snap_hint}"
        self.status_var.set("就绪")

        if not messagebox.askyesno("确认删除", confirm_msg, icon="warning"):
            return

        do_snapshot = self._ask_snapshot_before_clean(sd_path, files, folders)

        self.is_cleaning = True
        self.clean_btn.config(state=tk.DISABLED)
        self.progress.start()
        if self.current_step == 2:
            self._show_step(3)

        thread = threading.Thread(target=self._do_clean, args=(sd_path, files, folders, options, do_snapshot), daemon=True)
        thread.start()

    def _do_clean(self, sd_path: str, files: list[str], folders: list[str], options: PreserveOptions, do_snapshot: bool = True) -> None:
        deleted_count = 0
        error_count = 0
        errors: list[str] = []
        total_items = len(files) + len(folders)
        current_item = 0

        try:
            self.root.after(0, lambda: self.progress.config(mode="determinate"))
            self.root.after(0, lambda: self.progress.stop())

            if do_snapshot:
                self.root.after(0, lambda: self.status_var.set("正在创建快照..."))
                snap_id = self._snapshot_mgr.create_snapshot_for_clean(
                    sd_path, files, folders,
                    progress_callback=lambda pct, msg: self.root.after(
                        0, lambda p=pct, m=msg: self.status_var.set(f"[{p}%] {m}"),
                    ),
                )
                if snap_id:
                    logger.info("清理快照已创建: %s", snap_id)

            # 构建白名单：所有勾选保留的内容都不会被删除
            whitelist = build_whitelist_paths(options, sd_path)

            protected_switch = get_protected_switch_items(options)
            # 只保护实际存在的文件（没有 prod.keys 就不保护 switch 文件夹）
            switch_dir = os.path.join(sd_path, "switch")
            protected_switch = [p for p in protected_switch if os.path.exists(os.path.join(switch_dir, p))]
            protected_switch_lower = {p.lower() for p in protected_switch}

            for folder in folders:
                folder_path = os.path.join(sd_path, folder)
                current_item += 1
                pct = int(current_item * 100 / total_items) if total_items else 0
                self.root.after(0, lambda f=folder, p=pct: self.status_var.set(f"[{p}%] 正在删除文件夹: {f}"))
                self.root.after(0, lambda p=pct: setattr(self.progress, "value", p))
                try:
                    if folder.lower() == "switch" and protected_switch:
                        # always use selective deletion for switch/ folder
                        delete_switch_folder_selectively(folder_path, protected_switch_lower)
                    elif _folder_has_whitelist_entries(folder, whitelist):
                        # folder contains protected nested items — delete selectively
                        delete_folder_with_whitelist(folder_path, whitelist, folder)
                    else:
                        _rmtree_force(folder_path)
                    deleted_count += 1
                except OSError as e:
                    error_count += 1
                    errors.append(f"删除文件夹 {folder} 失败: {e}")

            for file in files:
                file_path = os.path.join(sd_path, file)
                current_item += 1
                pct = int(current_item * 100 / total_items) if total_items else 0
                self.root.after(0, lambda f=file, p=pct: self.status_var.set(f"[{p}%] 正在删除文件: {f}"))
                self.root.after(0, lambda p=pct: setattr(self.progress, "value", p))
                try:
                    _remove_force(file_path)
                    deleted_count += 1
                except OSError as e:
                    error_count += 1
                    errors.append(f"删除文件 {file} 失败: {e}")
        finally:
            self.root.after(0, self._clean_finished, deleted_count, error_count, errors)

    def _clean_finished(self, deleted_count: int, error_count: int, errors: list[str]) -> None:
        self.is_cleaning = False
        self.clean_btn.config(state=tk.NORMAL)
        self.progress.stop()
        self.progress["value"] = 0

        success = error_count == 0
        self._op_logger.log_clean(
            sd_path=self.sd_path.get(),
            deleted_files=[], deleted_folders=[],
            preserved=self._build_protected_summary(self.sd_path.get()),
            success=success,
            error_msg="; ".join(errors[:3]) if errors else "",
        )

        if success:
            self.status_var.set(f"清理完成！成功删除 {deleted_count} 个项目")
            messagebox.showinfo("清理完成", f"清理完成！成功删除 {deleted_count} 个项目")
        else:
            self.status_var.set(f"清理完成，但有 {error_count} 个错误")
            msg = f"删除完成\n成功: {deleted_count}\n失败: {error_count}\n\n错误详情:\n" + "\n".join(errors[:5])
            if len(errors) > 5:
                msg += f"\n... 还有 {len(errors) - 5} 个错误"
            messagebox.showwarning("完成（有错误）", msg)

        if self.current_step == 3:
            self._wizard_go_to(4)
        else:
            self._preview_delete()

    # ==================== 固件 UI ====================

    def _show_firmware_menu(self) -> None:
        fw_window = tk.Toplevel(self.root)
        self._set_window_icon(fw_window)
        fw_window.title("选择固件")
        fw_window.resizable(False, False)
        fw_window.transient(self.root)
        fw_window.grab_set()
        self._center_window(fw_window, 500, 450)

        frame = ttk.Frame(fw_window, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="请选择要安装的固件：").pack(anchor=tk.W)

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, font=("Microsoft YaHei UI", 10))
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=listbox.yview)

        status_label = ttk.Label(frame, text="正在加载固件列表...")
        status_label.pack(anchor=tk.W)

        firmware_dict: dict[str, str] = {}

        def load_list() -> None:
            local_fw = get_local_firmware()
            firmware_dict.update(local_fw)

            def update_local() -> None:
                if not fw_window.winfo_exists():
                    return
                for n in sort_firmware_names(local_fw.keys()):
                    listbox.insert(tk.END, n)
                status_label.config(text="正在获取在线固件列表...")

            fw_window.after(0, update_local)

            online_fw = get_online_firmware_list()
            firmware_dict.update(online_fw)

            def update_online() -> None:
                if not fw_window.winfo_exists():
                    return
                for n in sort_firmware_names(online_fw.keys()):
                    listbox.insert(tk.END, n)
                if firmware_dict:
                    status_label.config(text=f"共 {len(firmware_dict)} 个固件（在线: {len(online_fw)}, 本地: {len(local_fw)}）")
                    listbox.selection_set(0)
                else:
                    status_label.config(text="没有可用的固件")

            fw_window.after(0, update_online)

        threading.Thread(target=load_list, daemon=True).start()

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        def on_confirm() -> None:
            sel = listbox.curselection()
            if not sel:
                messagebox.showwarning("警告", "请先选择一个固件", parent=fw_window)
                return
            sd_path = self.sd_path.get()
            if not sd_path or not os.path.exists(sd_path):
                messagebox.showwarning("警告", "请先选择有效的SD卡路径", parent=fw_window)
                return
            name = listbox.get(sel[0])
            source = firmware_dict.get(name)
            if not source:
                return
            is_online = name.startswith("[在线]")
            src_type = "在线下载" if is_online else "本地安装"
            compat_ok, compat_msg = check_firmware_compatibility(sd_path, name)
            confirm_text = f"确定要安装固件吗？\n\n固件: {name}\n方式: {src_type}\n目标: {sd_path}\n\n{compat_msg}"
            if not compat_ok:
                if not messagebox.askyesno("⚠️ 兼容性警告", confirm_text, icon="warning", parent=fw_window):
                    return
            else:
                if not messagebox.askyesno("确认", confirm_text, parent=fw_window):
                    return
            do_snapshot = self._ask_snapshot_before_update(
                sd_path, "固件安装", targets=["atmosphere", "switch", "sept"],
            )
            fw_window.destroy()
            if is_online:
                self._download_and_install_firmware(source, name, sd_path, do_snapshot)
            else:
                is_user = name.startswith("[本地]")
                self._install_firmware(source, sd_path, is_user, do_snapshot)

        def on_delete() -> None:
            sel = listbox.curselection()
            if not sel:
                return
            name = listbox.get(sel[0])
            if not name.startswith("[本地]"):
                messagebox.showwarning("警告", "只能删除本地固件", parent=fw_window)
                return
            fw_path = firmware_dict.get(name)
            if not fw_path:
                return
            if not messagebox.askyesno("确认删除", f"确定要删除本地固件吗？\n\n{os.path.basename(fw_path)}", parent=fw_window):
                return
            ok, _ = delete_local_firmware(fw_path)
            if ok:
                listbox.delete(sel[0])
                del firmware_dict[name]
                messagebox.showinfo("完成", "固件已删除", parent=fw_window)

        def on_refresh() -> None:
            listbox.delete(0, tk.END)
            firmware_dict.clear()
            status_label.config(text="正在刷新固件列表...")
            threading.Thread(target=load_list, daemon=True).start()

        ttk.Button(btn_frame, text="刷新", command=on_refresh, width=8).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="删除", command=on_delete, width=8).pack(side=tk.LEFT, padx=(5, 0))
        ttk.Button(btn_frame, text="取消", command=fw_window.destroy, width=10).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="确定安装", command=on_confirm, width=10).pack(side=tk.RIGHT, padx=(0, 10))

    def _download_and_install_firmware(self, url: str, display_name: str, sd_path: str, do_snapshot: bool = True) -> None:
        self._set_busy(True)
        threading.Thread(target=self._do_download_and_install_firmware, args=(url, display_name, sd_path, do_snapshot), daemon=True).start()

    def _do_download_and_install_firmware(self, url: str, display_name: str, sd_path: str, do_snapshot: bool = True) -> None:
        temp_file: str | None = None
        try:
            filename = urllib.request.unquote(url.split("/")[-1])
            temp_file = os.path.join(tempfile.gettempdir(), filename)
            self.root.after(0, lambda: self.status_var.set(f"正在下载: {filename}"))

            max_retries = 3
            last_error: Exception | None = None

            for attempt in range(max_retries):
                try:
                    req = urllib.request.Request(url, headers={
                        "User-Agent": "Mozilla/5.0",
                        "Accept-Encoding": "identity",
                        "Connection": "keep-alive",
                    })
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        total = int(resp.headers.get("Content-Length", 0))
                        downloaded = 0
                        start_time = time.time()
                        with open(temp_file, "wb") as f:
                            while True:
                                buf = resp.read(1048576)  # 1MB chunks
                                if not buf:
                                    break
                                f.write(buf)
                                downloaded += len(buf)
                                elapsed = max(time.time() - start_time, 0.001)
                                speed = downloaded / elapsed / 1048576
                                if total > 0:
                                    pct = downloaded / total * 100
                                    self.root.after(0, lambda p=pct, d=downloaded, t=total, s=speed: (
                                        self.progress.config(value=p),
                                        self.status_var.set(
                                            f"正在下载: {d / 1048576:.1f} / {t / 1048576:.1f} MB "
                                            f"({p:.0f}%)  {s:.1f} MB/s"
                                        ),
                                    ))
                                else:
                                    self.root.after(0, lambda d=downloaded, s=speed: (
                                        self.status_var.set(
                                            f"正在下载: {d / 1048576:.1f} MB  {s:.1f} MB/s"
                                        ),
                                    ))
                    break  # success
                except (urllib.error.URLError, socket.timeout, OSError) as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        delay = 2 ** attempt  # 1s, 2s, 4s
                        self.root.after(0, lambda a=attempt+1, t=max_retries, d=delay: self.status_var.set(f"下载失败，重试 ({a}/{t})..."))
                        time.sleep(delay)
                        # Reset temp file for fresh download
                        if os.path.exists(temp_file):
                            try:
                                os.remove(temp_file)
                            except OSError:
                                pass
                    else:
                        raise

            self.root.after(0, lambda: self.status_var.set("下载完成，正在安装..."))
            self._do_install_firmware_inner(temp_file, sd_path, do_snapshot)
            self.root.after(0, self._firmware_install_finished, True, "")
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            self.root.after(0, self._firmware_install_finished, False, f"下载失败: {e}")
        finally:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass

    def _install_firmware(self, fw_path: str, sd_path: str, is_user_added: bool = False, do_snapshot: bool = True) -> None:
        self._set_busy(True)
        self.current_installing_firmware = {"path": fw_path, "is_user_added": is_user_added}
        threading.Thread(target=self._do_install_firmware_thread, args=(fw_path, sd_path, do_snapshot), daemon=True).start()

    def _do_install_firmware_thread(self, fw_path: str, sd_path: str, do_snapshot: bool = True) -> None:
        try:
            self._do_install_firmware_inner(fw_path, sd_path, do_snapshot)
            self.root.after(0, self._firmware_install_finished, True, "")
        except Exception as e:
            self.root.after(0, self._firmware_install_finished, False, str(e))

    def _do_install_firmware_inner(self, fw_path: str, sd_path: str, do_snapshot: bool = True) -> None:
        if do_snapshot:
            self.root.after(0, lambda: self.status_var.set("正在创建快照..."))
            self._snapshot_mgr.create_snapshot_for_update(
                sd_path, description=f"固件安装: {os.path.basename(fw_path)}",
                targets=["atmosphere", "switch", "sept"],
                progress_callback=lambda pct, msg: self.root.after(
                    0, lambda p=pct, m=msg: self.status_var.set(f"[{p}%] {m}"),
                ),
            )

        options = self._get_preserve_options()
        whitelist = build_whitelist_paths(options, sd_path)

        self.root.after(0, lambda: self.status_var.set("正在解压固件..."))
        self.root.after(0, lambda: self.progress.config(mode="indeterminate"))
        self.root.after(0, lambda: self.progress.start())

        temp_dir = tempfile.mkdtemp()
        try:
            extract_archive(fw_path, temp_dir)
            self.root.after(0, lambda: self.status_var.set("正在检测固件结构..."))
            fw_filename = os.path.basename(fw_path)

            def prog(pct: int, item: str) -> None:
                self.root.after(0, lambda: self.status_var.set(f"[{pct}%] 正在复制: {item}..."))
                self.root.after(0, lambda: setattr(self.progress, "value", pct))

            install_firmware_smart(temp_dir, sd_path, fw_filename, progress_callback=prog, exclude_set=whitelist)
        finally:
            try:
                shutil.rmtree(temp_dir)
            except OSError:
                pass

    def _firmware_install_finished(self, success: bool, error_msg: str) -> None:
        self._set_busy(False)
        fw_name = ""
        if self.current_installing_firmware:
            fw_name = os.path.basename(self.current_installing_firmware.get("path", ""))
        self._op_logger.log_firmware_install(
            sd_path=self.sd_path.get(),
            firmware_name=fw_name,
            source="本地" if self.current_installing_firmware and self.current_installing_firmware.get("is_user_added") else "在线",
            success=success,
            error_msg=error_msg,
        )
        if success:
            self.status_var.set("固件安装完成！")
            messagebox.showinfo("完成", "固件安装成功！")
            if self.current_installing_firmware:
                info = self.current_installing_firmware
                if info.get("is_user_added") and info.get("path") and os.path.exists(info["path"]):
                    if messagebox.askyesno("删除固件", f"固件安装成功！\n\n是否删除本地固件文件？\n{os.path.basename(info['path'])}"):
                        delete_local_firmware(info["path"])
        else:
            self.status_var.set("固件安装失败")
            messagebox.showerror("错误", f"固件安装失败\n\n{error_msg}")
        self.current_installing_firmware = None

    # ==================== 整合包更新 ====================

    def _start_update_package(self) -> None:
        if self.is_downloading or self.is_cleaning:
            return
        sd_path = self.sd_path.get()
        if not sd_path or not os.path.exists(sd_path):
            messagebox.showwarning("警告", "请先选择有效的SD卡路径")
            return
        if not messagebox.askyesno("确认更新", "即将执行以下操作：\n\n1. 下载最新整合包\n2. 解压到SD卡根目录\n   (已存在的文件将被覆盖)\n\n确定要继续吗？"):
            return
        do_snapshot = self._ask_snapshot_before_update(sd_path, "在线更新")
        self._set_busy(True)
        threading.Thread(target=self._do_download_and_extract, args=(sd_path, do_snapshot), daemon=True).start()

    def _do_download_and_extract(self, sd_path: str, do_snapshot: bool = True) -> None:
        temp_file_path: str | None = None
        temp_extract_dir: str | None = None
        options = self._get_preserve_options()

        try:
            if do_snapshot:
                self.root.after(0, lambda: self.status_var.set("正在创建快照..."))
                self._snapshot_mgr.create_snapshot_for_update(
                    sd_path, description="在线更新整合包",
                    progress_callback=lambda pct, msg: self.root.after(
                        0, lambda p=pct, m=msg: self.status_var.set(f"[{p}%] {m}"),
                    ),
                )

            # 构建白名单：更新时跳过这些路径，直接保护用户数据
            whitelist = build_whitelist_paths(options, sd_path)

            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
            temp_file_path = temp_file.name
            temp_file.close()
            temp_extract_dir = tempfile.mkdtemp()

            self.root.after(0, lambda: self.status_var.set("正在连接服务器..."))
            self.root.after(0, lambda: self.progress.config(mode="determinate"))

            # 使用大块分块下载以匹配浏览器速度
            start_time = time.time()
            req = urllib.request.Request(self.package_url.get(), headers={
                "User-Agent": "Mozilla/5.0",
                "Accept-Encoding": "identity",
                "Connection": "keep-alive",
            })
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                with open(temp_file_path, "wb") as f:
                    while True:
                        buf = resp.read(262144)  # 256KB chunks
                        if not buf:
                            break
                        f.write(buf)
                        downloaded += len(buf)
                        elapsed = max(time.time() - start_time, 0.001)
                        speed = downloaded / elapsed / 1048576  # MB/s
                        if total > 0:
                            pct = downloaded / total * 100
                            self.root.after(0, lambda p=pct, d=downloaded, t=total, s=speed: (
                                setattr(self.progress, "value", p),
                                self.status_var.set(
                                    f"正在下载: {d / 1048576:.1f} / {t / 1048576:.1f} MB "
                                    f"({p:.0f}%)  {s:.1f} MB/s"
                                ),
                            ))
                        else:
                            self.root.after(0, lambda d=downloaded, s=speed: (
                                self.status_var.set(
                                    f"正在下载: {d / 1048576:.1f} MB  {s:.1f} MB/s"
                                ),
                            ))

            self.root.after(0, lambda: self.status_var.set("正在解压文件..."))
            self.root.after(0, lambda: self.progress.config(mode="indeterminate"))
            self.root.after(0, lambda: self.progress.start())
            extract_archive(temp_file_path, temp_extract_dir)

            self.root.after(0, lambda: self.status_var.set("正在检测整合包结构..."))
            correct_root = find_package_root(temp_extract_dir)
            if correct_root is None:
                self.root.after(0, self._update_finished, False, "无法识别整合包结构")
                return

            self.root.after(0, lambda: self.status_var.set("正在复制文件到SD卡（跳过白名单项）..."))

            def cp_prog(pct: int, item: str) -> None:
                self.root.after(0, lambda: (self.status_var.set(f"[{pct}%] 正在复制: {item}..."), setattr(self.progress, "value", pct)))

            copy_tree(correct_root, sd_path, progress_callback=cp_prog, exclude_set=whitelist)

            self.root.after(0, self._update_finished, True, "")

        except urllib.error.URLError as e:
            self.root.after(0, self._update_finished, False, f"网络错误: {e}")
        except Exception as e:
            self.root.after(0, self._update_finished, False, f"发生错误: {e}")
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except OSError:
                    pass
            if temp_extract_dir and os.path.exists(temp_extract_dir):
                try:
                    shutil.rmtree(temp_extract_dir)
                except OSError:
                    pass

    def _local_update_from_wizard(self) -> None:
        sd_path = self.sd_path.get()
        if not sd_path or not os.path.exists(sd_path):
            messagebox.showwarning("警告", "请先选择有效的SD卡路径")
            return
        file_path = filedialog.askopenfilename(title="选择整合包文件", filetypes=[("压缩包", "*.zip *.7z *.rar"), ("所有文件", "*.*")])
        if file_path:
            self._start_local_update_with_file(file_path)

    def _start_local_update_with_file(self, file_path: str) -> None:
        sd_path = self.sd_path.get()
        if not sd_path or not os.path.exists(sd_path):
            messagebox.showwarning("警告", "请先选择有效的SD卡路径")
            return
        if self.is_downloading or self.is_cleaning:
            messagebox.showwarning("警告", "当前有操作正在进行")
            return
        if not os.path.exists(file_path):
            messagebox.showerror("错误", f"文件不存在：\n{file_path}")
            return

        filename = os.path.basename(file_path)
        archive_type = detect_archive_type(file_path, filename.lower())

        if archive_type == "firmware":
            if messagebox.askyesno("检测到固件", f"检测到选择的文件是固件：\n{filename}\n\n是否要安装此固件？"):
                self._install_firmware(file_path, sd_path)
            return
        if archive_type is None:
            result = messagebox.askyesnocancel("无法识别类型", f"无法识别：{filename}\n\n是 - 作为整合包\n否 - 作为固件\n取消 - 放弃")
            if result is None:
                return
            if not result:
                self._install_firmware(file_path, sd_path)
                return

        if not messagebox.askyesno("确认更新", f"即将解压整合包: {filename}\n安装到: {sd_path}\n\n确定继续吗？"):
            return
        do_snapshot = self._ask_snapshot_before_update(sd_path, "本地更新")
        self._set_busy(True)
        threading.Thread(target=self._do_local_update_package, args=(file_path, sd_path, do_snapshot), daemon=True).start()

    def _do_local_update_package(self, file_path: str, sd_path: str, do_snapshot: bool = True) -> None:
        temp_dir: str | None = None
        options = self._get_preserve_options()

        try:
            if do_snapshot:
                self.root.after(0, lambda: self.status_var.set("正在创建快照..."))
                self._snapshot_mgr.create_snapshot_for_update(
                    sd_path, description=f"本地更新: {os.path.basename(file_path)}",
                    progress_callback=lambda pct, msg: self.root.after(
                        0, lambda p=pct, m=msg: self.status_var.set(f"[{p}%] {m}"),
                    ),
                )

            # 构建白名单：更新时跳过这些路径，直接保护用户数据
            whitelist = build_whitelist_paths(options, sd_path)

            self.root.after(0, lambda: self.status_var.set("正在解压整合包..."))
            self.root.after(0, lambda: self.progress.config(mode="indeterminate"))
            self.root.after(0, lambda: self.progress.start())

            temp_dir = tempfile.mkdtemp()
            extract_archive(file_path, temp_dir)

            self.root.after(0, lambda: self.status_var.set("正在检测整合包结构..."))
            install_package_smart(temp_dir, sd_path, exclude_set=whitelist)

            self.root.after(0, self._update_finished, True, "")

        except Exception as e:
            self.root.after(0, self._update_finished, False, str(e))
        finally:
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except OSError:
                    pass

    def _update_finished(self, success: bool, error_msg: str) -> None:
        self._set_busy(False)
        self._op_logger.log_package_update(
            sd_path=self.sd_path.get(),
            source="在线" if self.current_step == 4 else "本地",
            success=success,
            error_msg=error_msg,
        )
        if success:
            self.status_var.set("整合包更新完成！")
            messagebox.showinfo("完成", "整合包更新成功！\n\n文件已解压到SD卡根目录。")
            if self.current_step == 4:
                self._wizard_go_to(5)
            else:
                self._preview_delete()
                self._prompt_eject_sd()
        else:
            self.status_var.set("更新失败")
            if self.current_step == 4:
                if messagebox.askyesno("在线更新失败", f"在线更新失败：{error_msg}\n\n是否改为本地更新？"):
                    self._local_update_from_wizard()
            else:
                messagebox.showerror("错误", f"整合包更新失败\n\n{error_msg}")
                self._preview_delete()

    # ==================== SD 卡空间 & 版本 ====================

    def _update_sd_space_info(self) -> None:
        sd_path = self.sd_path.get()
        if not sd_path or not os.path.exists(sd_path):
            self.space_label.config(text="SD卡空间: 请先选择SD卡")
            self.version_label.config(text="版本信息: 请先选择SD卡")
            return
        self._update_version_info(sd_path)
        space = get_sd_space(sd_path)
        if space:
            total, used, free = space
            self.space_label.config(text=f"SD卡空间: 总计 {total:.1f}GB | 已用 {used:.1f}GB | 剩余 {free:.1f}GB")
        else:
            self.space_label.config(text="SD卡空间: 无法获取")

    def _update_version_info(self, sd_path: str) -> None:
        """更新版本信息显示。"""
        info = detect_versions(sd_path)
        if not info.package3_exists:
            self.version_label.config(text="版本信息: 未检测到 Atmosphere")
            return
        if info.atmosphere is None:
            self.version_label.config(text="版本信息: 无法解析 (package3 格式异常)")
            return
        parts = [f"Atmosphere {info.atmosphere}"]
        if info.supported_hos:
            parts.append(f"支持固件 ≤ {info.supported_hos}")
        self.version_label.config(text="版本信息: " + " | ".join(parts))

    # ==================== 自定义保留 ====================

    def _show_custom_preserve_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        self._set_window_icon(dialog)
        dialog.title("自定义保留项目")
        dialog.resizable(True, True)
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_window(dialog, 550, 450)

        frame = ttk.Frame(dialog, padding="15")
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="添加需要保留的文件或文件夹（相对于SD卡根目录的路径）：").pack(anchor=tk.W)

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, font=("Microsoft YaHei UI", 10))
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=listbox.yview)
        for item in self.custom_preserve_items:
            listbox.insert(tk.END, item)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(0, 10))

        def add_folder() -> None:
            sd_path = self.sd_path.get()
            if not sd_path or not os.path.exists(sd_path):
                messagebox.showwarning("警告", "请先选择有效的SD卡路径", parent=dialog)
                return
            folder = filedialog.askdirectory(title="选择要保留的文件夹", initialdir=sd_path, parent=dialog)
            if folder:
                if os.path.normpath(folder).lower().startswith(os.path.normpath(sd_path).lower()):
                    rel = os.path.relpath(folder, sd_path)
                    if rel and rel != "." and rel not in self.custom_preserve_items:
                        self.custom_preserve_items.append(rel)
                        listbox.insert(tk.END, rel)
                else:
                    messagebox.showwarning("警告", "请选择SD卡内的文件夹", parent=dialog)

        def add_file() -> None:
            sd_path = self.sd_path.get()
            if not sd_path or not os.path.exists(sd_path):
                messagebox.showwarning("警告", "请先选择有效的SD卡路径", parent=dialog)
                return
            files = filedialog.askopenfilenames(title="选择要保留的文件", initialdir=sd_path, parent=dialog)
            for f in files:
                if os.path.normpath(f).lower().startswith(os.path.normpath(sd_path).lower()):
                    rel = os.path.relpath(f, sd_path)
                    if rel and rel not in self.custom_preserve_items:
                        self.custom_preserve_items.append(rel)
                        listbox.insert(tk.END, rel)

        def remove_selected() -> None:
            for idx in reversed(listbox.curselection()):
                item = listbox.get(idx)
                if item in self.custom_preserve_items:
                    self.custom_preserve_items.remove(item)
                listbox.delete(idx)

        def clear_all() -> None:
            if messagebox.askyesno("确认", "确定要清空所有自定义保留项目吗？", parent=dialog):
                self.custom_preserve_items.clear()
                listbox.delete(0, tk.END)

        ttk.Button(btn_frame, text="添加文件夹", command=add_folder).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="添加文件", command=add_file).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="移除选中", command=remove_selected).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="清空", command=clear_all).pack(side=tk.LEFT)

        bottom_frame = ttk.Frame(frame)
        bottom_frame.pack(fill=tk.X)

        def save_and_close() -> None:
            self._save_config()
            self.custom_count_label.config(text=f"(已选 {len(self.custom_preserve_items)} 项)")
            if self.sd_path.get():
                self._preview_delete()
            dialog.destroy()

        ttk.Button(bottom_frame, text="确定", command=save_and_close, width=10).pack(side=tk.RIGHT)
        ttk.Button(bottom_frame, text="取消", command=dialog.destroy, width=10).pack(side=tk.RIGHT, padx=(0, 10))

    # ==================== 拖放支持 ====================

    def _setup_drag_drop(self) -> None:
        try:
            import windnd
            windnd.hook_dropfiles(self.root, func=self._on_drop_files)
        except ImportError:
            pass
        except OSError as e:
            logger.debug("设置拖放失败: %s", e)

    def _on_drop_files(self, files: list) -> None:
        if not files:
            return
        for file_bytes in files:
            try:
                if isinstance(file_bytes, bytes):
                    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
                        try:
                            file_path = file_bytes.decode(enc)
                            break
                        except UnicodeDecodeError:
                            continue
                    else:
                        file_path = file_bytes.decode("utf-8", errors="ignore")
                else:
                    file_path = str(file_bytes)

                file_path = file_path.strip().strip('"').strip("'")
                if not os.path.exists(file_path):
                    messagebox.showerror("错误", f"文件不存在：\n{file_path}")
                    return
                if file_path.lower().endswith((".zip", ".7z", ".rar")):
                    self._handle_dropped_archive(file_path)
                    break
            except (UnicodeDecodeError, OSError) as e:
                messagebox.showerror("错误", f"处理文件路径时出错：\n{e}")
                return

    def _handle_dropped_archive(self, file_path: str) -> None:
        file_name = os.path.basename(file_path)
        sd_path = self.sd_path.get()
        if not sd_path or not os.path.exists(sd_path):
            messagebox.showwarning("警告", "请先选择SD卡路径后再拖放文件")
            return

        file_type = detect_archive_type(file_path, file_name.lower())
        if file_type == "firmware":
            if messagebox.askyesno("📦 安装固件", f"检测到固件文件：\n{file_name}\n\n目标SD卡：{sd_path}\n\n确定要安装此固件吗？"):
                self._install_firmware(file_path, sd_path)
        elif file_type == "package":
            if messagebox.askyesno("📦 安装整合包", f"检测到整合包文件：\n{file_name}\n\n目标SD卡：{sd_path}\n\n确定要安装此整合包吗？"):
                self._start_local_update_with_file(file_path)
        else:
            messagebox.showinfo("提示", f"无法识别压缩包类型：\n{file_name}\n\n支持拖放：固件（含 firmware/fw/固件）\n整合包（含 atmosphere+bootloader+hekate_ipl.ini）")

    # ==================== 操作日志 ====================

    def _show_op_log(self) -> None:
        """显示操作日志窗口。"""
        win = tk.Toplevel(self.root)
        self._set_window_icon(win)
        win.title("操作日志")
        win.resizable(True, True)
        win.transient(self.root)
        self._center_window(win, 600, 400)

        frame = ttk.Frame(win, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="📋 操作历史记录", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor=tk.W, pady=(0, 5))

        text_frame = ttk.Frame(frame)
        text_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        scrollbar = ttk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        log_text = tk.Text(text_frame, wrap=tk.WORD, yscrollcommand=scrollbar.set, state=tk.DISABLED, font=("Consolas", 10))
        log_text.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=log_text.yview)

        log_content = self._op_logger.get_records_for_display(50)
        log_text.config(state=tk.NORMAL)
        log_text.insert(tk.END, log_content)
        log_text.config(state=tk.DISABLED)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X)

        def clear_log() -> None:
            if messagebox.askyesno("确认", "确定要清空所有操作日志吗？", parent=win):
                self._op_logger.clear()
                log_text.config(state=tk.NORMAL)
                log_text.delete(1.0, tk.END)
                log_text.insert(tk.END, "日志已清空")
                log_text.config(state=tk.DISABLED)

        ttk.Button(btn_frame, text="清空日志", command=clear_log, width=10).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="关闭", command=win.destroy, width=10).pack(side=tk.RIGHT)

    # ==================== 快照管理 ====================

    def _show_snapshot_manager(self) -> None:
        """显示快照管理窗口。"""
        win = tk.Toplevel(self.root)
        self._set_window_icon(win)
        win.title("快照管理")
        win.resizable(True, True)
        win.transient(self.root)
        self._center_window(win, 650, 480)

        frame = ttk.Frame(win, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)

        total_mb = self._snapshot_mgr.get_total_size_mb()
        header = ttk.Frame(frame)
        header.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(header, text="📸 快照管理", font=("Microsoft YaHei UI", 12, "bold")).pack(side=tk.LEFT)
        size_label = ttk.Label(header, text=f"总占用: {total_mb:.1f} MB", foreground="gray")
        size_label.pack(side=tk.RIGHT)

        ttk.Label(frame, text="操作前自动创建快照，出错时可恢复到操作前的状态", foreground="gray").pack(anchor=tk.W, pady=(0, 5))

        columns = ("time", "op", "desc", "size")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=12)
        tree.heading("time", text="时间")
        tree.heading("op", text="操作类型")
        tree.heading("desc", text="说明")
        tree.heading("size", text="大小")
        tree.column("time", width=140, minwidth=120)
        tree.column("op", width=90, minwidth=80)
        tree.column("desc", width=280, minwidth=200)
        tree.column("size", width=80, minwidth=60)

        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=(0, 10))
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=(0, 10))

        snap_id_map: dict[str, str] = {}

        def refresh_list() -> None:
            tree.delete(*tree.get_children())
            snap_id_map.clear()
            snapshots = self._snapshot_mgr.list_snapshots()
            for snap in snapshots:
                sid = snap.get("snapshot_id", "")
                iid = tree.insert("", tk.END, values=(
                    snap.get("timestamp", ""),
                    snap.get("operation", ""),
                    snap.get("description", ""),
                    f'{snap.get("total_size_mb", 0):.1f} MB',
                ))
                snap_id_map[iid] = sid
            total = self._snapshot_mgr.get_total_size_mb()
            size_label.config(text=f"总占用: {total:.1f} MB")

        refresh_list()

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X)

        def on_restore() -> None:
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("提示", "请先选择一个快照", parent=win)
                return
            sid = snap_id_map.get(sel[0])
            if not sid:
                return
            vals = tree.item(sel[0], "values")
            desc = f"{vals[0]} - {vals[1]}" if vals else sid
            if not messagebox.askyesno(
                "确认恢复",
                f"确定要恢复到此快照吗？\n\n{desc}\n\n⚠️ 这将覆盖 SD 卡上的对应文件！",
                icon="warning",
                parent=win,
            ):
                return
            self.status_var.set("正在恢复快照...")
            self._set_busy(True)

            def do_restore() -> None:
                ok, msg = self._snapshot_mgr.restore_snapshot(
                    sid,
                    progress_callback=lambda pct, m: self.root.after(
                        0, lambda p=pct, txt=m: self.status_var.set(f"[{p}%] {txt}"),
                    ),
                )
                self.root.after(0, lambda: self._set_busy(False))
                if ok:
                    self.root.after(0, lambda: self.status_var.set("快照恢复完成"))
                    self.root.after(0, lambda: messagebox.showinfo("完成", msg, parent=win))
                    self.root.after(0, self._preview_delete)
                else:
                    self.root.after(0, lambda: self.status_var.set("快照恢复失败"))
                    self.root.after(0, lambda: messagebox.showerror("失败", msg, parent=win))

            threading.Thread(target=do_restore, daemon=True).start()

        def on_delete() -> None:
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("提示", "请先选择一个快照", parent=win)
                return
            sid = snap_id_map.get(sel[0])
            if not sid:
                return
            if not messagebox.askyesno("确认删除", "确定要删除此快照吗？\n删除后无法恢复。", parent=win):
                return
            self._snapshot_mgr.delete_snapshot(sid)
            refresh_list()

        def on_delete_all() -> None:
            if not snap_id_map:
                return
            if not messagebox.askyesno("确认清空", "确定要删除所有快照吗？\n\n⚠️ 删除后将无法回滚任何操作！", icon="warning", parent=win):
                return
            self._snapshot_mgr.delete_all_snapshots()
            refresh_list()

        ttk.Button(btn_frame, text="🔄 恢复选中", command=on_restore, width=12).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="🗑️ 删除选中", command=on_delete, width=12).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="清空全部", command=on_delete_all, width=10).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="关闭", command=win.destroy, width=8).pack(side=tk.RIGHT)

    # ==================== 弹出 SD 卡 ====================

    def _eject_sd_card(self) -> None:
        sd_path = self.sd_path.get()
        if not sd_path or not os.path.exists(sd_path):
            messagebox.showwarning("警告", "SD卡路径不存在")
            return
        success, msg = eject_sd_card(sd_path)
        if success:
            self.status_var.set(msg)
            messagebox.showinfo("成功", f"{msg}\n现在可以拔出SD卡了")
            self.sd_path.set("")
            self._update_preview("SD卡已弹出")
        else:
            messagebox.showerror("错误", f"{msg}\n请手动使用系统托盘安全弹出")

    def _prompt_eject_sd(self) -> None:
        if messagebox.askyesno("操作完成", "是否弹出SD卡？"):
            self._eject_sd_card()

    # ==================== 实时刷新 ====================

    def _start_auto_refresh(self) -> None:
        self._check_sd_changes()

    def _check_sd_changes(self) -> None:
        if not self.is_cleaning and not self.is_downloading:
            current = get_sd_state_snapshot(self.sd_path.get())
            if current is not None:
                if self.last_sd_state is None:
                    self.last_sd_state = current
                    self._preview_delete()
                elif current != self.last_sd_state:
                    self.last_sd_state = current
                    self._preview_delete()
            else:
                self.last_sd_state = None
        self.root.after(self.auto_refresh_interval, self._check_sd_changes)

    # ==================== 辅助方法 ====================

    def _set_busy(self, busy: bool) -> None:
        """统一设置忙碌状态（禁用/启用按钮、进度条）。"""
        if busy:
            self.is_downloading = True
            self.progress["value"] = 0
            for btn in (self.update_btn, self.clean_btn, self.firmware_btn):
                try:
                    btn.config(state=tk.DISABLED)
                except tk.TclError:
                    pass
        else:
            self.is_downloading = False
            self.progress.stop()
            self.progress.config(mode="determinate")
            self.progress["value"] = 0
            for btn in (self.update_btn, self.clean_btn, self.firmware_btn):
                try:
                    btn.config(state=tk.NORMAL)
                except tk.TclError:
                    pass
