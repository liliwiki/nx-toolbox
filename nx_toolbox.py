#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NS工具箱 - 主入口程序
集成多种Switch相关工具的统一入口
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

try:
    import winsound
    HAS_SOUND = True
except ImportError:
    HAS_SOUND = False

from sd_cleaner.common import VERSION

logger = logging.getLogger(__name__)

# ---- 路径 ----
if getattr(sys, "frozen", False):
    _BASE_DIR = sys._MEIPASS
    _BUNDLE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    _BUNDLE_DIR = _BASE_DIR

UPDATE_MANIFEST_URL = "https://download.liliwiki.com/toolbox/version.json"
APPDATA_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "NXToolbox"
)
os.makedirs(APPDATA_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(APPDATA_DIR, "toolbox_config.json")

# ---- 8-bit 标语 ----
SLOGANS = [
    "READY PLAYER ONE",
    "INSERT COIN TO START",
    "A LINK TO THE SWITCH",
    "POWER ON",
    "WAKE UP, LINK!",
    "IT'S DANGEROUS TO GO ALONE!",
    "HEY! LISTEN!",
    "THE LEGEND OF TOOLBOX",
]

CARD_SIZE = 140
CARD_PADDING = 15
CARD_GAP = 8
CARDS_PER_ROW = 3

PIXEL_COLORS = {
    "bg": "#0f0f23",
    "card_bg": "#1a1a3e",
    "card_border": "#4a4a8a",
    "accent": "#ff6b6b",
    "accent2": "#ffd93d",
    "text": "#ffffff",
    "text_dim": "#6a6a9a",
    "highlight": "#6b5bff",
    "green": "#6bff6b",
}


class NXToolbox:
    """主工具箱窗口 - 复古游戏风格启动器。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("NS工具箱")
        self.root.resizable(False, False)

        self._icon_path = os.path.join(_BUNDLE_DIR, "icon.ico")
        self._set_window_icon(self.root)

        self.is_dark_theme = tk.BooleanVar(value=True)
        self.sound_enabled = tk.BooleanVar(value=True)
        self.auto_check_update = tk.BooleanVar(value=True)
        self.update_manifest_url = tk.StringVar(value=UPDATE_MANIFEST_URL)
        self.drag_data: dict = {"index": None, "widget": None}

        self.tools: list[dict] = [
            {"name": "<换包工具>", "file": "SD_Cleaner.py", "icon": "[SD]",
             "desc": "SD卡整理/备份/更新整合包", "key": "1"},
            {"name": "敬请期待", "file": None, "icon": "[?]",
             "desc": "更多工具开发中...", "key": "2"},
            {"name": "敬请期待", "file": None, "icon": "[?]",
             "desc": "更多工具开发中...", "key": "3"},
            {"name": "敬请期待", "file": None, "icon": "[?]",
             "desc": "更多工具开发中...", "key": "4"},
            {"name": "敬请期待", "file": None, "icon": "[?]",
             "desc": "更多工具开发中...", "key": "5"},
            {"name": "敬请期待", "file": None, "icon": "[?]",
             "desc": "更多工具开发中...", "key": "6"},
        ]

        self.load_config()
        self.apply_theme()

        self.welcome_frame: tk.Frame | None = None
        self.tools_frame: tk.Frame | None = None
        self.blink_state = True

        self._played_startup = False
        self.root.after(100, self._show_pending_changelog)
        self.root.after(2000, self._poll_cleanup_artifacts)

        self.create_welcome_page()

        if self.auto_check_update.get():
            self.root.after(3000, self._auto_check_update)

    # ================================================================
    # 启动后任务
    # ================================================================
    def _show_pending_changelog(self) -> None:
        from update_checker import get_pending_changelog
        text = get_pending_changelog()
        if text:
            self.root.after(500, lambda: messagebox.showinfo("更新日志", text))

    def _poll_cleanup_artifacts(self) -> None:
        import threading
        from update_checker import try_cleanup_artifacts
        threading.Thread(target=try_cleanup_artifacts, daemon=True).start()

    # ================================================================
    # 配置持久化
    # ================================================================
    def load_config(self) -> None:
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.is_dark_theme.set(cfg.get("is_dark_theme", True))
            self.sound_enabled.set(cfg.get("sound_enabled", True))
            self.auto_check_update.set(cfg.get("auto_check_update", True))
            self.update_manifest_url.set(cfg.get("update_manifest_url", UPDATE_MANIFEST_URL))
            tools = cfg.get("tools")
            if isinstance(tools, list) and tools:
                self.tools = tools
        except (json.JSONDecodeError, OSError):
            pass

    def save_config(self) -> None:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "is_dark_theme": self.is_dark_theme.get(),
                    "sound_enabled": self.sound_enabled.get(),
                    "auto_check_update": self.auto_check_update.get(),
                    "update_manifest_url": self.update_manifest_url.get(),
                    "tools": self.tools,
                }, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ================================================================
    # 工具方法
    # ================================================================
    def center_window(self, width: int, height: int) -> None:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - width) // 2
        y = (sh - height) // 2
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _set_window_icon(self, win: tk.Toplevel | tk.Tk) -> None:
        if os.path.exists(self._icon_path):
            try:
                win.iconbitmap(self._icon_path)
            except tk.TclError:
                pass

    PIXEL_COLORS = PIXEL_COLORS

    def _bind_hover(self, widget: tk.Widget, enter_color: str, leave_color: str) -> None:
        widget.bind("<Enter>", lambda e, w=widget, c=enter_color: w.configure(bg=c), add="+")
        widget.bind("<Leave>", lambda e, w=widget, c=leave_color: w.configure(bg=c), add="+")

    def on_closing(self) -> None:
        self.save_config()
        self.root.destroy()

    def show_tooltip(self, widget: tk.Widget, text: str) -> None:
        pass  # stub

    def hide_tooltip(self) -> None:
        pass  # stub

    def play_sound(self, name: str) -> None:
        if not self.sound_enabled.get() or not HAS_SOUND:
            return
        tones = {"start": (600, 100), "select": (1000, 80), "back": (400, 60)}
        freq, dur = tones.get(name, (800, 80))
        try:
            winsound.Beep(freq, dur)
        except RuntimeError:
            pass

    # ================================================================
    # 欢迎页面
    # ================================================================
    def create_welcome_page(self) -> None:
        """创建欢迎页面 - 复古游戏风格。"""
        self.center_window(450, 380)
        self.root.configure(bg=PIXEL_COLORS["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.welcome_frame = tk.Frame(self.root, bg=PIXEL_COLORS["bg"])
        self.welcome_frame.pack(fill=tk.BOTH, expand=True)

        if not hasattr(self, "_played_startup"):
            self._played_startup = True
        self.play_sound("start")

        center_frame = tk.Frame(self.welcome_frame, bg=PIXEL_COLORS["bg"])
        center_frame.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        border_top = tk.Label(
            center_frame, text="╔════════════════════════╗",
            font=("Consolas", 12), fg=PIXEL_COLORS["card_border"], bg=PIXEL_COLORS["bg"],
        )
        border_top.pack()

        title_label = tk.Label(
            center_frame, text="[  ♦  ]",
            font=("Consolas", 32, "bold"), fg=PIXEL_COLORS["accent"], bg=PIXEL_COLORS["bg"],
        )
        title_label.pack(pady=(10, 0))

        title_text = tk.Label(
            center_frame, text="SWITCH TOOLBOX",
            font=("Consolas", 22, "bold"), fg=PIXEL_COLORS["accent2"], bg=PIXEL_COLORS["bg"],
        )
        title_text.pack(pady=(8, 3))

        subtitle = tk.Label(
            center_frame, text="< by 立里 >",
            font=("Consolas", 10), fg=PIXEL_COLORS["text_dim"], bg=PIXEL_COLORS["bg"],
        )
        subtitle.pack()

        version_label = tk.Label(
            center_frame, text=f"v{VERSION}",
            font=("Consolas", 9), fg=PIXEL_COLORS["text_dim"], bg=PIXEL_COLORS["bg"],
        )
        version_label.pack(pady=(2, 0))

        slogan = random.choice(SLOGANS)
        slogan_label = tk.Label(
            center_frame, text=f"~ {slogan} ~",
            font=("Consolas", 9), fg=PIXEL_COLORS["green"], bg=PIXEL_COLORS["bg"],
        )
        slogan_label.pack(pady=(5, 0))

        border_bottom = tk.Label(
            center_frame, text="╚════════════════════════╝",
            font=("Consolas", 12), fg=PIXEL_COLORS["card_border"], bg=PIXEL_COLORS["bg"],
        )
        border_bottom.pack(pady=(10, 0))

        def on_start_click() -> None:
            self.show_tools_page()

        self.blink_state = True

        enter_btn = tk.Button(
            center_frame,
            text=">>> PRESS START <<<",
            font=("Consolas", 14, "bold"),
            bg=PIXEL_COLORS["card_bg"],
            fg=PIXEL_COLORS["green"],
            activebackground=PIXEL_COLORS["highlight"],
            activeforeground=PIXEL_COLORS["text"],
            relief=tk.RAISED, bd=2,
            cursor="hand2", padx=20, pady=8,
            command=on_start_click,
        )
        enter_btn.pack(pady=(20, 0))

        def blink() -> None:
            if self.welcome_frame and self.welcome_frame.winfo_exists():
                self.blink_state = not self.blink_state
                enter_btn.configure(
                    fg=PIXEL_COLORS["accent2"] if self.blink_state else PIXEL_COLORS["green"]
                )
                self.root.after(600, blink)

        blink()

    # ================================================================
    # 工具页面
    # ================================================================
    def show_tools_page(self) -> None:
        """显示工具列表页面 - 复古游戏风格。"""
        if self.welcome_frame:
            self.welcome_frame.destroy()
            self.welcome_frame = None

        cols = min(len(self.tools), CARDS_PER_ROW)
        rows = math.ceil(len(self.tools) / CARDS_PER_ROW)
        card_cell = CARD_SIZE + 2 * CARD_GAP
        width = max(cols * card_cell + 2 * CARD_PADDING + 20, 550)
        height = rows * card_cell + 2 * CARD_PADDING + 20 + 130

        self.center_window(width, height)
        self.root.configure(bg=PIXEL_COLORS["bg"])
        self.root.bind("<Key>", self.on_key_press)
        self.root.bind("<Escape>", lambda e: self.back_to_welcome())

        self.tools_frame = tk.Frame(self.root, bg=PIXEL_COLORS["bg"])
        self.tools_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 标题
        header_frame = tk.Frame(self.tools_frame, bg=PIXEL_COLORS["bg"])
        header_frame.pack(fill=tk.X)

        title_label = tk.Label(
            header_frame, text="< SELECT YOUR TOOL >",
            font=("Consolas", 14, "bold"), fg=PIXEL_COLORS["accent2"], bg=PIXEL_COLORS["bg"],
        )
        title_label.pack()

        hint_label = tk.Label(
            header_frame, text="[1-6] 快捷选择  [ESC] 返回  [右键拖拽排序]",
            font=("Microsoft YaHei UI", 9), fg=PIXEL_COLORS["text_dim"], bg=PIXEL_COLORS["bg"],
        )
        hint_label.pack(pady=(2, 10))

        # 卡片容器
        cards_container = tk.Frame(
            self.tools_frame, bg=PIXEL_COLORS["bg"],
            highlightbackground=PIXEL_COLORS["card_border"],
            highlightthickness=2, relief=tk.RIDGE, bd=2,
        )
        cards_container.pack(fill=tk.BOTH, expand=True)

        cards_inner = tk.Frame(cards_container, bg=PIXEL_COLORS["bg"])
        cards_inner.pack(fill=tk.BOTH, padx=2, pady=2)

        grid_frame = tk.Frame(cards_inner, bg=PIXEL_COLORS["bg"])
        grid_frame.pack(padx=CARD_PADDING, pady=CARD_PADDING)

        # 确保所有列等宽
        for c in range(CARDS_PER_ROW):
            grid_frame.grid_columnconfigure(c, weight=1, uniform="card_col")

        for idx, tool in enumerate(self.tools):
            row, col = divmod(idx, CARDS_PER_ROW)
            card = self.create_tool_card(grid_frame, tool, idx, row, col)
            card.grid(row=row, column=col, padx=CARD_GAP, pady=CARD_GAP)

        # 底部按钮
        footer_frame = tk.Frame(self.tools_frame, bg=PIXEL_COLORS["bg"])
        footer_frame.pack(fill=tk.X, pady=(10, 5))

        back_btn = tk.Button(
            footer_frame, text="< BACK",
            font=("Consolas", 10, "bold"),
            bg=PIXEL_COLORS["card_bg"], fg=PIXEL_COLORS["text_dim"],
            activebackground=PIXEL_COLORS["highlight"],
            activeforeground=PIXEL_COLORS["text"],
            relief=tk.RAISED, bd=2,
            cursor="hand2", padx=15, pady=4,
            command=self.back_to_welcome,
        )
        back_btn.pack(side=tk.LEFT)

        # 音效按钮
        sound_text = "[♪ ON]" if self.sound_enabled.get() else "[♪ OFF]"
        sound_color = PIXEL_COLORS["green"] if self.sound_enabled.get() else PIXEL_COLORS["text_dim"]

        def update_sound_btn() -> None:
            sound_btn.configure(
                text="[♪ ON]" if self.sound_enabled.get() else "[♪ OFF]",
                fg=PIXEL_COLORS["green"] if self.sound_enabled.get() else PIXEL_COLORS["text_dim"],
            )

        def toggle_sound() -> None:
            self.sound_enabled.set(not self.sound_enabled.get())
            update_sound_btn()
            self.play_sound("select")

        sound_btn = tk.Button(
            footer_frame, text=sound_text,
            font=("Consolas", 10),
            bg=PIXEL_COLORS["card_bg"], fg=sound_color,
            activebackground=PIXEL_COLORS["highlight"],
            activeforeground=PIXEL_COLORS["text"],
            relief=tk.RAISED, bd=2,
            cursor="hand2", padx=10, pady=4,
            command=toggle_sound,
        )
        sound_btn.pack(side=tk.RIGHT, padx=(5, 0))

        update_btn = tk.Button(
            footer_frame, text="[↻ 检查更新]",
            font=("Consolas", 10),
            bg=PIXEL_COLORS["card_bg"], fg=PIXEL_COLORS["accent2"],
            activebackground=PIXEL_COLORS["highlight"],
            activeforeground=PIXEL_COLORS["text"],
            relief=tk.RAISED, bd=2,
            cursor="hand2", padx=10, pady=4,
            command=self.manual_check_update,
        )
        update_btn.pack(side=tk.RIGHT)

        self._bind_hover(sound_btn, PIXEL_COLORS["highlight"], PIXEL_COLORS["card_bg"])
        self._bind_hover(update_btn, PIXEL_COLORS["highlight"], PIXEL_COLORS["card_bg"])

    def back_to_welcome(self) -> None:
        """返回欢迎页面。"""
        self.play_sound("back")
        self.root.unbind("<Key>")
        self.root.unbind("<Escape>")
        if self.tools_frame:
            self.tools_frame.destroy()
            self.tools_frame = None
        self.create_welcome_page()

    def on_key_press(self, event: tk.Event) -> None:
        if event.char in "123456":
            idx = int(event.char) - 1
            if idx < len(self.tools):
                self.launch_tool(idx)

    # ================================================================
    # 工具卡片
    # ================================================================
    def create_tool_card(self, parent: tk.Frame, tool: dict, index: int, row: int, col: int) -> tk.Frame:
        """创建正方形工具卡片 - 复古游戏风格。"""
        card = tk.Frame(
            parent, width=CARD_SIZE, height=CARD_SIZE,
            bg=PIXEL_COLORS["card_bg"],
            highlightbackground=PIXEL_COLORS["card_border"],
            highlightthickness=1, bd=0,
        )
        card.pack_propagate(False)
        card.grid_propagate(False)

        if tool.get("file") is None:
            key_color = PIXEL_COLORS["text_dim"]
            name_color = PIXEL_COLORS["text_dim"]
        else:
            key_color = PIXEL_COLORS["green"]
            name_color = PIXEL_COLORS["text"]

        key = tool.get("key", "?")
        tk.Label(
            card, text=f"[{key}] {tool.get('icon', '[?]')}",
            font=("Consolas", 12, "bold"), fg=key_color, bg=PIXEL_COLORS["card_bg"],
        ).pack(pady=(22, 5))

        tk.Label(
            card, text=tool.get("name", ""),
            font=("Microsoft YaHei UI", 11, "bold"), fg=name_color, bg=PIXEL_COLORS["card_bg"],
        ).pack()

        tk.Label(
            card, text=tool.get("desc", ""),
            font=("Microsoft YaHei UI", 9), fg=PIXEL_COLORS["text_dim"],
            bg=PIXEL_COLORS["card_bg"], wraplength=CARD_SIZE - 16,
        ).pack(pady=(3, 0))

        # 事件：只在可用工具上绑定交互
        if tool.get("file"):
            card.configure(cursor="hand2")

            for w in [card] + list(card.winfo_children()):
                w.bind("<Button-1>", lambda e, i=index: self.launch_tool(i))
                w.bind("<ButtonPress-3>", lambda e, i=index: self._drag_start(e, i))
                w.bind("<ButtonRelease-3>", lambda e, i=index: self._drag_end(e, i))

        return card

    def _drag_start(self, event: tk.Event, index: int) -> None:
        self.drag_data["index"] = index
        self.drag_data["widget"] = event.widget

    def _drag_end(self, event: tk.Event, target_index: int) -> None:
        src = self.drag_data.get("index")
        if src is not None:
            self.swap_tools(src, target_index)
        self.drag_data = {"index": None, "widget": None}

    def swap_tools(self, idx1: int, idx2: int) -> None:
        if idx1 != idx2 and 0 <= idx1 < len(self.tools) and 0 <= idx2 < len(self.tools):
            self.tools[idx1], self.tools[idx2] = self.tools[idx2], self.tools[idx1]
            self.save_config()
            self.show_tools_page()

    # ================================================================
    # 启动工具
    # ================================================================
    def launch_tool(self, index: int) -> None:
        tool = self.tools[index]
        if tool.get("file") is None:
            messagebox.showinfo("提示", "该工具暂未开放")
            return
        self.play_sound("select")
        self.show_loading(tool["name"])

    def show_loading(self, tool_name: str) -> None:
        load_dlg = tk.Toplevel(self.root)
        load_dlg.title("LOADING...")
        load_dlg.resizable(False, False)
        load_dlg.configure(bg=PIXEL_COLORS["bg"])

        w, h = 300, 100
        sw, sh = load_dlg.winfo_screenwidth(), load_dlg.winfo_screenheight()
        load_dlg.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        tk.Label(
            load_dlg, text=f"正在启动 {tool_name}...",
            font=("Consolas", 11), fg=PIXEL_COLORS["accent2"], bg=PIXEL_COLORS["bg"],
        ).pack(pady=(20, 3))
        tk.Label(
            load_dlg, text="[  LOADING...  ]",
            font=("Consolas", 9), fg=PIXEL_COLORS["green"], bg=PIXEL_COLORS["bg"],
        ).pack()

        def _launch() -> None:
            load_dlg.destroy()
            self._do_launch_tool(tool_name)

        self.root.after(300, _launch)

    def _do_launch_tool(self, tool_name: str) -> None:
        try:
            import SD_Cleaner
            self.root.withdraw()
            tool_window = tk.Toplevel(self.root)
            self._set_window_icon(tool_window)

            def on_tool_close() -> None:
                tool_window.destroy()
                self._on_tool_closed()

            tool_window.protocol("WM_DELETE_WINDOW", on_tool_close)
            SD_Cleaner.SDCleanerApp(tool_window)
        except (ImportError, OSError) as e:
            logger.error("启动换包工具失败: %s", e)
            messagebox.showerror("错误", f"启动换包工具失败: {e}")
            self.root.deiconify()
            self.show_tools_page()

    def _on_tool_closed(self) -> None:
        self.root.deiconify()
        self.show_tools_page()

    # ================================================================
    # 更新检查
    # ================================================================
    def manual_check_update(self) -> None:
        url = self.update_manifest_url.get()
        threading.Thread(target=self._do_check_update, args=(True, url), daemon=True).start()

    def _auto_check_update(self) -> None:
        url = self.update_manifest_url.get()
        threading.Thread(target=self._do_check_update, args=(False, url), daemon=True).start()

    def _do_check_update(self, show_no_update: bool, manifest_url: str) -> None:
        from update_checker import check_for_update

        result = check_for_update(VERSION, manifest_url)
        if result is None:
            if show_no_update:
                self.root.after(0, lambda: messagebox.showinfo(
                    "检查更新", "当前已是最新版本"))
            return

        new_ver = result["version"]
        changelog = result.get("changelog", "")
        self.root.after(0, lambda: self._prompt_update(
            result["download_url"], new_ver, changelog))

    def _prompt_update(self, download_url: str, new_ver: str, changelog: str) -> None:
        msg = f"发现新版本 v{new_ver}\n当前版本 v{VERSION}\n\n{changelog}\n\n是否下载更新？"
        if not messagebox.askyesno("发现新版本", msg):
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("正在更新...")
        dialog.resizable(False, False)
        dialog.configure(bg=PIXEL_COLORS["bg"])

        w, h = 400, 120
        sw, sh = dialog.winfo_screenwidth(), dialog.winfo_screenheight()
        dialog.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        status_label = tk.Label(
            dialog, text="正在连接服务器...",
            font=("Microsoft YaHei UI", 10), fg=PIXEL_COLORS["text"],
            bg=PIXEL_COLORS["bg"],
        )
        status_label.pack(pady=(15, 8))

        progress = ttk.Progressbar(dialog, mode="determinate", length=320)
        progress.pack(pady=3)

        speed_label = tk.Label(
            dialog, text="", font=("Consolas", 8),
            fg=PIXEL_COLORS["text_dim"], bg=PIXEL_COLORS["bg"],
        )
        speed_label.pack()

        from update_checker import download_update, install_update
        import tempfile

        old_exe = self._get_exe_path()
        new_exe = os.path.join(tempfile.gettempdir(), "NS工具箱_update.exe")

        def prog_cb(pct: int, dl: float, tot: float) -> None:
            dialog.after(0, lambda: progress.config(value=pct))
            dialog.after(0, lambda: status_label.config(
                text=f"正在下载: {dl:.1f} / {tot:.1f} MB"))
            dialog.after(0, lambda: speed_label.config(text=f"{pct}%"))

        def do_download() -> None:
            ok = download_update(download_url, new_exe, progress_callback=prog_cb)
            if ok:
                dialog.after(0, lambda: status_label.config(text="正在准备更新..."))
                dialog.after(0, lambda: progress.config(value=100))
                dialog.after(0, lambda: status_label.config(text="正在安装..."))
                dialog.after(0, lambda: progress.config(value=100))
                dialog.after(0, lambda: speed_label.config(text=""))
                # install_update 会立刻替换并自重启，不会返回
                ok2 = install_update(new_exe, old_exe, changelog)
                if ok2:
                    dialog.after(0, lambda: status_label.config(
                        text="安装完成，正在重启..."))
                else:
                    dialog.after(0, lambda: status_label.config(text="更新准备失败"))
                    dialog.after(0, lambda: speed_label.config(text="请稍后重试"))
                    dialog.after(2000, dialog.destroy)
            else:
                dialog.after(0, lambda: status_label.config(
                    text="下载失败，请检查网络"))
                dialog.after(2000, dialog.destroy)

        threading.Thread(target=do_download, daemon=True).start()

    def _get_exe_path(self) -> str:
        if getattr(sys, "frozen", False):
            return sys.executable
        return os.path.join(_BUNDLE_DIR, "NS工具箱.exe")

    # ================================================================
    # 主题
    # ================================================================
    def apply_theme(self) -> None:
        self.root.configure(bg=PIXEL_COLORS["bg"])


# ================================================================
# 入口
# ================================================================
def main() -> None:
    root = tk.Tk()
    app = NXToolbox(root)
    root.mainloop()


if __name__ == "__main__":
    main()
