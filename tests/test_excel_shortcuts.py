# -*- coding: utf-8 -*-
"""Excel 常用快捷键：剪切、填充、Ctrl+Enter、F4、自动求和、整行整列选择、
定位、Ctrl+Home/End、按选区插入/删除、Sheet 切换、菜单快捷键绑定。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QApplication, QLineEdit, QMessageBox, QInputDialog
from PyQt6.QtTest import QTest

_app = QApplication.instance() or QApplication([])

from qtui.i18n import tr
from qtui.main_window import MainWindow, _ExcelTableView

CTRL = Qt.KeyboardModifier.ControlModifier
CTRL_SHIFT = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier


@pytest.fixture
def win():
    w = MainWindow()
    w.model.set_dataframe(pd.DataFrame({
        "A": [1, 2, 3, 4],
        "B": [10, 20, 30, 40],
        "C": ["x", "y", "z", "w"],
    }))
    w.show()
    yield w
    w.model.modified = False
    w.close()


def select(win, top, left, bottom, right):
    from PyQt6.QtCore import QItemSelection, QItemSelectionModel
    m = win.model
    win.table.setCurrentIndex(m.index(top, left))     # 会清选区，所以先设 current
    win.table.selectionModel().select(
        QItemSelection(m.index(top, left), m.index(bottom, right)),
        QItemSelectionModel.SelectionFlag.ClearAndSelect)


def sel_rect(win):
    idx = win.table.selectionModel().selectedIndexes()
    return (min(i.row() for i in idx), min(i.column() for i in idx),
            max(i.row() for i in idx), max(i.column() for i in idx))


class TestMenuBindings:
    def _shortcuts(self, win):
        out = {}
        for a in win.findChildren(QAction):
            if a.text():
                out[a.text()] = {k.toString() for k in a.shortcuts()}
        return out

    def test_expected_bindings(self, win):
        sc = self._shortcuts(win)
        expect = {
            tr("剪切"): {"Ctrl+X"}, tr("重做"): {"Ctrl+Shift+Z", "Ctrl+Y"},
            tr("向下填充"): {"Ctrl+D"}, tr("向右填充"): {"Ctrl+R"},
            tr("自动求和"): {"Alt+="}, tr("插入日期"): {"Ctrl+;"},
            tr("插入时间"): {"Ctrl+Shift+;", "Ctrl+:"},
            tr("选择整行"): {"Shift+Space"}, tr("选择整列"): {"Ctrl+Space", "Meta+Space"},
            tr("定位..."): {"Ctrl+G", "F5"}, tr("替换..."): {"Ctrl+H"},
            tr("按选区插入行/列"): {"Ctrl+Shift+=", "Ctrl++"},
            tr("按选区删除行/列"): {"Ctrl+Shift+-", "Ctrl+_"},
            tr("下一个Sheet"): {"Ctrl+PgDown"}, tr("上一个Sheet"): {"Ctrl+PgUp"},
        }
        for text, keys in expect.items():
            assert sc.get(text) == keys, text


class TestCutAndClear:
    def test_cut_copies_then_clears(self, win):
        select(win, 1, 0, 2, 0)
        win.cut_selection()
        assert QApplication.clipboard().text().split()[-2:] == ["1", "2"]
        assert pd.isna(win.model.df.iat[0, 0]) and pd.isna(win.model.df.iat[1, 0])
        assert win.model.df.iat[2, 0] == 3
        win.undo()
        assert win.model.df.iat[0, 0] == 1

    def test_clear_skips_header_row(self, win):
        select(win, 0, 1, 1, 1)
        win.clear_selected_cells()
        assert list(win.model.df.columns) == ["A", "B", "C"]
        assert pd.isna(win.model.df.iat[0, 1])


class TestFill:
    def test_fill_down_from_first_row_of_selection(self, win):
        win.model.setData(win.model.index(1, 1), "=A2*2")
        select(win, 1, 1, 4, 1)
        win.table.fill_selection(True)
        assert win.model.formulas[(3, 1)] == "=A5*2"
        assert win.model.df["B"].tolist() == [2, 4, 6, 8]

    def test_fill_down_single_cell_copies_from_above(self, win):
        select(win, 2, 2, 2, 2)
        win.table.fill_selection(True)
        assert win.model.df.iat[1, 2] == "x"

    def test_fill_right(self, win):
        select(win, 1, 0, 1, 2)
        win.table.fill_selection(False)
        assert win.model.df.iloc[0].tolist() == [1, 1, "1"]

    def test_fill_down_does_nothing_on_header(self, win):
        select(win, 1, 0, 1, 0)      # 上方只有表头行
        win.table.fill_selection(True)
        assert win.model.df.iat[0, 0] == 1


class TestCtrlEnter:
    def test_fills_selection_with_shifted_formula(self, win):
        select(win, 1, 2, 3, 2)
        win.table.edit(win.model.index(1, 2))
        editor = win.table.findChild(QLineEdit)
        editor.setText("=A2+B2")
        QTest.keyClick(editor, Qt.Key.Key_Return, CTRL)
        assert win.model.formulas[(0, 2)] == "=A2+B2"
        assert win.model.formulas[(2, 2)] == "=A4+B4"
        assert win.model.df["C"].tolist()[:3] == [11, 22, 33]
        assert win.table.currentIndex().row() == 1     # 不移动


class TestF4:
    @pytest.mark.parametrize("ref,expected", [
        ("A1", "$A$1"), ("$A$1", "A$1"), ("A$1", "$A1"), ("$A1", "A1"),
        ("A2:B3", "$A$2:$B$3"), ("$A$2:$B$3", "A$2:B$3"),
    ])
    def test_cycle(self, ref, expected):
        assert _ExcelTableView._cycle_absolute(ref) == expected

    def test_f4_toggles_ref_under_caret(self, win):
        win.table.edit(win.model.index(1, 2))
        editor = win.table.findChild(QLineEdit)
        editor.setText("=SUM(A2:A5)+B2")
        editor.setCursorPosition(8)          # 在 A2:A5 中
        QTest.keyClick(editor, Qt.Key.Key_F4)
        assert editor.text() == "=SUM($A$2:$A$5)+B2"
        editor.setCursorPosition(len(editor.text()))
        QTest.keyClick(editor, Qt.Key.Key_F4)
        assert editor.text() == "=SUM($A$2:$A$5)+$B$2"


class TestAutoSum:
    def test_sums_numbers_above(self, win):
        win.insert_row(4)
        win.table.setCurrentIndex(win.model.index(5, 1))
        win.auto_sum()
        assert win.model.formulas[(4, 1)] == "=SUM(B2:B5)"
        assert win.model.df.iat[4, 1] == 100

    def test_falls_back_to_left(self, win):
        win.model.setData(win.model.index(1, 2), "")
        win.table.setCurrentIndex(win.model.index(1, 2))
        win.auto_sum()
        assert win.model.formulas[(0, 2)] == "=SUM(A2:B2)"
        assert win.model.df.iat[0, 2] == 11

    def test_nothing_to_sum(self, win):
        win.table.setCurrentIndex(win.model.index(1, 0))
        win.auto_sum()
        assert not win.model.formulas


class TestSelection:
    def test_select_whole_rows_and_columns(self, win):
        select(win, 2, 1, 3, 1)
        win.table.select_whole_rows()
        assert sel_rect(win) == (2, 0, 3, 2)
        select(win, 2, 1, 2, 2)
        win.table.select_whole_columns()
        assert sel_rect(win) == (0, 1, 4, 2)
        assert win.table.whole_columns_selected()
        win.table.selectAll()
        assert not win.table.whole_columns_selected()

    def test_ctrl_home_end(self, win):
        win.table.setCurrentIndex(win.model.index(2, 1))
        QTest.keyClick(win.table, Qt.Key.Key_End, CTRL)
        assert (win.table.currentIndex().row(), win.table.currentIndex().column()) == (4, 2)
        QTest.keyClick(win.table, Qt.Key.Key_Home, CTRL)
        assert (win.table.currentIndex().row(), win.table.currentIndex().column()) == (1, 0)
        QTest.keyClick(win.table, Qt.Key.Key_End, CTRL_SHIFT)
        assert sel_rect(win) == (1, 0, 4, 2)


class TestInsertDeleteBySelection:
    def test_insert_rows_matches_selection_height(self, win):
        select(win, 2, 0, 3, 0)
        win.insert_by_selection()
        assert len(win.model.df) == 6
        assert pd.isna(win.model.df.iat[1, 0]) and pd.isna(win.model.df.iat[2, 0])
        assert win.model.df.iat[3, 0] == 2

    def test_insert_columns_when_whole_columns_selected(self, win):
        select(win, 1, 1, 1, 1)
        win.table.select_whole_columns()
        win.insert_by_selection()
        assert len(win.model.df.columns) == 4 and list(win.model.df.columns)[2] == "B"

    def test_delete_by_selection_dispatches(self, win, monkeypatch):
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
        select(win, 1, 2, 1, 2)
        win.table.select_whole_columns()
        win.delete_by_selection()
        assert list(win.model.df.columns) == ["A", "B"]
        select(win, 1, 0, 2, 0)
        win.delete_by_selection()
        assert win.model.df["A"].tolist() == [3, 4]


class TestGoto:
    def test_goto_reference(self, win, monkeypatch):
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("b3", True)))
        win.goto_cell_dialog()
        idx = win.table.currentIndex()
        assert (idx.row(), idx.column()) == (2, 1)     # Excel 第 3 行 = 视图行 2

    def test_goto_row_number_keeps_column(self, win, monkeypatch):
        win.table.setCurrentIndex(win.model.index(1, 2))
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("99", True)))
        win.goto_cell_dialog()
        idx = win.table.currentIndex()
        assert (idx.row(), idx.column()) == (4, 2)     # 越界钳到最后一行

    def test_goto_invalid(self, win, monkeypatch):
        win.table.setCurrentIndex(win.model.index(1, 1))
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("??", True)))
        win.goto_cell_dialog()
        assert win.table.currentIndex().row() == 1
        assert tr("无效的单元格引用: {}").format("??") in win.statusBar().currentMessage()


class TestDateTime:
    def test_insert_date_into_cell(self, win):
        win.table.setCurrentIndex(win.model.index(2, 2))
        win.insert_datetime(False)
        import datetime
        assert win.model.df.iat[1, 2] == datetime.date.today().isoformat()

    def test_insert_into_editor(self, win):
        win.table.edit(win.model.index(1, 2))
        editor = win.table.findChild(QLineEdit)
        win.activateWindow()
        editor.setFocus()
        if QApplication.focusWidget() is not editor:
            pytest.skip("offscreen 平台无法激活窗口焦点")
        editor.setText("at ")
        editor.setCursorPosition(3)
        win.insert_datetime(True)
        assert editor.text().startswith("at ") and ":" in editor.text()


class TestSheets:
    def test_switch_relative_wraps(self, win):
        win.add_sheet_from_df(pd.DataFrame({"k": [1]}), "S2")
        win.add_sheet_from_df(pd.DataFrame({"k": [2]}), "S3")
        assert win.sheet_names == ["Sheet1", "S2", "S3"]
        win.switch_sheet_relative(1)
        assert win.current_sheet == "S2"
        win.switch_sheet_relative(-1)
        assert win.current_sheet == "Sheet1"
        win.switch_sheet_relative(-1)
        assert win.current_sheet == "S3"


class TestEditingGuards:
    def test_shift_space_types_a_space_while_editing(self, win):
        select(win, 1, 2, 3, 2)
        win.table.edit(win.model.index(1, 2))
        editor = win.table.findChild(QLineEdit)
        editor.setText("ab")
        editor.setCursorPosition(2)
        QTest.keyClick(editor, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)
        assert editor.text() == "ab "
        assert sel_rect(win) == (1, 2, 3, 2)      # 选区没有被"选整行"改掉

    def test_fill_and_autosum_ignored_while_editing(self, win):
        select(win, 1, 1, 3, 1)
        win.table.edit(win.model.index(1, 1))
        before = win.model.df["B"].tolist()
        win.table.fill_selection(True)
        assert win.model.df["B"].tolist() == before
        win.table.select_whole_rows()
        assert sel_rect(win) == (1, 1, 3, 1)
