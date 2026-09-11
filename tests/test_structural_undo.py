# -*- coding: utf-8 -*-
"""结构操作（增删行列、排序、设为表头）可撤销/重做。

撤销必须把数据、公式表、背景色、列 dtype 都恢复到操作前；
重做则回到操作后；操作之后的单元格编辑按栈序先于结构操作被撤销。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest
from PyQt6.QtCore import QCoreApplication, Qt

from qtui.pandas_model import PandasTableModel

_app = QCoreApplication.instance() or QCoreApplication([])


def make_model():
    df = pd.DataFrame({'X': [30, 10, 20], 'Y': [1.0, 2.0, 3.0], 'N': ['a', 'b', 'c']})
    return PandasTableModel(df)


def values(m):
    return m.df.values.tolist()


class TestInsertRow:
    def test_undo_removes_inserted_row_and_restores_int_dtype(self):
        m = make_model()
        original = values(m)
        m.insert_row(1)
        assert len(m.df) == 4 and m.df['X'].dtype == float   # NaN 行把整数列升成 float
        assert m.undo()
        assert values(m) == original
        assert m.df['X'].dtype == np.int64
        assert not m.modified is False   # 仍标记为已修改（撤销不等于未改动）

    def test_redo_reinserts(self):
        m = make_model()
        m.insert_row(0)
        m.undo()
        assert m.redo()
        assert len(m.df) == 4 and pd.isna(m.df.iat[0, 0])

    def test_formulas_shift_back_on_undo(self):
        m = make_model()
        m.setData(m.index(3, 1), '=A2*2')      # 数据行 2 引用 X 第 1 数据行(30)
        assert m.df.iat[2, 1] == 60
        m.insert_row(0)
        assert m.formulas == {(3, 1): '=A3*2'}
        assert m.undo()
        assert m.formulas == {(2, 1): '=A2*2'} and m.df.iat[2, 1] == 60
        assert m.redo()
        assert m.formulas == {(3, 1): '=A3*2'} and m.df.iat[3, 1] == 60

    def test_edit_after_insert_is_undone_first(self):
        m = make_model()
        m.insert_row(0)
        m.setData(m.index(1, 2), 'new')        # 新行(数据行 0)写值
        assert m.df.iat[0, 2] == 'new'
        assert m.undo()                        # 先撤销写值
        assert len(m.df) == 4 and pd.isna(m.df.iat[0, 2])
        assert m.undo()                        # 再撤销插行
        assert len(m.df) == 3 and m.df.iat[0, 2] == 'a'


class TestRemoveRows:
    def test_undo_restores_rows_in_place(self):
        m = make_model()
        original = values(m)
        m.remove_rows([0, 2])
        assert values(m) == [[10, 2.0, 'b']]
        assert m.undo()
        assert values(m) == original
        assert m.df['X'].dtype == np.int64
        assert m.redo()
        assert values(m) == [[10, 2.0, 'b']]

    def test_undo_revives_ref_error_formula_and_colors(self):
        m = make_model()
        m.setData(m.index(3, 1), '=A2')        # 引用将被删的行
        m.set_cell_color(0, 0, '#ff0000')
        m.set_cell_color(2, 2, '#00ff00')
        m.remove_rows([0])
        assert m.formulas == {(1, 1): '=#REF!'}
        assert m.cell_colors == {(1, 2): '#00ff00'}
        assert m.undo()
        assert m.formulas == {(2, 1): '=A2'} and m.df.iat[2, 1] == 30
        assert m.cell_colors == {(0, 0): '#ff0000', (2, 2): '#00ff00'}

    def test_unsorted_duplicate_positions_are_normalised(self):
        m = make_model()
        m.remove_rows([2, 0, 2])
        assert values(m) == [[10, 2.0, 'b']]
        assert m.undo() and len(m.df) == 3


class TestColumns:
    def test_insert_column_undo_redo(self):
        m = make_model()
        m.insert_column(1, 'Z')
        assert list(m.df.columns) == ['X', 'Z', 'Y', 'N']
        assert m.undo() and list(m.df.columns) == ['X', 'Y', 'N']
        assert m.redo() and list(m.df.columns) == ['X', 'Z', 'Y', 'N']

    def test_remove_columns_undo_restores_data_formulas_colors(self):
        m = make_model()
        m.setData(m.index(1, 2), '=CONCAT(A2, "!")')
        m.set_cell_color(1, 0, '#123456')
        m.remove_columns([0, 2])
        assert list(m.df.columns) == ['Y'] and not m.formulas and not m.cell_colors
        assert m.undo()
        assert list(m.df.columns) == ['X', 'Y', 'N']
        assert m.df['X'].tolist() == [30, 10, 20]
        assert m.formulas == {(0, 2): '=CONCAT(A2, "!")'} and m.df.iat[0, 2] == '30!'
        assert m.cell_colors == {(1, 0): '#123456'}
        assert m.redo() and list(m.df.columns) == ['Y']


class TestReorder:
    def test_sort_undo_restores_order_and_frozen_formulas(self):
        m = make_model()
        m.setData(m.index(1, 1), '=SUM(A2:A3)')    # 部分区域：排序时会被冻结
        assert m.df.iat[0, 1] == 40
        frozen = m.sort(0, Qt.SortOrder.AscendingOrder)
        assert frozen == 1 and not m.formulas
        assert m.df['X'].tolist() == [10, 20, 30]
        assert m.undo()
        assert m.df['X'].tolist() == [30, 10, 20]
        assert m.formulas == {(0, 1): '=SUM(A2:A3)'} and m.df.iat[0, 1] == 40
        assert m.redo()
        assert m.df['X'].tolist() == [10, 20, 30] and not m.formulas

    def test_sort_twice_undo_twice(self):
        m = make_model()
        m.sort(0, Qt.SortOrder.AscendingOrder)
        m.sort(2, Qt.SortOrder.DescendingOrder)
        assert m.df['N'].tolist() == ['c', 'b', 'a']
        m.undo()
        assert m.df['X'].tolist() == [10, 20, 30]
        m.undo()
        assert m.df['X'].tolist() == [30, 10, 20]


class TestPromoteHeader:
    def test_undo_restores_original_table(self):
        df = pd.DataFrame({'A': ['meta', 'id', '1', '2'], 'B': ['x', 'score', '5', '6']})
        m = PandasTableModel(df)
        assert m.promote_row_to_header(1)
        assert list(m.df.columns) == ['id', 'score'] and len(m.df) == 2
        assert m.undo()
        assert list(m.df.columns) == ['A', 'B'] and m.df['A'].tolist() == ['meta', 'id', '1', '2']
        assert m.redo()
        assert list(m.df.columns) == ['id', 'score'] and m.df['score'].tolist() == [5, 6]


class TestHistory:
    def test_new_operation_clears_redo(self):
        m = make_model()
        m.insert_row(0)
        m.undo()
        assert m._redo_stack
        m.remove_columns([0])
        assert not m._redo_stack

    def test_clear_history(self):
        m = make_model()
        m.insert_row(0)
        m.undo()
        m.clear_history()
        assert not m.undo() and not m.redo()

    def test_structure_version_bumps_on_undo_and_redo(self):
        m = make_model()
        v0 = m.structure_version
        m.insert_row(0)
        m.undo()
        m.redo()
        assert m.structure_version == v0 + 3
