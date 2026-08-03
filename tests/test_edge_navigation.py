"""Cmd/Ctrl+方向键边缘跳转测试

Cmd+↓/↑/←/→ 跳到表格边缘；加 Shift 把选区扩展到边缘。
macOS 上 Cmd 映射为 Qt 的 ControlModifier，测试用 ControlModifier 等价。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest

_app = QApplication.instance() or QApplication([])

from qtui.main_window import MainWindow

CTRL = Qt.KeyboardModifier.ControlModifier
CTRL_SHIFT = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier


@pytest.fixture
def win():
    w = MainWindow()
    w.model.set_dataframe(pd.DataFrame(
        {c: [f'{c}{i}' for i in range(5)] for c in 'WXYZ'}))
    w.show()
    yield w
    w.model.modified = False
    w.close()


def rows_cols(win):
    idx = win.table.selectionModel().selectedIndexes()
    return ({i.row() for i in idx}, {i.column() for i in idx})


class TestEdgeJump:
    def test_ctrl_down_jumps_to_last_row(self, win):
        win.table.setCurrentIndex(win.model.index(2, 1))
        QTest.keyClick(win.table, Qt.Key.Key_Down, CTRL)
        assert win.table.currentIndex().row() == 5     # 5 数据行 + 表头 = 视图行 5
        assert win.table.currentIndex().column() == 1

    def test_ctrl_up_jumps_to_first_data_row(self, win):
        win.table.setCurrentIndex(win.model.index(4, 2))
        QTest.keyClick(win.table, Qt.Key.Key_Up, CTRL)
        assert win.table.currentIndex().row() == 1

    def test_ctrl_left_right_jump_to_edge_columns(self, win):
        win.table.setCurrentIndex(win.model.index(2, 2))
        QTest.keyClick(win.table, Qt.Key.Key_Left, CTRL)
        assert win.table.currentIndex().column() == 0
        QTest.keyClick(win.table, Qt.Key.Key_Right, CTRL)
        assert win.table.currentIndex().column() == 3


class TestEdgeExtendSelection:
    def test_ctrl_shift_down_selects_to_last_row(self, win):
        win.table.setCurrentIndex(win.model.index(2, 1))
        QTest.keyClick(win.table, Qt.Key.Key_Down, CTRL_SHIFT)
        rows, cols = rows_cols(win)
        assert rows == {2, 3, 4, 5} and cols == {1}
        assert win.table.currentIndex().row() == 5

    def test_ctrl_shift_up_selects_to_first_data_row(self, win):
        win.table.setCurrentIndex(win.model.index(3, 0))
        QTest.keyClick(win.table, Qt.Key.Key_Up, CTRL_SHIFT)
        rows, cols = rows_cols(win)
        assert rows == {1, 2, 3} and cols == {0}

    def test_ctrl_shift_right_selects_to_last_column(self, win):
        win.table.setCurrentIndex(win.model.index(2, 1))
        QTest.keyClick(win.table, Qt.Key.Key_Right, CTRL_SHIFT)
        rows, cols = rows_cols(win)
        assert rows == {2} and cols == {1, 2, 3}

    def test_selected_range_can_be_cleared_with_delete(self, win):
        # 组合场景：Cmd+Shift+↓ 选到底后 Delete 清空
        win.table.setCurrentIndex(win.model.index(3, 3))
        QTest.keyClick(win.table, Qt.Key.Key_Down, CTRL_SHIFT)
        QTest.keyClick(win.table, Qt.Key.Key_Delete)
        assert [str(v) for v in win.model.df['Z'].fillna('')] == \
            ['Z0', 'Z1', '', '', '']
