#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Smart Table Hub PyQt6 版入口。

用法:
    python3 smart_table_quick_analysing_hub_qt.py [文件路径]
"""

import argparse
import os
import sys

import pandas as pd

pd.set_option("future.no_silent_downcasting", True)

# 环境变量可能指向其他 Python 安装的 Qt 插件（版本不匹配会导致启动失败），
# 强制使用当前 PyQt6 自带的插件目录。
import PyQt6
_plugin_root = os.path.join(os.path.dirname(PyQt6.__file__), "Qt6", "plugins")
os.environ["QT_PLUGIN_PATH"] = _plugin_root
os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_plugin_root, "platforms")

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from qtui.main_window import MainWindow

ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "assets", "smart_table_quick_analysing_hub_icon.png")


def main():
    parser = argparse.ArgumentParser(description="Smart Table Hub (PyQt6)")
    parser.add_argument("file", nargs="?", default=None, help="启动时打开的文件")
    parser.add_argument("-f", "--file", dest="file_opt", default=None,
                        help="启动时打开的文件（与位置参数等效，兼容旧版入口）")
    args = parser.parse_args()
    initial_file = args.file_opt or args.file

    app = QApplication(sys.argv)
    app.setApplicationName("Smart Table Hub")
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    window = MainWindow(initial_file=initial_file)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
