"""Excel 式列头筛选：箭头点击区、值勾选弹层、应用到 active_filters。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest
from PyQt6.QtCore import Qt, QPoint, QPointF, QEvent, QTimer
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui import filter_engine
from qtui.header_filter import BLANK_LABEL, ColumnFilterPopup, FilterHeaderView
from qtui.main_window import MainWindow


@pytest.fixture
def win():
    w = MainWindow()
    df = pd.DataFrame({
        "城市": ["北京", "上海", "北京", "广州", None],
        "数量": [10.0, 9.0, 100.0, 10.0, 2.0],
    })
    w.model.set_dataframe(df)
    w.resize(700, 400)
    w.show()
    _app.processEvents()
    yield w
    w.model.modified = False
    w.close()


class TestValueCounts:
    def test_counts_use_display_text_and_blank(self):
        s = pd.Series(["a", "b", "a", None])
        assert filter_engine.value_counts(s) == [("a", 2), ("b", 1), ("", 1)]

    def test_numeric_column_sorts_numerically(self):
        s = pd.Series([10.0, 9.0, 100.0, 9.0])
        assert filter_engine.value_counts(s) == [("9", 2), ("10", 1), ("100", 1)]

    def test_in_list_filter_matches_display_text(self):
        df = pd.DataFrame({"n": [10.0, 9.5, np.nan]})
        out, _ = filter_engine.apply_filters(
            df, [{"col": "n", "condition": "值在列表中", "value": ["10", ""]}])
        assert len(out) == 2          # 10.0 按 "10" 匹配，NaN 按 "" 匹配


class TestHeaderArrow:
    def test_arrow_click_emits_filter_signal(self, win):
        header = win.table.horizontalHeader()
        assert isinstance(header, FilterHeaderView)
        got = []
        header.filterClicked.disconnect()      # 别真弹出筛选层
        header.filterClicked.connect(got.append)
        rect = header.arrow_rect_at(1)
        ev = QMouseEvent(QEvent.Type.MouseButtonPress,
                         QPointF(rect.center()), QPointF(rect.center()),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        header.mousePressEvent(ev)
        assert got == [1]

    def test_click_away_from_arrow_does_not_open_filter(self, win):
        header = win.table.horizontalHeader()
        got = []
        header.filterClicked.disconnect()
        header.filterClicked.connect(got.append)
        pos = QPointF(header.sectionViewportPosition(1) + 4, header.height() / 2)
        ev = QMouseEvent(QEvent.Type.MouseButtonPress, pos, pos,
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        header.mousePressEvent(ev)
        assert got == []

    def test_real_click_on_arrow_opens_the_popup(self, win, monkeypatch):
        """走真实鼠标事件（表头视口 -> QHeaderView.mousePressEvent）的整条路。"""
        opened = []
        monkeypatch.setattr(ColumnFilterPopup, "popup_at",
                            lambda self, pos: opened.append(pos) or 0)
        header = win.table.horizontalHeader()
        QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier,
                         header.arrow_rect_at(1).center())
        assert len(opened) == 1

    def test_popup_anchors_under_the_column_header(self, win, monkeypatch):
        got = []
        monkeypatch.setattr(ColumnFilterPopup, "popup_at",
                            lambda self, pos: got.append(pos) or 0)
        win.open_column_filter(1)
        header = win.table.horizontalHeader()
        expect = header.viewport().mapToGlobal(
            QPoint(header.sectionViewportPosition(1), header.height()))
        assert (got[0].x(), got[0].y()) == (expect.x(), expect.y())

    def test_column_menu_uses_the_same_popup(self, win, monkeypatch):
        opened = []
        monkeypatch.setattr(type(win), "open_column_filter",
                            lambda self, col: opened.append(col))
        monkeypatch.setattr(type(win), "open_filter_dialog",
                            lambda self, **kw: opened.append("old dialog"))
        from PyQt6.QtWidgets import QMenu
        from qtui.i18n import tr
        wanted = tr("筛选此列 ({})...").format("数量")
        monkeypatch.setattr(QMenu, "exec", lambda self, *a: (
            [a_ for a_ in self.actions() if a_.text() == wanted][0].trigger()))
        header = win.table.horizontalHeader()
        win._show_col_menu(QPoint(header.sectionViewportPosition(1) + 5, 5))
        assert opened == [1]

    def test_filtered_columns_get_the_funnel(self, win):
        win.active_filters = [{"col": "数量", "condition": "值在列表中",
                               "value": ["10"]}]
        win._update_filter_indicators()
        assert win.table.horizontalHeader()._filtered_cols == {1}


class TestPopup:
    def _popup(self, counts, **kw):
        return ColumnFilterPopup(None, "城市", counts, **kw)

    def test_all_checked_means_clear(self):
        p = self._popup([("a", 1), ("b", 2)])
        p._accept()
        assert p.result == "clear"

    def test_partial_selection_returns_values(self):
        p = self._popup([("a", 1), ("b", 2)])
        p.value_list.item(1).setCheckState(Qt.CheckState.Unchecked)
        p._accept()
        assert p.result == ["a"]

    def test_existing_filter_prechecks_only_its_values(self):
        p = self._popup([("a", 1), ("b", 2)], checked={"b"}, has_filter=True)
        states = [p.value_list.item(i).checkState() for i in range(2)]
        assert states == [Qt.CheckState.Unchecked, Qt.CheckState.Checked]
        assert p.clear_btn.isEnabled()

    def test_search_checks_only_matches(self):
        p = self._popup([("apple", 1), ("banana", 2), ("grape", 3)])
        p.search_edit.setText("ap")
        checked = p.checked_values()
        assert checked == ["apple", "grape"]        # 香蕉不含 ap，被取消勾选
        assert p.value_list.item(1).isHidden()
        p.search_edit.setText("")                   # 清空搜索恢复初始勾选
        assert p.checked_values() == ["apple", "banana", "grape"]

    def test_invert_flips_visible_items(self):
        p = self._popup([("a", 1), ("b", 2)])
        p.value_list.item(0).setCheckState(Qt.CheckState.Unchecked)
        p._invert()
        assert p.checked_values() == ["a"]

    def test_blank_value_is_labelled(self):
        p = self._popup([("", 3)])
        assert BLANK_LABEL in p.value_list.item(0).text()
        assert p.value_list.item(0).data(Qt.ItemDataRole.UserRole) == ""

    def test_nothing_checked_clears_when_filter_exists(self):
        p = self._popup([("a", 1)], checked={"a"}, has_filter=True)
        p.value_list.item(0).setCheckState(Qt.CheckState.Unchecked)
        p._accept()
        assert p.result == "clear"

    def test_more_conditions_hands_off_to_dialog(self):
        p = self._popup([("a", 1)])
        p._advanced()
        assert p.result == "advanced"

    def test_click_outside_closes_and_cancels(self):
        p = self._popup([("a", 1)])
        p.move(100, 100)
        p.resize(200, 200)
        p.show()
        _app.processEvents()
        outside = QPointF(600, 600)
        ev = QMouseEvent(QEvent.Type.MouseButtonPress, outside, outside,
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        assert p.eventFilter(p, ev) is True        # 吞掉这一下
        assert not p.isVisible()
        assert p.result is None                    # 等于取消，不改筛选

    def test_click_inside_keeps_it_open(self):
        p = self._popup([("a", 1)])
        p.move(100, 100)
        p.resize(200, 200)
        p.show()
        _app.processEvents()
        inside = QPointF(p.frameGeometry().center())
        ev = QMouseEvent(QEvent.Type.MouseButtonPress, inside, inside,
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        assert p.eventFilter(p, ev) is False
        assert p.isVisible()
        p.close()

    def test_popup_at_returns_after_accept(self):
        """popup_at 自跑事件循环：确定/取消后必须真的返回，不能卡住。"""
        p = self._popup([("a", 1), ("b", 1)])
        QTimer.singleShot(0, lambda: (
            p.value_list.item(1).setCheckState(Qt.CheckState.Unchecked),
            p._accept()))
        p.popup_at(QPoint(50, 50))
        assert p.result == ["a"]
        assert not p.isVisible()

    def test_sort_buttons_report_order(self):
        p = self._popup([("a", 1)])
        p._sort(False)
        assert p.sort_ascending is False and p.result is None


class TestApplyThroughWindow:
    def _run_popup(self, win, monkeypatch, action):
        """拦住弹层的 exec，直接在弹层上执行 action 后返回。"""
        def fake_popup_at(self, _pos):
            action(self)
            return 1
        monkeypatch.setattr(ColumnFilterPopup, "popup_at", fake_popup_at)

    def test_selecting_values_filters_rows(self, win, monkeypatch):
        # 值按显示文本排序：上海 / 北京 / 广州 / (空白)
        self._run_popup(win, monkeypatch, lambda p: (
            p.value_list.item(0).setCheckState(Qt.CheckState.Unchecked),
            p.value_list.item(2).setCheckState(Qt.CheckState.Unchecked),
            p.value_list.item(3).setCheckState(Qt.CheckState.Unchecked),
            p._accept()))
        win.open_column_filter(0)
        assert win.active_filters == [
            {"col": "城市", "condition": "值在列表中", "value": ["北京"]}]
        assert list(win.model.df["城市"]) == ["北京", "北京"]
        assert win.table.horizontalHeader()._filtered_cols == {0}

    def test_clear_from_popup_removes_column_filter(self, win, monkeypatch):
        win.active_filters = [{"col": "城市", "condition": "值在列表中",
                               "value": ["北京"]}]
        win._reapply_filters()
        assert len(win.model.df) == 2
        self._run_popup(win, monkeypatch, lambda p: p._clear())
        win.open_column_filter(0)
        assert win.active_filters == []
        assert len(win.model.df) == 5
        assert win.table.horizontalHeader()._filtered_cols == set()

    def test_refiltering_same_column_replaces_not_stacks(self, win, monkeypatch):
        win.active_filters = [{"col": "城市", "condition": "值在列表中",
                               "value": ["北京"]}]
        win._reapply_filters()
        # 打开时只有北京被勾上；改成只勾上海
        self._run_popup(win, monkeypatch, lambda p: (
            p.value_list.item(1).setCheckState(Qt.CheckState.Unchecked),
            p.value_list.item(0).setCheckState(Qt.CheckState.Checked),
            p._accept()))
        win.open_column_filter(0)
        assert len(win.active_filters) == 1
        assert win.active_filters[0]["value"] == ["上海"]

    def test_advanced_opens_condition_dialog(self, win, monkeypatch):
        opened = []
        monkeypatch.setattr(type(win), "open_filter_dialog",
                            lambda self, preset_col=None, edit_index=None:
                            opened.append(preset_col))
        self._run_popup(win, monkeypatch, lambda p: p._advanced())
        win.open_column_filter(0)
        assert opened == ["城市"]
        assert win.active_filters == []

    def test_values_cascade_from_other_columns_filters(self, win, monkeypatch):
        """其它列筛过之后，本列只列剩下的值和计数（Excel 行为）。"""
        win.active_filters = [{"col": "数量", "condition": "值在列表中",
                               "value": ["10"]}]           # 只剩北京、广州两行
        win._reapply_filters()
        seen = {}
        monkeypatch.setattr(ColumnFilterPopup, "__init__",
                            lambda self, parent, colname, counts, **kw: (
                                seen.update(counts=counts), None)[1])
        monkeypatch.setattr(ColumnFilterPopup, "popup_at", lambda self, pos: 0)
        monkeypatch.setattr(ColumnFilterPopup, "sort_ascending", None, raising=False)
        monkeypatch.setattr(ColumnFilterPopup, "result", None, raising=False)
        win.open_column_filter(0)
        assert seen["counts"] == [("北京", 1), ("广州", 1)]

    def test_own_column_filter_does_not_narrow_its_own_list(self, win, monkeypatch):
        win.active_filters = [{"col": "城市", "condition": "值在列表中",
                               "value": ["北京"]}]
        win._reapply_filters()
        seen = {}
        monkeypatch.setattr(ColumnFilterPopup, "__init__",
                            lambda self, parent, colname, counts, **kw: (
                                seen.update(counts=counts), None)[1])
        monkeypatch.setattr(ColumnFilterPopup, "popup_at", lambda self, pos: 0)
        monkeypatch.setattr(ColumnFilterPopup, "sort_ascending", None, raising=False)
        monkeypatch.setattr(ColumnFilterPopup, "result", None, raising=False)
        win.open_column_filter(0)
        assert [v for v, _ in seen["counts"]] == ["上海", "北京", "广州", ""]

    def test_sort_from_popup_sorts_column(self, win, monkeypatch):
        self._run_popup(win, monkeypatch, lambda p: p._sort(True))
        win.open_column_filter(1)
        assert list(win.model.df["数量"]) == [2.0, 9.0, 10.0, 10.0, 100.0]
        assert win.active_filters == []
