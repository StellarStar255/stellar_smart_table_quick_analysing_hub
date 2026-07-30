"""测试环境配置：Qt 平台插件与无头运行。

本机若同时装有 anaconda 的 Qt 库和 PyQt6/PySide6，QApplication 初始化时
可能找不到平台插件直接 abort——显式指向 PyQt6 自带的插件目录；
统一用 offscreen 平台无头运行（CI 与本地一致，也避免测试时闪窗口）。
必须在任何 Qt 模块导入前设置。
"""
import os

import PyQt6

_plugins = os.path.join(
    os.path.dirname(PyQt6.__file__), 'Qt6', 'plugins', 'platforms')
if os.path.isdir(_plugins):
    os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH', _plugins)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
