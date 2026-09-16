"""查找替换：缺失值不是 "nan"/"None" 文本，搜索与替换都按表格显示文本进行。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

_app = QApplication.instance() or QApplication([])

from qtui.find_dialog import FindReplaceDialog
from qtui.pandas_model import PandasTableModel


class _Host(QWidget):
    def __init__(self, df):
        super().__init__()
        self.model = PandasTableModel(df)
        self.jumps = []

    def jump_to_cell(self, row, col):
        self.jumps.append((row, col))


def _dialog(df, monkeypatch=None):
    host = _Host(df)
    dlg = FindReplaceDialog(host)
    if monkeypatch is not None:
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    return host, dlg


class TestNullsAreNotText:
    def test_search_skips_nan_and_none(self):
        host, dlg = _dialog(pd.DataFrame({'t': ['banana', None, np.nan, 'None'],
                                          'v': [1.0, np.nan, 3.0, None]}))
        dlg.find_edit.setText('n')
        assert dlg._search() == [(0, 0), (3, 0)]       # 只有真正含 n 的文本
        dlg.find_edit.setText('a')
        assert dlg._search() == [(0, 0)]               # NaN 不是 "nan"

    def test_replace_all_does_not_touch_nan(self, monkeypatch):
        # 回归：[1.0, NaN, 3.0] 里 a→b 曾把 NaN 改成 'nbn' 并报告替换 3 处
        host, dlg = _dialog(pd.DataFrame({'v': [1.0, np.nan, 3.0]}), monkeypatch)
        dlg.find_edit.setText('a')
        dlg.replace_edit.setText('b')
        dlg.replace_all()
        assert host.model.df['v'].tolist()[0] == 1.0
        assert np.isnan(host.model.df['v'].tolist()[1])
        assert host.model.df['v'].tolist()[2] == 3.0
        assert host.model.df['v'].dtype.kind == 'f'
        assert not host.model.modified

    def test_replace_uses_display_text_of_numbers(self, monkeypatch):
        host, dlg = _dialog(pd.DataFrame({'v': [10.0, np.nan, 2.5]}), monkeypatch)
        dlg.find_edit.setText('10')
        dlg.replace_edit.setText('20')
        dlg.replace_all()
        vals = host.model.df['v'].tolist()
        assert vals[0] == 20.0 and np.isnan(vals[1]) and vals[2] == 2.5
        assert '1' in dlg.status.text()                # 已替换 1 处

    def test_replace_current_skips_missing_cell(self, monkeypatch):
        host, dlg = _dialog(pd.DataFrame({'t': ['xa', None, 'ya']}), monkeypatch)
        dlg.find_edit.setText('a')
        dlg.replace_edit.setText('z')
        dlg.find_next()                                 # 定位到 (0, 0)
        dlg.replace_current()
        assert host.model.df['t'].tolist()[0] == 'xz'
        assert pd.isna(host.model.df['t'].tolist()[1])   # pandas 3 的 str dtype 缺失值是 NaN
        dlg.replace_current()
        vals = host.model.df['t'].tolist()
        assert vals[0] == 'xz' and pd.isna(vals[1]) and vals[2] == 'yz'
