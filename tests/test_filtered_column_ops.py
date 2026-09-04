"""筛选状态下增删行/列：视图和 original_df 同步；只有"设为表头"仍要求先清筛选。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

_app = QApplication.instance() or QApplication([])

from qtui.i18n import tr
from qtui.main_window import MainWindow


@pytest.fixture
def win():
    w = MainWindow()
    w.model.set_dataframe(pd.DataFrame({
        "城市": ["北京", "上海", "北京", "广州"],
        "数量": [1, 2, 3, 4],
    }))
    w.active_filters = [{"col": "城市", "condition": "值在列表中", "value": ["北京"]}]
    w._reapply_filters()
    yield w
    w.model.modified = False
    w.close()


def test_filter_is_active(win):
    assert len(win.model.df) == 2 and len(win.original_df) == 4


def test_insert_column_while_filtered_syncs_original(win):
    win._insert_col_at(1, "备注")
    assert list(win.model.df.columns) == ["城市", "备注", "数量"]
    assert list(win.original_df.columns) == ["城市", "备注", "数量"]
    assert len(win.original_df) == 4          # 原表行数不受影响


def test_edit_in_new_column_reaches_original(win):
    win._insert_col_at(2, "备注")
    win.model.setData(win.model.index(1, 2), "写点东西")   # 视图第 1 数据行
    orig_label = win._filtered_idx_map[0]
    assert win.original_df.at[orig_label, "备注"] == "写点东西"


def test_delete_column_while_filtered_syncs_original(win, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    win._delete_col_at(1)
    assert list(win.model.df.columns) == ["城市"]
    assert list(win.original_df.columns) == ["城市"]
    assert len(win.model.df) == 2             # 筛选还在


def test_deleting_the_filtered_column_drops_its_filter(win, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    win._delete_col_at(0)                     # 删掉"城市"——筛选依据没了
    assert win.active_filters == []
    assert list(win.model.df.columns) == ["数量"]
    assert len(win.model.df) == 4             # 筛选撤销后恢复全部行


def test_colors_and_suspended_formulas_follow_new_column(win):
    win.clear_all_filters()
    win.model.set_dataframe(pd.DataFrame({
        "a": [1, 2], "b": [3, 4], "c": [0, 0]}))
    win.model.setData(win.model.index(1, 2), "=A1+B1")     # c 列放个公式
    win.model.cell_colors[(0, 1)] = "#ff0000"
    win.active_filters = [{"col": "a", "condition": "值在列表中", "value": ["1"]}]
    win._reapply_filters()
    assert win._suspended_formulas == {(0, 2): "=A1+B1"}
    win._insert_col_at(0, "新")                # 在最前面插一列，列号都要 +1
    assert list(win._suspended_formulas) == [(0, 3)]
    assert win._suspended_formulas[(0, 3)] == "=B1+C1"     # 引用跟着右移
    assert win._orig_cell_colors == {(0, 2): "#ff0000"}


def test_promote_header_is_still_blocked(win, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: warned.append(a[2]))
    win.promote_row_to_header(1)
    assert warned == [tr("筛选状态下不支持此操作，请先清除筛选")]


class TestFilteredRowOps:
    """筛选视图：北京(原 0)、北京(原 2)；原表共 4 行。"""

    def test_insert_row_lands_after_the_visible_row_above(self, win):
        win.insert_row(1)                       # 在视图第 1 行（原 2）上面插
        # 原表：北京(0) 上海(1) [新] 北京(3) 广州(4)
        assert len(win.original_df) == 5
        assert pd.isna(win.original_df.at[2, "城市"])
        assert list(win.model.df["城市"].fillna("")) == ["北京", "", "北京"]
        assert win._filtered_idx_map == [0, 2, 3]

    def test_insert_at_top_goes_before_first_visible(self, win):
        win.insert_row(0)
        assert pd.isna(win.original_df.at[0, "城市"])
        assert win._filtered_idx_map == [0, 1, 3]

    def test_insert_at_end_goes_after_last_visible(self, win):
        win.insert_row(2)
        # 最后可见行原来是 2，新行插到原 3；广州被推到 4
        assert pd.isna(win.original_df.at[3, "城市"])
        assert win.original_df.at[4, "城市"] == "广州"
        assert win._filtered_idx_map == [0, 2, 3]

    def test_edit_inserted_row_reaches_original(self, win):
        win.insert_row(1)
        win.model.setData(win.model.index(2, 1), "77")    # 视图数据行 1 = 新行
        assert str(win.original_df.at[2, "数量"]) in ("77", "77.0")

    def test_delete_rows_removes_matching_original_rows(self, win, monkeypatch):
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: QMessageBox.StandardButton.Yes)
        win.table.selectionModel().select(
            win.model.index(2, 0),
            win.table.selectionModel().SelectionFlag.Select)    # 视图数据行 1
        win.delete_selected_rows()
        assert list(win.original_df["城市"]) == ["北京", "上海", "广州"]
        assert list(win.model.df["城市"]) == ["北京"]
        assert win._filtered_idx_map == [0]

    def test_ledgers_follow_row_insert_and_delete(self, win, monkeypatch):
        win.clear_all_filters()
        win.model.set_dataframe(pd.DataFrame({"a": [1, 2, 3], "b": [0, 0, 0]}))
        win.model.setData(win.model.index(3, 1), "=A3*2")     # 数据行 2 的 b
        win.model.cell_colors[(2, 0)] = "#ff0000"
        win.active_filters = [{"col": "a", "condition": "值在列表中",
                               "value": ["1", "3"]}]
        win._reapply_filters()
        assert win._suspended_formulas == {(2, 1): "=A3*2"}
        win.insert_row(0)                                       # 原表最前面插一行
        assert win._suspended_formulas == {(3, 1): "=A4*2"}
        assert win._orig_cell_colors == {(3, 0): "#ff0000"}
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: QMessageBox.StandardButton.Yes)
        win.table.selectionModel().select(
            win.model.index(1, 0),
            win.table.selectionModel().SelectionFlag.Select)    # 删掉新插的那行
        win.delete_selected_rows()
        assert win._suspended_formulas == {(2, 1): "=A3*2"}
        assert win._orig_cell_colors == {(2, 0): "#ff0000"}

    def test_clear_filter_after_row_ops_restores_consistent_table(self, win):
        win.insert_row(1)
        win.model.setData(win.model.index(2, 0), "新城")
        win.clear_all_filters()
        assert list(win.model.df["城市"]) == ["北京", "上海", "新城", "北京", "广州"]
