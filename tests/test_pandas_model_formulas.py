"""表格模型公式联动测试：编辑重算、排序后公式跟随

运行: python3 -m pytest tests/ -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import QCoreApplication, Qt

from qtui.pandas_model import PandasTableModel

# QAbstractTableModel 需要应用实例存在（无需事件循环）
_app = QCoreApplication.instance() or QCoreApplication([])


def make_model():
    df = pd.DataFrame({'X': [30.0, 10.0, 20.0], 'Y': [1.0, 2.0, 3.0]})
    return PandasTableModel(df)


class TestFormulaEditing:
    def test_formula_stores_text_and_result(self):
        m = make_model()
        assert m.setData(m.index(0, 1), '=A1*2')
        assert m.formulas[(0, 1)] == '=A1*2'
        assert m._df.iat[0, 1] == 60

    def test_dependent_recalc_on_edit(self):
        m = make_model()
        m.setData(m.index(0, 1), '=A1+A2')
        assert m._df.iat[0, 1] == 40
        m.setData(m.index(1, 0), '5')  # A2: 10 -> 5
        assert m._df.iat[0, 1] == 35

    def test_overwrite_formula_with_value(self):
        m = make_model()
        m.setData(m.index(0, 1), '=A1*2')
        m.setData(m.index(0, 1), '7')
        assert (0, 1) not in m.formulas
        assert m._df.iat[0, 1] == 7


class TestSortFollowsFormulas:
    def test_formula_cell_moves_with_its_row(self):
        m = make_model()
        m.setData(m.index(0, 1), '=A1*2')  # 行 X=30，结果 60
        m.sort(0, Qt.SortOrder.AscendingOrder)  # X: 10, 20, 30
        # X=30 的行排到第 3 行，公式跟着走且引用重写
        assert m.formulas == {(2, 1): '=A3*2'}
        assert m._df.iat[2, 1] == 60

    def test_recalc_after_sort_keeps_result(self):
        m = make_model()
        m.setData(m.index(1, 1), '=A2+100')  # 行 X=10，结果 110
        m.sort(0, Qt.SortOrder.AscendingOrder)  # X=10 的行排到第 1 行
        assert m.formulas == {(0, 1): '=A1+100'}
        assert m._df.iat[0, 1] == 110

    def test_dependency_tracking_survives_sort(self):
        m = make_model()
        m.setData(m.index(0, 1), '=A1*2')
        m.sort(0, Qt.SortOrder.AscendingOrder)
        # 排序后编辑被引用的单元格，公式应重算
        m.setData(m.index(2, 0), '50')  # 原 X=30 行现在在第 3 行
        assert m._df.iat[2, 1] == 100

    def test_range_formula_recalculated_after_sort(self):
        m = make_model()
        m.setData(m.index(0, 1), '=SUM(A1:A2)')  # 30+10 = 40
        m.sort(0, Qt.SortOrder.AscendingOrder)  # X: 10, 20, 30
        # 区域引用不重写，按新行序重算：10+20 = 30
        assert m.formulas == {(2, 1): '=SUM(A1:A2)'}
        assert m._df.iat[2, 1] == 30

    def test_sort_without_formulas(self):
        m = make_model()
        m.sort(0, Qt.SortOrder.AscendingOrder)
        assert list(m._df['X']) == [10.0, 20.0, 30.0]


class TestReorderRows:
    def test_reorder_moves_formulas_colors_and_data(self):
        m = make_model()
        m.setData(m.index(0, 1), '=A1*2')  # 60
        m.cell_colors[(0, 0)] = '#ff0000'
        m.reorder_rows([2, 0, 1])  # 旧行 2/0/1 -> 新行 0/1/2
        assert list(m._df['X']) == [20.0, 30.0, 10.0]
        assert m.formulas == {(1, 1): '=A2*2'}
        assert m.cell_colors == {(1, 0): '#ff0000'}
        assert m._df.iat[1, 1] == 60

    def test_reorder_clears_undo_and_marks_modified(self):
        m = make_model()
        m.setData(m.index(0, 0), '99')
        m.reorder_rows([1, 2, 0])
        assert m.modified
        assert not m._undo_stack


class TestInsertDeleteFollowsFormulas:
    def test_insert_row_shifts_formula_and_refs(self):
        m = make_model()
        m.setData(m.index(0, 1), '=A2')  # 引用 X=10
        m.insert_row(0)
        # 公式随行下移，引用也指向下移后的数据
        assert m.formulas == {(1, 1): '=A3'}
        assert m._df.iat[1, 1] == 10

    def test_insert_row_below_does_not_touch_refs(self):
        m = make_model()
        m.setData(m.index(0, 1), '=A1*2')
        m.insert_row(2)
        assert m.formulas == {(0, 1): '=A1*2'}
        assert m._df.iat[0, 1] == 60

    def test_insert_row_grows_range(self):
        m = make_model()
        m.setData(m.index(0, 1), '=SUM(A1:A3)')  # 60
        m.insert_row(1)
        assert m.formulas == {(0, 1): '=SUM(A1:A4)'}
        assert m._df.iat[0, 1] == 60  # 插入的空行按 0 计

    def test_insert_column_shifts_refs(self):
        m = make_model()
        m.setData(m.index(0, 1), '=A1*2')
        m.insert_column(0)
        assert m.formulas == {(0, 2): '=B1*2'}
        assert m._df.iat[0, 2] == 60

    def test_delete_referenced_row_becomes_ref_error(self):
        m = make_model()
        m.setData(m.index(0, 1), '=A2')
        m.remove_rows([1])
        assert m.formulas == {(0, 1): '=#REF!'}
        assert m._df.iat[0, 1] == '#REF!'

    def test_delete_row_inside_range_shrinks_and_recalcs(self):
        m = make_model()
        m.setData(m.index(0, 1), '=SUM(A1:A3)')  # 60
        m.remove_rows([1])  # 删掉 X=10
        assert m.formulas == {(0, 1): '=SUM(A1:A2)'}
        assert m._df.iat[0, 1] == 50

    def test_delete_referenced_column_becomes_ref_error(self):
        m = make_model()
        m.setData(m.index(0, 1), '=A1*2')
        m.remove_columns([0])
        assert m.formulas == {(0, 0): '=#REF!*2'}
        assert m._df.iat[0, 0] == '#REF!'

    def test_delete_formula_cell_row_drops_formula(self):
        m = make_model()
        m.setData(m.index(1, 1), '=A1')
        m.remove_rows([1])
        assert m.formulas == {}
