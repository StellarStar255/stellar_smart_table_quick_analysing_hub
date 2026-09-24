# -*- coding: utf-8 -*-
"""拖动字母列头移动列：数据/公式引用/背景色随列走，可撤销；筛选中同步原表。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui.main_window import MainWindow
from qtui.pandas_model import PandasTableModel


def make_model():
    return PandasTableModel(pd.DataFrame(
        {'A': [1, 2], 'B': [10, 20], 'C': ['x', 'y'], 'D': [0.5, 1.5]}))


class TestMoveOrder:
    @pytest.mark.parametrize("cols,gap,expected", [
        ([0], 2, [1, 0, 2, 3]),
        ([0], 4, [1, 2, 3, 0]),
        ([3], 0, [3, 0, 1, 2]),
        ([1, 2], 0, [1, 2, 0, 3]),
        ([1, 2], 4, [0, 3, 1, 2]),
    ])
    def test_order(self, cols, gap, expected):
        assert PandasTableModel.column_move_order(4, cols, gap) == expected

    @pytest.mark.parametrize("gap", [1, 2, 3])
    def test_drop_inside_own_block_is_noop(self, gap):
        assert PandasTableModel.column_move_order(4, [1, 2], gap) is None


class TestModelMove:
    def test_data_moves_with_dtypes(self):
        m = make_model()
        m.move_columns([0], 3)
        assert list(m.df.columns) == ['B', 'C', 'A', 'D']
        assert m.df['A'].tolist() == [1, 2] and m.df['A'].dtype == 'int64'
        assert m.modified

    def test_formula_refs_and_colors_follow(self):
        m = make_model()
        m.setData(m.index(1, 3), '=B2*2')          # D 第 1 数据行引用 B
        m.cell_colors[(0, 1)] = '#ff0000'
        m.move_columns([1], 0)                      # B 移到最前
        assert list(m.df.columns) == ['B', 'A', 'C', 'D']
        assert m.formulas == {(0, 3): '=A2*2'}
        assert m.df.iat[0, 3] == 20
        assert m.cell_colors == {(0, 0): '#ff0000'}

    def test_undo_redo(self):
        m = make_model()
        m.setData(m.index(1, 3), '=B2*2')
        m.move_columns([1, 2], 4)
        assert list(m.df.columns) == ['A', 'D', 'B', 'C']
        assert m.formulas == {(0, 1): '=C2*2'}
        assert m.undo()
        assert list(m.df.columns) == ['A', 'B', 'C', 'D']
        assert m.formulas == {(0, 3): '=B2*2'} and m.df.iat[0, 3] == 20
        assert m.redo()
        assert list(m.df.columns) == ['A', 'D', 'B', 'C']
        assert m.formulas == {(0, 1): '=C2*2'}


@pytest.fixture
def win():
    w = MainWindow()
    w.model.set_dataframe(pd.DataFrame(
        {'城市': ['北京', '上海', '北京'], '数量': [1, 2, 3], '备注': ['a', 'b', 'c']}))
    w.resize(900, 500)
    w.show()
    yield w
    w.model.modified = False
    w.close()


def _header_center(header, col):
    return QPoint(header.sectionViewportPosition(col) + header.sectionSize(col) // 2,
                  header.height() // 2)


class TestWindow:
    def test_widths_follow_and_selection_moves(self, win):
        header = win.table.horizontalHeader()
        header.resizeSection(0, 222)
        win.move_columns([0], 3)
        assert list(win.model.df.columns) == ['数量', '备注', '城市']
        assert header.sectionSize(2) == 222
        assert win.table.selectionModel().isColumnSelected(2)

    def test_filtered_move_syncs_original(self, win):
        win.active_filters = [{"col": "城市", "condition": "值在列表中", "value": ["北京"]}]
        win._reapply_filters()
        win.move_columns([2], 0)
        assert list(win.model.df.columns) == ['备注', '城市', '数量']
        assert list(win.original_df.columns) == ['备注', '城市', '数量']
        assert len(win.original_df) == 3

    def test_drag_selected_header(self, win):
        header = win.table.horizontalHeader()
        win.table.selectColumn(0)
        start = _header_center(header, 0)
        end = QPoint(header.sectionViewportPosition(2) + header.sectionSize(2) - 10,
                     start.y())
        vp = header.viewport()
        QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        QTest.mouseMove(vp, QPoint(start.x() + 20, start.y()))
        QTest.mouseMove(vp, end)
        assert header.drop_gap == 3
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
        assert list(win.model.df.columns) == ['数量', '备注', '城市']
        assert header.drop_gap is None

    def test_click_without_drag_selects_column(self, win):
        header = win.table.horizontalHeader()
        win.table.selectColumn(0)
        QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, _header_center(header, 0))
        assert list(win.model.df.columns) == ['城市', '数量', '备注']
        assert win.table.selectionModel().isColumnSelected(0)

    def test_unselected_header_drag_still_selects_range(self, win):
        header = win.table.horizontalHeader()
        win.table.clearSelection()
        vp = header.viewport()
        QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         _header_center(header, 0))
        QTest.mouseMove(vp, _header_center(header, 1))
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                           _header_center(header, 1))
        assert list(win.model.df.columns) == ['城市', '数量', '备注']
