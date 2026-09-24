# -*- coding: utf-8 -*-
"""筛选条件随文件记忆：关闭后重新打开自动恢复（按 sheet 分别记），
不算修改文档；引用了已不存在列的条件忽略；清除筛选后不再恢复。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui import main_window as mw_mod
from qtui.main_window import MainWindow

CITY = {"col": "城市", "condition": "值在列表中", "value": ["北京"]}
BIG = {"col": "数量", "condition": "大于", "value": "1"}


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(mw_mod, "_make_settings",
                        lambda: QSettings(ini, QSettings.Format.IniFormat))


@pytest.fixture
def windows():
    made = []

    def make():
        w = MainWindow()
        made.append(w)
        return w
    yield make
    for w in made:
        w.model.modified = False
        w.close()


def _open(make, path):
    w = make()
    w.load_file(path)
    _app.processEvents()
    return w


@pytest.fixture
def csv_path(tmp_path):
    path = str(tmp_path / "data.csv")
    pd.DataFrame({"城市": ["北京", "上海", "北京", "广州"],
                  "数量": [1, 2, 3, 4]}).to_csv(path, index=False)
    return path


@pytest.fixture
def xlsx_path(tmp_path):
    path = str(tmp_path / "book.xlsx")
    with pd.ExcelWriter(path) as xw:
        pd.DataFrame({"城市": ["北京", "上海", "北京"], "数量": [1, 2, 3]}).to_excel(
            xw, sheet_name="甲", index=False)
        pd.DataFrame({"城市": ["广州", "北京"], "数量": [5, 6]}).to_excel(
            xw, sheet_name="乙", index=False)
    return path


def _apply(w, *filters):
    w.active_filters = [dict(f) for f in filters]
    w._reapply_filters()


def test_csv_filters_restored_on_reopen(windows, csv_path):
    w = _open(windows, csv_path)
    _apply(w, CITY, BIG)
    assert len(w.model.df) == 1
    w.close()

    w2 = _open(windows, csv_path)
    assert w2.active_filters == [CITY, BIG]
    assert w2.model.df["数量"].tolist() == [3]
    assert len(w2.original_df) == 4
    assert not w2.model.modified           # 恢复筛选不算改动文档
    assert w2.filter_bar.isVisibleTo(w2)


def test_saving_file_keeps_filters(windows, csv_path):
    w = _open(windows, csv_path)
    _apply(w, CITY)
    w.model.setData(w.model.index(1, 1), "10")   # 改一格再保存
    assert w.save_file() is True
    _app.processEvents()
    w.close()
    w2 = _open(windows, csv_path)
    assert w2.active_filters == [CITY]
    assert w2.original_df["数量"].tolist() == [10, 2, 3, 4]


def test_clear_filters_is_remembered_too(windows, csv_path):
    w = _open(windows, csv_path)
    _apply(w, CITY)
    w.clear_all_filters()
    w.close()
    w2 = _open(windows, csv_path)
    assert w2.active_filters == []
    assert len(w2.model.df) == 4


def test_per_sheet_filters(windows, xlsx_path):
    w = _open(windows, xlsx_path)
    assert w.current_sheet == "甲"
    _apply(w, CITY)
    w.switch_sheet("乙")
    _apply(w, BIG)
    w.switch_sheet("甲")          # 最后停在「甲」
    assert w.model.modified       # xlsx 的筛选要随文件保存，算修改
    w.model.modified = False      # 不保存直接关：仍由本机配置记住
    w.close()

    w2 = _open(windows, xlsx_path)
    assert w2.current_sheet == "甲"
    assert w2.active_filters == [CITY]
    assert w2.model.df["数量"].tolist() == [1, 3]
    w2.switch_sheet("乙")         # 没访问过的 sheet 切过去时恢复
    assert w2.active_filters == [BIG]
    assert w2.model.df["数量"].tolist() == [5, 6]


def test_missing_column_filter_ignored(windows, csv_path):
    w = _open(windows, csv_path)
    _apply(w, CITY, BIG)
    w.close()
    # 文件在外部被改：「数量」列没了
    pd.DataFrame({"城市": ["北京", "上海"]}).to_csv(csv_path, index=False)
    w2 = _open(windows, csv_path)
    assert w2.active_filters == [CITY]
    assert "已不存在" in w2.statusBar().currentMessage() or \
        "no longer exist" in w2.statusBar().currentMessage()


def test_corrupt_config_entries_ignored(windows, csv_path):
    w = _open(windows, csv_path)
    w.close()
    with open(mw_mod.FILE_CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg[os.path.abspath(csv_path)]["filters"] = {"": [{"col": 1}, "junk", CITY]}
    with open(mw_mod.FILE_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    w2 = _open(windows, csv_path)
    assert w2.active_filters == [CITY]


def test_rename_filtered_column_updates_memory(windows, csv_path):
    w = _open(windows, csv_path)
    _apply(w, CITY)
    w._after_column_rename(0, "城市", "City")
    with open(mw_mod.FILE_CONFIG_PATH, encoding="utf-8") as f:
        saved = json.load(f)[os.path.abspath(csv_path)]["filters"]
    assert saved[""][0]["col"] == "City"
