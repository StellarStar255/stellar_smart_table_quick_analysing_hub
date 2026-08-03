"""填充柄（fill handle）测试

拖拽选区右下角小方块：公式按偏移平移相对引用填充，
值循环复制；表头行不参与；填充后可撤销。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui.main_window import MainWindow


@pytest.fixture
def win():
    w = MainWindow()
    w.model.set_dataframe(
        pd.DataFrame({'X': [1.0, 2.0, 3.0, 4.0], 'Y': [10.0, 0.0, 0.0, 0.0]}))
    w.show()
    yield w
    w.model.modified = False
    w.close()


def fill(w, source, target):
    w.table._fill_source = source
    w.table._fill_target = target
    w.table._perform_fill()


class TestFillHandle:
    def test_fill_formula_down_shifts_row_refs(self, win):
        win.model.setData(win.model.index(1, 1), '=A2*2')   # Y1 = X1*2
        fill(win, (1, 1, 1, 1), (1, 1, 4, 1))
        assert win.model.formulas == {
            (0, 1): '=A2*2', (1, 1): '=A3*2', (2, 1): '=A4*2', (3, 1): '=A5*2'}
        assert list(win.model.df['Y']) == [2.0, 4.0, 6.0, 8.0]

    def test_fill_value_down_repeats_cyclically(self, win):
        # 源区两行值 1,2 -> 循环填充 1,2,1,2
        fill(win, (1, 0, 2, 0), (1, 0, 4, 0))
        assert list(win.model.df['X']) == [1.0, 2.0, 1.0, 2.0]

    def test_fill_right_shifts_col_refs(self, win):
        win.model.setData(win.model.index(1, 0), '=B2+1')
        fill(win, (1, 0, 1, 0), (1, 0, 1, 1))
        assert win.model.formulas[(0, 1)] == '=C2+1'

    def test_header_row_excluded(self, win):
        cols_before = list(win.model.df.columns)
        fill(win, (1, 0, 1, 0), (0, 0, 1, 0))   # 目标含表头行
        assert list(win.model.df.columns) == cols_before

    def test_fill_is_undoable(self, win):
        fill(win, (1, 0, 1, 0), (1, 0, 2, 0))   # X2: 2.0 -> 1.0
        assert win.model.df.iat[1, 0] == 1.0
        win.model.undo()
        assert win.model.df.iat[1, 0] == 2.0

    def test_absolute_refs_stay_fixed(self, win):
        win.model.setData(win.model.index(1, 1), '=$A$2*A2')
        fill(win, (1, 1, 1, 1), (1, 1, 2, 1))
        assert win.model.formulas[(1, 1)] == '=$A$2*A3'

    def test_handle_rect_at_selection_corner(self, win):
        idx = win.model.index(1, 1)
        win.table.setCurrentIndex(idx)
        handle = win.table._fill_handle_rect()
        cell = win.table.visualRect(idx)
        assert handle is not None
        assert cell.adjusted(-4, -4, 4, 4).contains(handle.center())
        # 手柄中心应贴着单元格右下角
        assert abs(handle.center().x() - cell.right()) <= 3
        assert abs(handle.center().y() - cell.bottom()) <= 3

    def test_fill_step_vertical_clamps_and_previews(self, win):
        win.table._fill_source = (1, 0, 1, 0)
        win.table._fill_target = None
        pos = win.table.visualRect(win.model.index(3, 0)).center()
        win.table._fill_step(pos)
        assert win.table._fill_target == (1, 0, 3, 0)
