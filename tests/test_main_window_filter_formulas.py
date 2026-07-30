"""主窗口集成测试：筛选期间公式挂起、清除筛选后恢复

需要 QApplication（widgets）；CI 里用 QT_QPA_PLATFORM=offscreen 无头运行。
不跑事件循环，MainWindow 里的 QTimer.singleShot（自动加载最近文件、
检查更新）不会触发。

运行: python3 -m pytest tests/ -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui.main_window import MainWindow

# filter_engine 的条件标识符是中文（与界面一致）
FILTER_X_GT_15 = {'col': 'X', 'condition': '大于', 'value': '15'}


@pytest.fixture
def win():
    w = MainWindow()
    w.model.set_dataframe(
        pd.DataFrame({'X': [30.0, 10.0, 20.0], 'Y': [1.0, 2.0, 3.0]}))
    yield w
    # 清掉修改标志再关闭，否则 closeEvent 弹"是否保存"模态框，无头环境下挂死
    w.model.modified = False
    w.close()


def apply_filter(w, *filters):
    w.active_filters = [dict(f) for f in filters]
    w._reapply_filters()


class TestFilterSuspendsFormulas:
    def test_suspend_on_filter_keeps_static_value(self, win):
        win.model.setData(win.model.index(0, 1), '=A1*2')  # 60
        apply_filter(win, FILTER_X_GT_15)
        assert len(win.model.df) == 2  # X=30, X=20
        assert win.model.formulas == {}
        assert win._suspended_formulas == {(0, 1): '=A1*2'}
        assert win.model.df.iat[0, 1] == 60  # 静态值保留

    def test_restore_recalculates_with_edits_made_while_filtered(self, win):
        win.model.setData(win.model.index(0, 1), '=A1*2')
        apply_filter(win, FILTER_X_GT_15)
        # 筛选中编辑被引用的单元格（显示行 0 = 原行 0）：30 -> 50
        win.model.setData(win.model.index(0, 0), '50')
        assert win.original_df.iloc[0, 0] == 50
        win.clear_all_filters()
        assert win.model.formulas == {(0, 1): '=A1*2'}
        assert win.model.df.iat[0, 1] == 100  # 50*2

    def test_overwriting_formula_cell_while_filtered_drops_formula(self, win):
        win.model.setData(win.model.index(0, 1), '=A1*2')
        apply_filter(win, FILTER_X_GT_15)
        win.model.setData(win.model.index(0, 1), '999')
        win.clear_all_filters()
        assert win.model.formulas == {}
        assert win.model.df.iat[0, 1] == 999

    def test_refilter_keeps_suspended_formulas(self, win):
        win.model.setData(win.model.index(0, 1), '=A1*2')
        apply_filter(win, FILTER_X_GT_15)
        apply_filter(win, FILTER_X_GT_15,
                     {'col': 'X', 'condition': '小于', 'value': '100'})
        assert win._suspended_formulas == {(0, 1): '=A1*2'}
        win.clear_all_filters()
        assert win.model.formulas == {(0, 1): '=A1*2'}

    def test_filter_without_formulas(self, win):
        apply_filter(win, FILTER_X_GT_15)
        assert win._suspended_formulas is None
        win.clear_all_filters()
        assert win.model.formulas == {}
        assert len(win.model.df) == 3
