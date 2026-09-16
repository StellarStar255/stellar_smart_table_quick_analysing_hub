# -*- coding: utf-8 -*-
"""公式依赖图的区域反向索引：正确性 + 性能护栏。

旧实现 _dependents_of 每次扫全部 _formula_ranges、_evaluation_order 对每个
公式逐格比对区域成员，整列填充 =A{n}*2+SUM(B$2:B$3001) 后改一格 B 要 5 秒
（真正求值只占 0.5 秒）。现在按"边界 -> 公式集合"反向索引并按列分桶，
整列填充共享同一边界，查依赖与拓扑排序都与公式总数无关。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from PyQt6.QtWidgets import QApplication

from qtui.pandas_model import PandasTableModel

_app = QApplication.instance() or QApplication([])   # 须是 GUI 应用：同一进程里其他测试要建控件


def range_model(n):
    """C 列整列 =A{n}*2+SUM(B$2:B${n+1})：3000 行时旧实现改一格 B 要 5 秒。"""
    df = pd.DataFrame({"A": np.arange(n, dtype=float), "B": np.ones(n), "C": np.nan})
    formulas = {(i, 2): f"=A{i + 2}*2+SUM(B$2:B${n + 1})" for i in range(n)}
    m = PandasTableModel()
    m.set_dataframe(df, formulas=formulas)
    return m


class TestRangeIndexCorrectness:
    """与计时无关的正确性：反向索引与正向表一致，拓扑序算出正确值。"""

    def test_shared_bounds_collapse_into_one_entry(self):
        m = range_model(50)
        # 50 个公式共享同一区域边界 -> 反向索引只有一条，按列分桶只挂在 B 列
        assert list(m._range_formulas) == [(0, 49, 1, 1)]
        assert len(m._range_formulas[(0, 49, 1, 1)]) == 50
        assert set(m._range_cols) == {1}
        # 依赖查询：B 列任一格 -> 全部 50 个公式；A 列一格 -> 只有同行公式
        assert len(m._dependents_of((7, 1))) == 50
        assert m._dependents_of((7, 0)) == {(7, 2)}
        assert m._dependents_of((7, 2)) == set()

    def test_edit_in_range_recalculates_every_formula(self):
        m = range_model(20)
        assert m.df.iat[3, 2] == 3 * 2 + 20
        m.setData(m.index(6, 1), "5")          # B 第 5 数据行 1 -> 5：SUM 多 4
        assert all(m.df.iat[i, 2] == i * 2 + 24 for i in range(20))
        m.setData(m.index(5, 0), "100")        # 视图行 5 = 数据行 4：只影响 C 第 4 数据行
        assert m.df.iat[4, 2] == 200 + 24
        assert m.df.iat[3, 2] == 6 + 24

    def test_index_cleaned_when_formulas_removed(self):
        m = range_model(5)
        for i in range(5):
            m.setData(m.index(i + 1, 2), "")
        assert not m._range_formulas and not m._range_cols
        assert not m._formula_ranges and not m._formula_deps

    def test_replacing_range_formula_drops_old_bounds(self):
        m = PandasTableModel(pd.DataFrame({"X": [1.0, 2.0, 3.0], "Y": [0.0, 0.0, 0.0]}))
        m.setData(m.index(1, 1), "=SUM(A2:A3)")
        assert (0, 1, 0, 0) in m._range_formulas
        m.setData(m.index(1, 1), "=SUM(A3:A4)")
        assert (0, 1, 0, 0) not in m._range_formulas
        assert (1, 2, 0, 0) in m._range_formulas
        m.setData(m.index(2, 0), "10")        # A 第 1 数据行不再被引用...
        assert m.df.iat[0, 1] == 13.0         # ...但 A3 在新区域内，触发重算 10+3
        m.setData(m.index(1, 0), "99")        # A2 已不在区域内：不重算
        assert m.df.iat[0, 1] == 13.0

    def test_multi_column_range_registered_in_every_column_bucket(self):
        m = PandasTableModel(pd.DataFrame({"X": [1.0, 2.0], "Y": [3.0, 4.0], "Z": [0.0, 0.0]}))
        m.setData(m.index(1, 2), "=SUM(A2:B3)")
        assert set(m._range_cols) == {0, 1}
        m.setData(m.index(2, 1), "40")
        assert m.df.iat[0, 2] == 46.0
        m.undo()
        assert m.df.iat[0, 2] == 10.0

    def test_diamond_through_range_evaluates_in_dependency_order(self):
        # B = A*2（单格）、C = SUM(B2:B4)（区域含公式格）、D = C + B3（混合）
        df = pd.DataFrame({"A": [1.0, 2.0, 3.0], "B": 0.0, "C": 0.0, "D": 0.0})
        m = PandasTableModel(df)
        for i in range(3):
            m.setData(m.index(i + 1, 1), f"=A{i + 2}*2")
        m.setData(m.index(1, 2), "=SUM(B2:B4)")
        m.setData(m.index(1, 3), "=C2+B3")
        assert m.df.iat[0, 2] == 12.0 and m.df.iat[0, 3] == 16.0
        m.setData(m.index(2, 0), "10")        # A 第 2 行 2 -> 10：B3=20, C2=28, D2=48
        assert m.df.iat[1, 1] == 20.0
        assert m.df.iat[0, 2] == 28.0
        assert m.df.iat[0, 3] == 48.0
        # 拓扑序：C2 入度包含区域内 3 个公式格 + D2 依赖 C2 与 B3（去重后 2 条边）
        order, cyclic = m._evaluation_order(m.formulas)
        assert not cyclic
        pos = {cell: i for i, cell in enumerate(order)}
        assert all(pos[(i, 1)] < pos[(0, 2)] for i in range(3))
        assert pos[(0, 2)] < pos[(0, 3)] and pos[(1, 1)] < pos[(0, 3)]

    def test_range_self_reference_is_circular(self):
        m = PandasTableModel(pd.DataFrame({"A": [1.0, 2.0, 3.0]}))
        m.setData(m.index(1, 0), "=SUM(A2:A4)")
        assert m.df.iat[0, 0] == "#CIRC!"
        m.setData(m.index(2, 0), "5")
        assert m.df.iat[0, 0] == "#CIRC!"

    def test_evaluation_order_counts_range_edge_once_with_single_ref(self):
        # 同一依赖既是单格引用又在区域内：只算一条边，否则入度永远减不到 0
        m = PandasTableModel(pd.DataFrame({"A": [1.0, 2.0], "B": 0.0}))
        m.setData(m.index(1, 1), "=A2*2")
        m.setData(m.index(2, 1), "=B2+SUM(B2:B2)")
        assert m.df.iat[1, 1] == 4.0
        m.setData(m.index(1, 0), "5")
        assert m.df.iat[1, 1] == 20.0


class TestPerformanceGuard:
    """性能护栏：整列 2000 个区域公式，改一格 / 增删一行都应远低于 1 秒。"""

    N = 2000

    def test_edit_one_range_cell_is_fast(self):
        m = range_model(self.N)
        t = time.perf_counter()
        m.setData(m.index(6, 1), "5")
        elapsed = time.perf_counter() - t
        assert m.df.iat[0, 2] == 0 + self.N + 4
        assert elapsed < 1.0, f"edit one B cell took {elapsed:.2f}s"

    def test_insert_and_remove_row_are_fast(self):
        m = range_model(self.N)
        t = time.perf_counter()
        m.insert_row(self.N // 2)
        m.remove_rows([self.N // 2])
        elapsed = time.perf_counter() - t
        assert m.formulas[(0, 2)] == f"=A2*2+SUM(B$2:B${self.N + 1})"
        assert m.df.iat[0, 2] == self.N
        assert elapsed < 2.0, f"insert+remove row took {elapsed:.2f}s"

    def test_clear_column_is_fast(self):
        m = range_model(self.N)
        t = time.perf_counter()
        cleared = m.clear_cells([(r, 1) for r in range(self.N)])
        elapsed = time.perf_counter() - t
        assert cleared == self.N
        assert m.df.iat[3, 2] == 6.0                 # SUM 空列 = 0
        assert elapsed < 1.0, f"clear column took {elapsed:.2f}s"


class TestStructuralRecalcOnlyChanged:
    """增删行列只重算文本被改写的公式及其依赖闭包，结果与整表重算一致。"""

    def test_insert_row_values_match_full_recalc(self):
        m = range_model(30)
        m.setData(m.index(2, 0), "7")                # A 第 1 数据行
        m.insert_row(0)
        expected = {k: m._evaluate(f) for k, f in m.formulas.items()}
        assert all(m.df.iat[r, c] == v for (r, c), v in expected.items())
        assert m.formulas[(1, 2)] == "=A3*2+SUM(B$3:B$32)"

    def test_unaffected_formula_keeps_value_and_dependent_chain_updates(self):
        df = pd.DataFrame({"A": [1.0, 2.0, 3.0, 4.0], "B": 0.0, "C": 0.0})
        m = PandasTableModel(df)
        m.setData(m.index(1, 1), "=A2*10")           # B2 引用首行：删第 3 行文本不变
        m.setData(m.index(1, 2), "=B2+A4")           # C2 引用被删行 -> #REF!
        m.setData(m.index(4, 1), "=A5")              # B5 行号前移 -> 文本改写
        m.remove_rows([2])
        assert m.formulas[(0, 1)] == "=A2*10" and m.df.iat[0, 1] == 10.0
        assert m.formulas[(0, 2)] == "=B2+#REF!" and m.df.iat[0, 2] == "#REF!"
        assert m.formulas[(2, 1)] == "=A4" and m.df.iat[2, 1] == 4.0

    def test_delete_column_referenced_by_range_shrinks_and_recalcs(self):
        df = pd.DataFrame({"A": [1.0], "B": [2.0], "C": [3.0], "D": [0.0]})
        m = PandasTableModel(df)
        m.setData(m.index(1, 3), "=SUM(A2:C2)")
        assert m.df.iat[0, 3] == 6.0
        m.remove_columns([1])
        assert m.formulas[(0, 2)] == "=SUM(A2:B2)"
        assert m.df.iat[0, 2] == 4.0
        m.undo()
        assert m.formulas[(0, 3)] == "=SUM(A2:C2)" and m.df.iat[0, 3] == 6.0

    def test_unsupported_function_keeps_cached_value_after_insert(self):
        # 与旧 evaluate_all_formulas 口径一致：#NAME? 不覆盖现有值
        m = PandasTableModel(pd.DataFrame({"A": [1.0, 2.0], "B": [0.0, 0.0]}))
        m.set_dataframe(m.df, formulas={(1, 1): "=NOSUCHFN(A3)"})
        m._set_cell(1, 1, 42.0)
        m.insert_row(0)
        assert m.formulas[(2, 1)] == "=NOSUCHFN(A4)"
        assert m.df.iat[2, 1] == 42.0
