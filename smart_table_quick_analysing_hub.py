#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Smart Table Hub PyQt6 版入口。

用法:
    python3 smart_table_quick_analysing_hub.py [文件路径]
"""

import argparse
import datetime
import os
import sys
import traceback

import pandas as pd

pd.set_option("future.no_silent_downcasting", True)

# 环境变量可能指向其他 Python 安装的 Qt 插件（版本不匹配会导致启动失败），
# 强制使用当前 PyQt6 自带的插件目录。打包后（PyInstaller）插件已内置，跳过。
if not getattr(sys, "frozen", False):
    import PyQt6
    _plugin_root = os.path.join(os.path.dirname(PyQt6.__file__), "Qt6", "plugins")
    os.environ["QT_PLUGIN_PATH"] = _plugin_root
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_plugin_root, "platforms")

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from qtui.i18n import tr
from qtui.main_window import MainWindow
from version import __version__, APP_NAME

_BASE_DIR = (getattr(sys, "_MEIPASS", None)
             or os.path.dirname(os.path.abspath(__file__)))
ICON_PATH = os.path.join(_BASE_DIR, "assets",
                         "smart_table_quick_analysing_hub_icon.png")


CRASH_LOG_PATH = os.path.expanduser("~/.smart_table_hub/crash.log")


def _install_excepthook():
    """槽函数里未捕获的异常默认会让 PyQt6 调 qFatal() 直接终止进程（未保存
    数据全丢，打包版连堆栈都看不到）。改为：写日志 + 弹窗，程序继续运行。"""
    from PyQt6.QtWidgets import QApplication, QMessageBox

    state = {"showing": False}

    def hook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            os.makedirs(os.path.dirname(CRASH_LOG_PATH), exist_ok=True)
            with open(CRASH_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(f"==== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} "
                         f"v{__version__}\n{text}\n")
        except OSError:
            pass
        try:
            sys.__stderr__ and sys.__stderr__.write(text)
        except Exception:
            pass
        # 弹窗本身抛异常或弹窗期间再次触发时不递归
        if state["showing"] or QApplication.instance() is None:
            return
        state["showing"] = True
        try:
            tail = "\n".join(text.strip().splitlines()[-6:])
            QMessageBox.critical(
                None, tr("程序内部错误"),
                tr("发生了未预期的错误，操作可能未完成。\n"
                   "建议立即保存工作。详细日志：{}\n\n{}").format(CRASH_LOG_PATH, tail))
        except Exception:
            pass
        finally:
            state["showing"] = False

    sys.excepthook = hook


def main():
    parser = argparse.ArgumentParser(description="Smart Table Hub (PyQt6)")
    parser.add_argument("file", nargs="?", default=None,
                        help=tr("启动时打开的文件"))
    parser.add_argument("-f", "--file", dest="file_opt", default=None,
                        help=tr("启动时打开的文件（与位置参数等效，兼容旧版入口）"))
    args = parser.parse_args()
    initial_file = args.file_opt or args.file

    _install_excepthook()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    window = MainWindow(initial_file=initial_file)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
