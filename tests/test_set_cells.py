# -*- coding: utf-8 -*-
"""set_cells 批量写入 / append_rows 追加空行。

set_cells 语义等同逐格 setData(EditRole)，但整批一条撤销记录、一次
dataChanged、一次依赖重建与重算；append_rows 是可撤销的结构操作，
让粘贴溢出不再靠 set_dataframe（会清空撤销栈）补行。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from qtui.pandas_model import PandasTableModel

_app = QApplication.instance() or QApplication([])   # 须是 GUI 应用：同一进程里其他测试要建控件
H = PandasTableModel.HEADER_ROWS


def make_model():
    return PandasTableModel(pd.DataFrame(
        {"X": [1, 2, 3], "Y": [10.0, 20.0, 30.0], "N": ["a", "b", "c"]}))


def display_changes(m):
    """记录 DisplayRole/EditRole 的 dataChanged 发射（矩形坐标）。"""
    seen = []

    def on_changed(tl, br, roles=None):
        if roles and Qt.ItemDataRole.DisplayRole not in roles \
                and Qt.ItemDataRole.EditRole not in roles:
            return
        seen.append((tl.row(), tl.column(), br.row(), br.column()))
    m.dataChanged.connect(on_changed)
    return seen


def reference_via_setdata(entries):
    """同一批数据逐格 setData 得到的参照结果。"""
    ref = make_model()
    for r, c, v in entries:
        ref.setData(ref.index(r, c), v)
    return ref


class TestSetCellsSemantics:
    def test_values_and_coercion_match_setdata(self):
        entries = [(1, 0, "7"), (2, 0, "8.5"), (3, 1, ""), (1, 2, "z"), (2, 1, "abc")]
        m = make_model()
        assert m.set_cells(entries)
        ref = reference_via_setdata(entries)
        pd.testing.assert_frame_equal(m.df, ref.df)   # 值与 dtype 都一致（NaN 视为相等）
        assert m.df.iat[0, 0] == 7 and m.df.iat[1, 0] == 8.5
        assert pd.isna(m.df.iat[2, 1])
        assert m.modified

    def test_formulas_evaluated_in_dependency_order(self):
        m = make_model()
        # C 依赖 B、B 依赖 A：按条目顺序逆着给，也必须算对
        assert m.set_cells([(1, 2, "=B2*2"), (1, 1, "=A2+1"), (1, 0, "5")])
        assert m.df.iat[0, 0] == 5 and m.df.iat[0, 1] == 6.0 and m.df.iat[0, 2] == 12.0
        assert m.formulas == {(0, 2): "=B2*2", (0, 1): "=A2+1"}
        assert (0, 0) in m._dependents and (0, 1) in m._dependents
        # 之后改 A2 依旧联动
        m.setData(m.index(1, 0), "10")
        assert m.df.iat[0, 2] == 22.0

    def test_existing_dependents_outside_batch_recalc(self):
        m = make_model()
        m.setData(m.index(3, 1), "=A2+A3")          # Y3 依赖 X1、X2
        assert m.set_cells([(1, 0, "100"), (2, 0, "200")])
        assert m.df.iat[2, 1] == 300.0

    def test_overwriting_formula_with_value_drops_formula(self):
        m = make_model()
        m.setData(m.index(1, 1), "=A2*2")
        assert m.set_cells([(1, 1, "99")])
        assert (0, 1) not in m.formulas and not m._formula_deps
        assert m.df.iat[0, 1] == 99.0
        m.setData(m.index(1, 0), "3")                # 不再联动
        assert m.df.iat[0, 1] == 99.0

    def test_header_row_entries_rename_columns(self):
        m = make_model()
        renamed = []
        m.columnRenamed.connect(lambda c, o, n: renamed.append((c, o, n)))
        msgs = []
        m.renameFailed.connect(msgs.append)
        # 空名跳过、与现有列重名拒绝；"X" 在本批前面已改名，此时不算重名（与逐格 setData 一致）
        assert m.set_cells([(0, 0, "销售额"), (0, 1, "  "), (0, 2, "Y"), (0, 1, "X")])
        assert list(m.df.columns) == ["销售额", "X", "N"]
        assert renamed == [(0, "X", "销售额"), (1, "Y", "X")]
        assert len(msgs) == 1

    def test_rename_rejected_emits_failure_like_setdata(self):
        m = make_model()
        msgs = []
        m.renameFailed.connect(msgs.append)
        assert not m.set_cells([(0, 0, "Y")])
        assert len(msgs) == 1 and list(m.df.columns) == ["X", "Y", "N"]

    def test_no_change_returns_false_and_records_nothing(self):
        m = make_model()
        assert not m.set_cells([(1, 0, "1"), (2, 1, "20"), (1, 2, "a"), (0, 0, "X")])
        assert not m._undo_stack and not m.modified

    def test_out_of_range_entries_ignored(self):
        m = make_model()
        assert m.set_cells([(1, 0, "5"), (9, 0, "x"), (1, 7, "x"), (-1, 0, "x")])
        assert m.df.iat[0, 0] == 5 and m.df.shape == (3, 3)

    def test_same_cell_twice_last_write_wins(self):
        m = make_model()
        assert m.set_cells([(1, 0, "5"), (1, 0, "=B2")])
        assert m.formulas == {(0, 0): "=B2"} and m.df.iat[0, 0] == 10.0
        m.undo()
        assert m.df.iat[0, 0] == 1 and not m.formulas

    def test_empty_iterable(self):
        m = make_model()
        assert not m.set_cells([])
        assert not m.set_cells(iter(()))


class TestSetCellsBatching:
    def test_single_display_signal_bounding_rect(self):
        m = make_model()
        seen = display_changes(m)
        m.set_cells([(1, 0, "5"), (3, 2, "q"), (2, 1, "=A2*2")])
        assert seen == [(1, 0, 3, 2)]

    def test_signal_rect_covers_recalculated_dependents(self):
        m = make_model()
        m.setData(m.index(3, 2), "=A2*2")
        seen = display_changes(m)
        m.set_cells([(1, 0, "5")])
        assert seen == [(1, 0, 3, 2)]           # 含被重算的 N3

    def test_single_undo_restores_everything_and_redo_reapplies(self):
        m = make_model()
        before = m.df.values.tolist()
        dtypes = list(m.df.dtypes)
        m.set_cells([(1, 0, "7"), (2, 0, "x"), (3, 1, "=A2*2"), (0, 2, "备注")])
        assert len(m._undo_stack) == 1
        after = m.df.values.tolist()
        assert m.df.iat[2, 1] == 14.0 and list(m.df.columns) == ["X", "Y", "备注"]
        assert m.undo()
        assert m.df.values.tolist() == before
        assert list(m.df.dtypes) == dtypes         # 文本写入 int 列退化 object，撤销复原
        assert not m.formulas and not m._formula_deps
        assert list(m.df.columns) == ["X", "Y", "N"]
        assert m.redo()
        assert m.df.values.tolist() == after
        assert m.formulas == {(2, 1): "=A2*2"}
        assert list(m.df.columns) == ["X", "Y", "备注"]

    def test_undo_emits_once_and_recalcs_dependents(self):
        m = make_model()
        m.setData(m.index(3, 2), "=A2+A3")
        m.set_cells([(1, 0, "100"), (2, 0, "200")])
        assert m.df.iat[2, 2] == 300.0
        seen = display_changes(m)
        m.undo()
        assert m.df.iat[2, 2] == 3.0
        assert seen == [(1, 0, 3, 2)]

    def test_batch_restoring_formula_reregisters_dependency(self):
        m = make_model()
        m.setData(m.index(1, 1), "=A2*2")
        m.set_cells([(1, 1, "0")])                  # 覆盖公式
        m.undo()                                     # 公式复活
        m.setData(m.index(1, 0), "4")
        assert m.df.iat[0, 1] == 8.0

    def test_rename_swap_via_temp_undoes_in_reverse_order(self):
        m = make_model()
        m.set_cells([(0, 0, "__tmp"), (0, 1, "X"), (0, 2, "Y"), (0, 0, "N")])
        assert list(m.df.columns) == ["N", "X", "Y"]
        m.undo()
        assert list(m.df.columns) == ["X", "Y", "N"]
        m.redo()
        assert list(m.df.columns) == ["N", "X", "Y"]


class TestAppendRows:
    def test_appends_empty_rows_and_is_undoable(self):
        m = make_model()
        assert m.append_rows(2) == 3
        assert len(m.df) == 5 and pd.isna(m.df.iat[4, 1]) and pd.isna(m.df.iat[3, 0])
        assert m.df["X"].dtype == float             # NaN 把整数列升成 float
        assert m.undo()
        assert len(m.df) == 3 and m.df["X"].dtype == np.int64
        assert m.redo()
        assert len(m.df) == 5

    def test_keeps_formulas_colors_and_undo_history(self):
        m = make_model()
        m.setData(m.index(1, 1), "=A2*2")
        m.apply_cell_colors([(2, 2)], "#ff0000")
        depth = len(m._undo_stack)
        m.append_rows(3)
        assert m.formulas == {(0, 1): "=A2*2"} and m.cell_colors == {(2, 2): "#ff0000"}
        assert len(m._undo_stack) == depth + 1
        assert m.structure_version == 1
        m.setData(m.index(1, 0), "5")
        assert m.df.iat[0, 1] == 10.0

    def test_emits_rows_inserted_signal(self):
        m = make_model()
        seen = []
        m.rowsInserted.connect(lambda parent, first, last: seen.append((first, last)))
        m.append_rows(2)
        assert seen == [(3 + H, 4 + H)]

    def test_zero_or_negative_is_noop(self):
        m = make_model()
        assert m.append_rows(0) == 3 and m.append_rows(-1) == 3
        assert len(m.df) == 3 and not m._undo_stack

    def test_clipped_range_extends_to_new_rows(self):
        m = make_model()
        m.setData(m.index(1, 2), "=SUM(B2:B10)")      # 只有 3 行，区域被裁剪
        assert m.df.iat[0, 2] == 60.0
        m.append_rows(2)
        m.setData(m.index(5, 1), "40")               # 新行落在区域内 -> 重算
        assert m.df.iat[0, 2] == 100.0
        m.undo()
        m.undo()
        assert len(m.df) == 3 and m.df.iat[0, 2] == 60.0

    def test_paste_overflow_pattern_set_cells_into_new_rows(self):
        m = make_model()
        m.setData(m.index(1, 0), "9")                # 先有一条编辑历史
        m.append_rows(2)
        m.set_cells([(4, 0, "4"), (5, 0, "5"), (5, 1, "=A6*2")])
        assert m.df.iat[4, 1] == 10.0 and len(m.df) == 5
        m.undo()                                     # 撤销粘贴
        assert len(m.df) == 5 and pd.isna(m.df.iat[4, 0])
        m.undo()                                     # 撤销追加行
        assert len(m.df) == 3
        m.undo()                                     # 最早的编辑仍在栈里
        assert m.df.iat[0, 0] == 1
