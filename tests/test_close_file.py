"""关闭文件（文件 > 关闭文件 / Cmd+W）：关闭当前文件回到空白表格，程序保持运行。

需要 QApplication（widgets）；CI 里用 QT_QPA_PLATFORM=offscreen 无头运行。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui.main_window import MainWindow


@pytest.fixture
def win():
    w = MainWindow()
    yield w
    # 清掉修改标志再关闭，否则 closeEvent 弹"是否保存"模态框，无头环境下挂死
    w.model.modified = False
    w.close()


@pytest.fixture
def xlsx_path(tmp_path):
    path = str(tmp_path / "sample.xlsx")
    pd.DataFrame({'A': [1, 2], 'B': [3, 4]}).to_excel(path, index=False)
    return path


def _load_sync(win, path):
    """绕过加载进度对话框的后台线程，同步加载文件。"""
    from qtui import file_io
    excel_file, sheets = file_io.load_workbook_lazy(path)
    df = file_io.read_sheet(excel_file, sheets[0])
    win.current_file = path
    win._excel_file = excel_file
    win.sheet_names = sheets
    win.current_sheet = sheets[0]
    win.model.set_dataframe(df)
    win.model.modified = False
    win._update_title()


def test_close_file_resets_to_blank(win, xlsx_path):
    _load_sync(win, xlsx_path)
    win.close_file()
    assert win.current_file is None
    assert win._excel_file is None
    assert win.sheet_names == []
    assert win.current_sheet is None
    assert win.model.modified is False
    assert "未命名" in win.windowTitle() or "Untitled" in win.windowTitle()


def test_close_file_releases_handle(win, xlsx_path):
    _load_sync(win, xlsx_path)
    win.close_file()
    # 句柄已释放：文件可被覆盖/删除
    os.remove(xlsx_path)


def test_close_file_without_open_file_is_noop_safe(win):
    win.close_file()  # 未打开文件时也不应报错
    assert win.current_file is None
