"""主窗口审查修复的回归测试：

- 菜单/工具栏信号的 checked 参数不能透传给带可选参数的槽（插入行崩溃、新建跳过确认）
- 已修改 / 仅存在于内存的 sheet 不能被 LRU 缓存淘汰
- "未保存修改"里选保存但取消另存对话框时，不能继续丢弃数据
- 预览框 400ms 延迟保存未触发就切换单元格，编辑不丢
- 剪贴板单个含逗号的值不拆列

需要 QApplication（widgets）；CI 里用 QT_QPA_PLATFORM=offscreen 无头运行。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QMessageBox, QFileDialog

_app = QApplication.instance() or QApplication([])

from qtui import main_window as mw_mod
from qtui.main_window import MainWindow, MAX_SHEET_CACHE


@pytest.fixture
def win():
    w = MainWindow()
    w.model.set_dataframe(pd.DataFrame({'A': [1.0, 2.0, 3.0], 'B': [4.0, 5.0, 6.0]}))
    w.model.modified = False
    yield w
    w.model.modified = False
    w.close()


def _action(win, text):
    for a in win.findChildren(QAction):
        if a.text() == text:
            return a
    raise AssertionError(f"action {text!r} not found")


class TestSlotCheckedArgument:
    def test_insert_row_action_does_not_receive_checked(self, win):
        before = len(win.model.df)
        _action(win, mw_mod.tr("插入行")).trigger()   # 曾以 position=False 崩溃
        assert len(win.model.df) == before + 1

    def test_new_file_action_prompts_when_modified(self, win, monkeypatch):
        win.model.modified = True
        asked = []
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.Cancel)
        _action(win, mw_mod.tr("新建")).trigger()      # 曾以 confirm=False 静默清空
        assert asked, "新建未弹出保存确认"
        assert len(win.model.df) == 3

    def test_copy_action_respects_header_checkbox(self, win):
        win.copy_headers_cb.setChecked(True)
        win.table.selectionModel().select(
            win.model.index(1, 0),
            win.table.selectionModel().SelectionFlag.ClearAndSelect)
        _action(win, mw_mod.tr("复制")).trigger()
        assert QApplication.clipboard().text().startswith("A")


class TestSheetCachePinning:
    def _multi_sheet(self, win, n):
        win.sheet_names = [f"S{i}" for i in range(n)]
        win.current_sheet = "S0"
        for name in win.sheet_names:
            win._cache_sheet(name, pd.DataFrame({'v': [name]}), pin=True)

    def test_new_sheets_survive_eviction(self, win):
        self._multi_sheet(win, MAX_SHEET_CACHE + 3)
        sheets, order, missing = win._collect_all_sheets()
        assert set(order) == set(sheets), f"丢失: {set(order) - set(sheets)}"
        assert missing == []

    def test_modified_sheet_is_pinned_on_switch(self, win, tmp_path):
        path = str(tmp_path / "many.xlsx")
        with pd.ExcelWriter(path) as xw:
            for i in range(MAX_SHEET_CACHE + 3):
                pd.DataFrame({'v': [i]}).to_excel(xw, sheet_name=f"S{i}", index=False)
        from qtui import file_io
        win.current_file = path
        win._excel_file, win.sheet_names = file_io.load_workbook_lazy(path)
        win.current_sheet = "S0"
        win.model.set_dataframe(file_io.read_sheet(win._excel_file, "S0"))
        win.model.setData(win.model.index(1, 0), "999")   # 编辑 S0
        assert win.model.modified
        for name in win.sheet_names[1:]:
            win.switch_sheet(name)
        sheets, order, missing = win._collect_all_sheets()
        assert "S0" in sheets and "S0" not in missing
        assert str(sheets["S0"].iat[0, 0]) in ("999", "999.0")


class TestSaveBeforeDiscard:
    def test_cancelled_save_as_blocks_discard(self, win, monkeypatch):
        win.model.modified = True
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: QMessageBox.StandardButton.Save)
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: ("", "")))
        assert win._check_save_before_discard() is False
        assert win.model.modified is True

    def test_save_untitled_switches_to_file(self, win, monkeypatch, tmp_path):
        target = str(tmp_path / "out.xlsx")
        win.model.modified = True
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (target, "")))
        assert win.save_file() is True
        assert os.path.exists(target)
        assert win.current_file == target
        assert win.model.modified is False


class TestPreviewPane:
    def test_pending_preview_edit_flushes_on_cell_change(self, win):
        win.table.setCurrentIndex(win.model.index(1, 0))
        win.cell_preview_text.setPlainText("typed")
        assert win._preview_save_timer.isActive()
        win.table.setCurrentIndex(win.model.index(2, 0))   # 400ms 内切换
        assert str(win.model.df.iat[0, 0]) == "typed"


class TestClipboardDelimiter:
    def test_single_value_with_comma_stays_one_cell(self, win):
        assert win._parse_clipboard_text("Smith, John") == [["Smith, John"]]

    def test_consistent_csv_lines_split(self, win):
        assert win._parse_clipboard_text("a,b\n1,2") == [["a", "b"], ["1", "2"]]

    def test_tab_wins_over_comma(self, win):
        assert win._parse_clipboard_text("a,b\t1") == [["a,b", "1"]]
