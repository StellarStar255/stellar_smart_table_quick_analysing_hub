"""筛选状态下增删列：视图和 original_df 同步，行操作仍然被拦住。"""
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


def test_row_ops_are_still_blocked(win, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: warned.append(a[2]))
    before = len(win.model.df)
    win.insert_row(0)
    assert len(win.model.df) == before
    assert warned == [tr("筛选状态下不支持增删行，请先清除筛选")]
