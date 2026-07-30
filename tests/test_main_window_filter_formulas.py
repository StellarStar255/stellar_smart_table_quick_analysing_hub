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
from PyQt6.QtCore import QItemSelectionModel
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


class TestReviewFindings:
    """代码审查确认问题的回归测试（finding 5/7/8）"""

    def test_refilter_notifies_frozen_view_formulas(self, win):
        # finding 5: 筛选中输入的公式被转静态值时必须提示，不能无声丢弃
        apply_filter(win, FILTER_X_GT_15)
        win.model.setData(win.model.index(0, 1), '=A1*2')
        apply_filter(win, FILTER_X_GT_15,
                     {'col': 'X', 'condition': '小于', 'value': '100'})
        assert win.model.formulas == {}
        assert '静态值' in win.statusBar().currentMessage()

    def test_clear_filters_notifies_frozen_view_formulas(self, win):
        apply_filter(win, FILTER_X_GT_15)
        win.model.setData(win.model.index(0, 1), '=A1*2')
        win.clear_all_filters()
        assert '静态值' in win.statusBar().currentMessage()

    def _copy_cell(self, win, row, col):
        idx = win.model.index(row, col)
        win.table.setCurrentIndex(idx)
        win.table.selectionModel().select(
            idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        win.copy_selection(with_headers=False)

    def test_paste_shifts_formula_when_structure_unchanged(self, win):
        win.model.setData(win.model.index(0, 1), '=A1*2')
        self._copy_cell(win, 0, 1)
        win.table.setCurrentIndex(win.model.index(2, 1))
        win.paste_selection()
        assert win.model.formulas.get((2, 1)) == '=A3*2'

    def test_paste_falls_back_after_structure_change(self, win):
        # finding 7: 复制后结构变化，剪贴板旧公式作废，按值粘贴
        win.model.setData(win.model.index(0, 1), '=A1*2')
        self._copy_cell(win, 0, 1)
        win.model.insert_column(0)  # 结构变化：公式引用已被重写
        win.table.setCurrentIndex(win.model.index(2, 2))
        win.paste_selection()
        assert (2, 2) not in win.model.formulas  # 按值粘贴，不再是公式
        assert win.model.df.iat[2, 2] == 60     # 复制时的计算结果

    def test_parse_clipboard_keeps_blank_rows_for_formula_paste(self, win):
        # finding 8: 公式粘贴需与复制矩阵逐行对齐，空行不能被丢弃
        rows = win._parse_clipboard_text('a\t1\n\t\nb\t2', keep_blank=True)
        assert len(rows) == 3
        rows = win._parse_clipboard_text('a\t1\n\t\nb\t2')
        assert len(rows) == 2  # 按值粘贴维持旧行为
