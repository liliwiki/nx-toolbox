"""
SD卡换包工具 - Nyx GUI 配套工具

功能模块:
  1. SD卡清理 - 清理并保留特定文件夹
  2. 固件更新 - 本地/在线固件安装
  3. 整合包更新 - 下载并安装大气层整合包

作者: 立里
"""

import tkinter as tk
from tkinter import ttk

from sd_cleaner.app import SDCleanerApp


def main() -> None:
    root = tk.Tk()
    SDCleanerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
