"""公式依赖正向索引（_formula_deps）簿记测试

注销依赖靠正向索引精确清除（性能修复：避免线性扫描 _dependents
导致大量公式时 O(N²)）。这里验证索引与反向表在公式改写/清除/撤销
后保持一致，不留脏依赖。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui.pandas_model import PandasTableModel


@pytest.fixture
def m():
    return PandasTableModel(pd.DataFrame(
        {'X': [1.0, 2.0], 'Y': [10.0, 20.0], 'Z': [0.0, 0.0]}))


class TestDependencyIndex:
    def test_replacing_formula_drops_stale_deps(self, m):
        m.setData(m.index(1, 2), '=A2*2')     # Z1 依赖 X1
        m.setData(m.index(1, 2), '=B2*2')     # 改为依赖 Y1
        assert (0, 0) not in m._dependents    # 旧依赖不残留
        m.setData(m.index(1, 0), '100')       # 改 X1 不应重算 Z1
        assert m.df.iat[0, 2] == 20.0
        m.setData(m.index(1, 1), '50')        # 改 Y1 触发重算
        assert m.df.iat[0, 2] == 100.0

    def test_clearing_formula_empties_both_maps(self, m):
        m.setData(m.index(1, 2), '=A2+B2')
        assert m._formula_deps and m._dependents
        m.setData(m.index(1, 2), '')
        assert not m._formula_deps
        assert not m._dependents              # 空依赖集合被整体移除

    def test_undo_restores_dependency_tracking(self, m):
        m.setData(m.index(1, 2), '=A2*2')
        m.setData(m.index(1, 2), '')          # 清除公式
        m.undo()                              # 恢复公式
        m.setData(m.index(1, 0), '5')
        assert m.df.iat[0, 2] == 10.0         # 依赖恢复，重算生效

    def test_range_formula_registers_bounds_not_cells(self, m):
        m.setData(m.index(1, 2), '=SUM(A2:B3)')
        # 区域只记边界（整列引用展开成几十万个键是性能热点）
        assert m._formula_ranges[(0, 2)] == [(0, 1, 0, 1)]
        assert (0, 2) not in m._formula_deps
        m.setData(m.index(2, 1), '30')        # 区域内任意格触发重算
        assert m.df.iat[0, 2] == 43.0         # 1+2+10+30
        m.setData(m.index(1, 2), '')          # 清公式后区域依赖一并注销
        assert not m._formula_ranges


class TestRangeReverseIndex:
    """区域反向索引（边界 -> 公式集合，按列分桶）与 _formula_ranges 保持一致。"""

    def test_register_and_unregister_keep_both_directions_in_sync(self, m):
        m.setData(m.index(1, 2), '=SUM(A2:B3)')
        m.setData(m.index(2, 2), '=SUM(A2:B3)+1')    # 共享同一边界
        assert m._range_formulas == {(0, 1, 0, 1): {(0, 2), (1, 2)}}
        assert m._range_cols == {0: {(0, 1, 0, 1)}, 1: {(0, 1, 0, 1)}}
        m.setData(m.index(1, 2), '')
        assert m._range_formulas == {(0, 1, 0, 1): {(1, 2)}}
        m.setData(m.index(2, 2), '7')
        assert not m._range_formulas and not m._range_cols

    def test_rebuild_after_structure_change_matches_formulas(self, m):
        m.setData(m.index(1, 2), '=SUM(A2:A3)')
        m.insert_row(0)
        assert m._formula_ranges == {(1, 2): [(1, 2, 0, 0)]}
        assert m._range_formulas == {(1, 2, 0, 0): {(1, 2)}}
        assert m._range_cols == {0: {(1, 2, 0, 0)}}
        m.undo()
        assert m._range_formulas == {(0, 1, 0, 0): {(0, 2)}}

    def test_clear_formulas_empties_range_index(self, m):
        m.setData(m.index(1, 2), '=SUM(A2:B3)')
        m.clear_formulas()
        assert not m._range_formulas and not m._range_cols

    def test_dependents_of_uses_column_bucket(self, m):
        m.setData(m.index(1, 2), '=SUM(A2:A3)')
        assert m._dependents_of((1, 0)) == {(0, 2)}
        assert m._dependents_of((1, 1)) == set()      # 相邻列不在区域内
        assert m._dependents_of((5, 0)) == set()      # 行越界
