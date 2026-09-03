"""测试环境配置：Qt 平台插件、无头运行、配置文件隔离。

本机若同时装有 anaconda 的 Qt 库和 PyQt6/PySide6，QApplication 初始化时
可能找不到平台插件直接 abort——显式指向 PyQt6 自带的插件目录；
统一用 offscreen 平台无头运行（CI 与本地一致，也避免测试时闪窗口）。
必须在任何 Qt 模块导入前设置。

另外把"最近打开文件"配置指向临时目录：测试保存文件会写进这份配置，
而 MainWindow 启动 100ms 后会自动恢复上次会话——测试里就会莫名其妙地
去加载上一个测试的临时文件（嵌套 load_file 撞上模态框直接卡死），
顺带也避免测试污染用户真实的最近文件列表。
"""
import os

import PyQt6

_plugins = os.path.join(
    os.path.dirname(PyQt6.__file__), 'Qt6', 'plugins', 'platforms')
if os.path.isdir(_plugins):
    os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH', _plugins)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


import pytest


@pytest.fixture(autouse=True)
def isolated_recent_files(tmp_path_factory, monkeypatch):
    from qtui import file_io
    cfg = tmp_path_factory.mktemp("cfg") / "recent_files.json"
    monkeypatch.setattr(file_io, "RECENT_FILES_PATH", str(cfg))
