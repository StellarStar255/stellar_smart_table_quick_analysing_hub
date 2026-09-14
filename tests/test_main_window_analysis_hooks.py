"""主窗口给 Python 分析窗口的数据接口：取 sheet、选区、结果回写（可撤销）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import QItemSelectionModel
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui.main_window import MainWindow, HEADER_ROWS


@pytest.fixture
def win():
    w = MainWindow()
    w.model.set_dataframe(pd.DataFrame({'A': [1.0, 2.0, 3.0], 'B': [4.0, 5.0, 6.0],
                                        'C': ['x', 'y', 'z']}))
    w.model.modified = False
    yield w
    w.model.modified = False
    w.close()


def select_block(win, r0, r1, c0, c1):
    sm = win.table.selectionModel()
    sm.clearSelection()
    m = win.model
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            sm.select(m.index(r + HEADER_ROWS, c), QItemSelectionModel.SelectionFlag.Select)


class TestGetSheetDf:
    def test_current_sheet_returns_copy_of_full_table(self, win):
        df = win.get_sheet_df(win.current_sheet)
        assert list(df.columns) == ['A', 'B', 'C'] and len(df) == 3
        df['A'] = 0
        assert win.model.df['A'].tolist() == [1.0, 2.0, 3.0]

    def test_other_sheet_from_cache_and_missing_raises(self, win):
        win.add_sheet_from_df(pd.DataFrame({'k': [1]}), '结果')
        assert list(win.get_sheet_df('结果').columns) == ['k']
        with pytest.raises(KeyError):
            win.get_sheet_df('不存在')


class TestSelectionFrame:
    def test_block_selection(self, win):
        select_block(win, 0, 1, 0, 1)
        sel, cols = win.selection_frame()
        assert cols == ['A', 'B']
        assert sel.shape == (2, 2) and sel['B'].tolist() == [4.0, 5.0]

    def test_header_only_selection_gives_none_but_columns(self, win):
        sm = win.table.selectionModel()
        sm.clearSelection()
        sm.select(win.model.index(0, 2), QItemSelectionModel.SelectionFlag.Select)
        sel, cols = win.selection_frame()
        assert sel is None and cols == ['C']


class TestWriteBack:
    def test_replace_is_undoable(self, win):
        win.replace_current_sheet_df(pd.DataFrame({'Z': [9]}))
        assert list(win.model.df.columns) == ['Z'] and win.model.modified
        win.undo()
        assert list(win.model.df.columns) == ['A', 'B', 'C']
        assert win.model.df['A'].tolist() == [1.0, 2.0, 3.0]

    def test_append_columns_is_undoable(self, win):
        names = win.append_columns_to_current(pd.DataFrame({'A': [7, 8, 9], 'D': [0, 0, 0]}))
        assert names == ['A_1', 'D']
        assert list(win.model.df.columns) == ['A', 'B', 'C', 'A_1', 'D']
        win.undo()
        assert list(win.model.df.columns) == ['A', 'B', 'C']

    def test_refused_while_filtered(self, win):
        win.active_filters = [{"col": "A", "op": "eq", "value": 1}]
        with pytest.raises(RuntimeError):
            win.replace_current_sheet_df(pd.DataFrame({'Z': [9]}))
        with pytest.raises(RuntimeError):
            win.append_columns_to_current(pd.DataFrame({'Z': [1, 2, 3]}))
        win.active_filters = []
