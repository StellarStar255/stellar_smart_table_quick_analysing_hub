# -*- coding: utf-8 -*-
"""互动式新手教程：首启标记、示例数据载入/清除、目标定位与跳过、
互动步骤条件满足后自动前进、用户自己的文件上不要求改数据的操作。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import QPoint, QSettings
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui import i18n
from qtui import main_window as mw_mod
from qtui import onboarding_tour as ot
from qtui.main_window import MainWindow


def _pump(n=5):
    for _ in range(n):
        QApplication.processEvents()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(mw_mod, "_make_settings",
                        lambda: QSettings(ini, QSettings.Format.IniFormat))
    monkeypatch.setattr(i18n, "_ACTIVE", i18n.LANG_ZH)


@pytest.fixture
def win():
    w = MainWindow()
    w.resize(1300, 850)
    w.show()
    _pump()
    yield w
    tour = getattr(w, "_onboarding_tour", None)
    if tour is not None and tour.is_active():
        tour.skip()
    w.model.modified = False
    w.close()


def _goto_key(tour, key):
    idx = [s.key for s in tour.steps].index(key)
    tour._goto(idx)
    assert tour.current_step.key == key
    return tour.current_step


def _tick_until_done(tour):
    tour._tick()
    assert tour._done_flag
    assert tour._advance_timer.isActive()


class TestFlag:
    def test_first_launch_then_marked(self, win):
        assert ot.should_show_onboarding(win._settings)
        tour = win.start_onboarding_tour()
        tour.skip()
        assert not ot.should_show_onboarding(win._settings)

    def test_help_menu_entry(self, win):
        texts = [a.text() for m in win.menuBar().actions() if m.menu()
                 for a in m.menu().actions()]
        assert "新手教程" in texts


class TestSampleData:
    def test_blank_document_gets_sample_and_restored_after(self, win):
        tour = win.start_onboarding_tour()
        assert tour.sample_loaded
        assert list(win.model.df.columns)[:4] == ["日期", "城市", "品类", "销量"]
        assert not win.model.modified
        tour.skip()
        assert win.current_file is None
        assert win.model.df.isna().all().all()     # 回到空白表

    def test_user_data_is_left_alone(self, win):
        win.model.set_dataframe(pd.DataFrame({"x": [1, 2], "y": [3, 4]}))
        tour = win.start_onboarding_tour()
        assert not tour.sample_loaded
        assert list(win.model.df.columns) == ["x", "y"]
        # 会改数据的步骤在用户表上只讲解，不要求操作
        step = _goto_key(tour, "move_column")
        assert not tour.is_interactive(step)
        assert tour.overlay.next_btn.text() == "下一步"
        # 不改数据的互动步骤照常
        assert tour.is_interactive(_goto_key(tour, "context_menu"))
        tour.skip()
        assert list(win.model.df.columns) == ["x", "y"]


class TestSteps:
    def test_every_step_renders_and_finish_closes(self, win):
        tour = win.start_onboarding_tour()
        seen = []
        while tour.is_active():
            seen.append(tour.current_step.key)
            assert tour.overlay.title_label.text()
            tour.next()
        assert seen[0] == "welcome" and seen[-1] == "done"
        assert not ot.should_show_onboarding(win._settings)

    def test_hidden_target_step_is_skipped(self, win):
        win.cell_preview_dock.hide()
        _pump()
        tour = win.start_onboarding_tour()
        keys = []
        while tour.is_active():
            keys.append(tour.current_step.key)
            tour.next()
        assert "cell_preview" not in keys
        assert "python" in keys           # 可选目标：工具栏隐藏时卡片居中照常讲

    def test_hole_punches_through_and_card_stays_clickable(self, win):
        tour = win.start_onboarding_tour()
        _goto_key(tour, "files")
        hole = tour.overlay.hole()
        assert hole is not None
        btn = win._toolbar_buttons["打开"]
        center = btn.mapTo(win, btn.rect().center())
        mask = tour.overlay.mask()
        assert not mask.contains(center)                     # 点击落到真实按钮
        assert mask.contains(tour.overlay.card.geometry().center())
        assert mask.contains(QPoint(2, win.height() - 2))    # 洞外被遮罩吃掉

    def test_move_column_step_completes(self, win):
        tour = win.start_onboarding_tour()
        _goto_key(tour, "move_column")
        assert tour.overlay.next_btn.text() == "跳过此步"
        tour._tick()
        assert not tour._done_flag
        win.move_columns([1], 4)
        _tick_until_done(tour)
        assert tour.overlay.hint_label.text().startswith("✓")

    def test_formula_step_completes(self, win):
        tour = win.start_onboarding_tour()
        _goto_key(tour, "formula")
        col = list(win.model.df.columns).index("金额")
        win.model.setData(win.model.index(1, col), "=D2*E2")
        _tick_until_done(tour)
        assert win.model.df.iat[0, col] == pytest.approx(420)

    def test_filter_step_completes_when_popup_opens(self, win):
        from qtui.header_filter import ColumnFilterPopup
        tour = win.start_onboarding_tour()
        _goto_key(tour, "filter")
        popup = ColumnFilterPopup(win, "城市", [("上海", 3), ("北京", 3)])
        popup.show()
        try:
            _tick_until_done(tour)
        finally:
            popup.close()

    def test_escape_exits(self, win):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        tour = win.start_onboarding_tour()
        QTest.keyClick(tour.overlay, Qt.Key.Key_Escape)
        assert not tour.is_active()
