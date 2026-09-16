# -*- coding: utf-8 -*-
"""多列排序：模型多键稳定排序、排序对话框收集条件、主窗口接线与撤销。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui.i18n import tr
from qtui.main_window import MainWindow
from qtui.pandas_model import PandasTableModel
from qtui.sort_dialog import SortDialog


def make_df():
    return pd.DataFrame({
        "城市": ["上海", "北京", "上海", "北京", "广州"],
        "数量": [3, 1, 1, 2, None],
        "备注": ["b", "a", "c", "d", "e"],
    })


class TestModelSortPositions:
    def test_two_keys(self):
        m = PandasTableModel(make_df())
        pos = m.sort_positions([(0, True), (1, False)])
        # 城市升序（上海 < 北京 < 广州 按字符串），同城市内数量降序
        assert [m.df.iat[p, 2] for p in pos] == ["b", "c", "d", "a", "e"]

    def test_stable_for_equal_keys(self):
        m = PandasTableModel(pd.DataFrame({"k": [1, 1, 1], "v": ["x", "y", "z"]}))
        assert m.sort_positions([(0, False)]) == [0, 1, 2]

    def test_nulls_last_regardless_of_direction(self):
        m = PandasTableModel(make_df())
        assert m.sort_positions([(1, True)])[-1] == 4
        assert m.sort_positions([(1, False)])[-1] == 4

    def test_ignores_out_of_range_and_duplicate_columns(self):
        m = PandasTableModel(make_df())
        assert m.sort_positions([(9, True)]) is None
        assert m.sort_positions([(1, True), (1, False), (7, True)]) == \
            m.sort_positions([(1, True)])

    def test_mixed_text_and_numbers_numbers_first(self):
        m = PandasTableModel(pd.DataFrame({"a": ["x", 5, "#DIV/0!", 2], "b": [1, 1, 1, 1]}))
        pos = m.sort_positions([(0, True), (1, True)])
        assert [m.df.iat[p, 0] for p in pos] == [2, 5, "#DIV/0!", "x"]

    def test_sort_by_keys_is_undoable(self):
        m = PandasTableModel(make_df())
        m.sort_by_keys([(0, True), (2, False)])
        assert m.df["备注"].tolist() == ["c", "b", "d", "a", "e"]
        assert m.undo()
        assert m.df["备注"].tolist() == ["b", "a", "c", "d", "e"]


class TestSortDialog:
    def test_starts_with_preset_column(self):
        d = SortDialog(["a", "b", "c"], preset_col=2)
        assert d.keys() == [(2, True)]

    def test_add_and_remove_keys(self):
        d = SortDialog(["a", "b", "c"])
        d.add_key(1, False)
        d.add_key()                       # 自动挑一个未用的列
        assert d.keys() == [(0, True), (1, False), (2, True)]
        assert not d._add_btn.isEnabled()  # 条件数已达列数
        d._remove(d._rows[1][0])
        assert d.keys() == [(0, True), (2, True)]
        assert d._rows[0][0][0].text() == tr("排序依据")
        assert d._rows[1][0][0].text() == tr("然后按")

    def test_cannot_remove_last_key(self):
        d = SortDialog(["a", "b"])
        d._remove(d._rows[0][0])
        assert d.keys() == [(0, True)]

    def test_duplicate_column_keeps_highest_priority(self):
        d = SortDialog(["a", "b"])
        d.add_key(0, False)
        assert d.keys() == [(0, True)]


@pytest.fixture
def win():
    w = MainWindow()
    w.model.set_dataframe(make_df())
    yield w
    w.model.modified = False
    w.close()


class TestMainWindow:
    def test_sort_by_keys_reorders_and_reports(self, win):
        win._sort_by_keys([(0, True), (1, False)])
        assert win.model.df["备注"].tolist() == ["b", "c", "d", "a", "e"]
        msg = win.statusBar().currentMessage()
        assert tr("已按 {} 排序").format(tr("，").join([
            tr("{}（{}）").format("城市", tr("升序")),
            tr("{}（{}）").format("数量", tr("降序"))])) in msg

    def test_single_column_path_still_works(self, win):
        win._sort_by(2, False)
        assert win.model.df["备注"].tolist() == ["e", "d", "c", "b", "a"]

    def test_sort_then_undo_through_window(self, win):
        win._sort_by_keys([(2, False)])
        win.undo()
        assert win.model.df["备注"].tolist() == ["b", "a", "c", "d", "e"]
        assert tr("已撤销") in win.statusBar().currentMessage()
        win.redo()
        assert win.model.df["备注"].tolist() == ["e", "d", "c", "b", "a"]

    def test_row_and_column_ops_undo_through_window(self, win):
        win.insert_row(0)
        win._insert_col_at(1, "新")
        assert len(win.model.df) == 6 and "新" in win.model.df.columns
        win.undo()
        assert "新" not in win.model.df.columns
        win.undo()
        assert len(win.model.df) == 5

    def test_structural_ops_while_filtered_clear_history(self, win):
        win.active_filters = [{"col": "城市", "condition": "值在列表中", "value": ["北京"]}]
        win._reapply_filters()
        win.model.setData(win.model.index(1, 2), "改过")
        assert win.model._undo_stack
        win.insert_row(0)
        assert not win.model._undo_stack and not win.model._redo_stack
        win._sort_by_keys([(1, True)])
        assert not win.model._undo_stack


class TestDatetimeSortKey:
    """日期列 NaT 必须排最后：to_numeric 会把 NaT 变成 int64 最小值排到最前。"""

    def test_nat_last_regardless_of_direction(self):
        m = PandasTableModel(pd.DataFrame({
            "d": pd.to_datetime(["2024-03-01", None, "2023-01-01", "2025-06-30"]),
            "v": ["b", "nat", "a", "c"],
        }))
        asc = m.sort_positions([(0, True)])
        desc = m.sort_positions([(0, False)])
        assert [m.df.iat[p, 1] for p in asc] == ["a", "b", "c", "nat"]
        assert [m.df.iat[p, 1] for p in desc] == ["c", "b", "a", "nat"]

    def test_timedelta_nat_last(self):
        m = PandasTableModel(pd.DataFrame({
            "t": pd.to_timedelta(["2h", None, "1h"]),
        }))
        assert m.sort_positions([(0, True)]) == [2, 0, 1]
        assert m.sort_positions([(0, False)]) == [0, 2, 1]

    def test_datetime_as_secondary_key_after_group(self):
        m = PandasTableModel(pd.DataFrame({
            "g": ["x", "x", "y", "x"],
            "d": pd.to_datetime(["2024-01-02", None, "2024-01-01", "2024-01-01"]),
        }))
        assert m.sort_positions([(0, True), (1, True)]) == [3, 0, 1, 2]
