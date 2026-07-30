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
