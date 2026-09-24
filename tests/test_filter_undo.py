# -*- coding: utf-8 -*-
"""筛选可撤销/重做（⌘Z / ⇧⌘Z），且与单元格编辑的撤销按时间顺序交错：
筛选前的编辑在筛选后仍能撤销，筛选中的编辑撤销时对准原来那一行。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui.main_window import MainWindow, HEADER_ROWS

CITY_BJ = {"col": "城市", "condition": "值在列表中", "value": ["北京"]}
BIG = {"col": "数量", "condition": "大于", "value": "1"}


@pytest.fixture
def win():
    w = MainWindow()
    w.model.set_dataframe(pd.DataFrame({
        "城市": ["北京", "上海", "北京", "广州", "北京"],
        "数量": [1, 2, 3, 4, 5],
    }))
    w._reset_history()
    w.model.modified = False
    yield w
    w.model.modified = False
    w.close()


def _filter(w, *filters):
    w.active_filters = [dict(f) for f in filters]
    w._reapply_filters()


def _edit(w, data_row, col, value):
    w.model.setData(w.model.index(data_row + HEADER_ROWS, col), value)


def _full(w):
    df = w.original_df if w.original_df is not None else w.model.df
    return df["数量"].tolist()


def test_undo_and_redo_a_filter(win):
    _filter(win, CITY_BJ)
    assert len(win.model.df) == 3
    win.undo()
    assert win.active_filters == [] and len(win.model.df) == 5
    assert not win.filter_bar.isVisibleTo(win)
    win.redo()
    assert win.active_filters == [CITY_BJ] and len(win.model.df) == 3


def test_undo_clear_filters_brings_them_back(win):
    _filter(win, CITY_BJ)
    _filter(win, CITY_BJ, BIG)       # 逐个加条件，每次一条记录
    win.clear_all_filters()
    win.undo()
    assert win.active_filters == [CITY_BJ, BIG]
    assert win.model.df["数量"].tolist() == [3, 5]
    win.undo()
    assert win.active_filters == [CITY_BJ]
    win.undo()
    assert win.active_filters == []
    win.undo()                       # 没有更早的历史：什么也不做
    assert len(win.model.df) == 5


def test_edit_before_filter_still_undoable(win):
    _edit(win, 1, 1, "20")           # 上海 2 -> 20
    _filter(win, CITY_BJ)
    win.undo()                       # 先撤筛选
    assert win.active_filters == []
    win.undo()                       # 再撤筛选前的编辑
    assert _full(win) == [1, 2, 3, 4, 5]
    win.redo()
    win.redo()
    assert win.active_filters == [CITY_BJ]
    assert _full(win) == [1, 20, 3, 4, 5]


def test_edit_inside_filter_undone_in_order(win):
    _filter(win, CITY_BJ)
    _edit(win, 1, 1, "30")           # 视图第 2 行 = 原表第 3 行
    _filter(win, CITY_BJ, BIG)
    win.undo()                       # 撤第二个条件
    assert win.active_filters == [CITY_BJ]
    win.undo()                       # 撤筛选中的编辑，落在原来那一行
    assert _full(win) == [1, 2, 3, 4, 5]
    win.undo()
    assert win.active_filters == []


def test_view_restored_exactly_even_if_edit_breaks_filter(win):
    """筛选中把一行改得不再满足条件，撤销到这个视图时它仍在原位置。"""
    _filter(win, CITY_BJ)
    _edit(win, 0, 0, "上海")         # 视图第 1 行不再是北京
    _filter(win, CITY_BJ, BIG)
    win.undo()
    assert win.model.df["城市"].tolist() == ["上海", "北京", "北京"]
    win.undo()                       # 撤销编辑：对准的仍是视图第 1 行
    assert win.model.df["城市"].tolist() == ["北京", "北京", "北京"]
    assert win.original_df["城市"].tolist()[0] == "北京"


def test_new_edit_after_undo_discards_filter_redo(win):
    _filter(win, CITY_BJ)
    win.undo()
    _edit(win, 0, 1, "9")
    win.redo()                       # 重做链已被新编辑截断
    assert win.active_filters == []
    assert _full(win) == [9, 2, 3, 4, 5]


def test_undo_filter_does_not_mark_modified(win):
    _filter(win, CITY_BJ)
    win.undo()
    win.redo()
    assert not win.model.modified


def test_column_popup_filter_undoable(win, monkeypatch):
    from qtui import header_filter
    monkeypatch.setattr(header_filter.ColumnFilterPopup, "popup_at",
                        lambda self, pos: setattr(self, "result", ["上海"]) or self.result)
    win.open_column_filter(0)
    assert len(win.model.df) == 1
    win.undo()
    assert len(win.model.df) == 5


def test_remove_filter_chip_undoable(win):
    _filter(win, CITY_BJ, BIG)
    win.remove_filter(0)
    assert win.active_filters == [BIG]
    win.undo()
    assert win.active_filters == [CITY_BJ, BIG]


def test_structure_change_while_filtered_resets_history(win):
    _filter(win, CITY_BJ)
    win.insert_row(0)
    assert not win._filter_undo
    win.undo()                       # 不应把结构操作前的视图套到新原表上
    assert win.active_filters == [CITY_BJ]
