"""Delete/Backspace 清空选中单元格测试

多选后按 Delete 或 Backspace 清空内容（公式一并清除）；
表头行不受影响；逐格可撤销。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import Qt, QItemSelection, QItemSelectionModel
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest

_app = QApplication.instance() or QApplication([])

from qtui.main_window import MainWindow


@pytest.fixture
def win():
    w = MainWindow()
    w.model.set_dataframe(
        pd.DataFrame({'X': ['a', 'b', 'c'], 'Y': ['1', '2', '3']}))
    w.show()
    yield w
    w.model.modified = False
    w.close()


def select_range(w, top, left, bottom, right):
    sm = w.table.selectionModel()
    sm.select(QItemSelection(w.model.index(top, left),
                             w.model.index(bottom, right)),
              QItemSelectionModel.SelectionFlag.ClearAndSelect)


class TestClearSelection:
    def test_delete_clears_selected_cells(self, win):
        select_range(win, 1, 0, 3, 0)   # X 列全部数据行
        QTest.keyClick(win.table, Qt.Key.Key_Delete)
        assert [str(v) for v in win.model.df['X'].fillna('')] == ['', '', '']
        assert list(win.model.df['Y']) == ['1', '2', '3']

    def test_backspace_clears_too(self, win):
        select_range(win, 1, 1, 2, 1)
        QTest.keyClick(win.table, Qt.Key.Key_Backspace)
        assert [str(v) for v in win.model.df['Y'].fillna('')] == ['', '', '3']

    def test_header_row_not_cleared(self, win):
        select_range(win, 0, 0, 1, 1)   # 含表头行
        QTest.keyClick(win.table, Qt.Key.Key_Delete)
        assert list(win.model.df.columns) == ['X', 'Y']

    def test_formula_cell_cleared(self, win):
        win.model.setData(win.model.index(1, 1), '=A2')
        select_range(win, 1, 1, 1, 1)
        QTest.keyClick(win.table, Qt.Key.Key_Delete)
        assert win.model.formulas == {}

    def test_clear_is_undoable(self, win):
        select_range(win, 1, 0, 1, 0)
        QTest.keyClick(win.table, Qt.Key.Key_Delete)
        win.model.undo()
        assert win.model.df.iat[0, 0] == 'a'
