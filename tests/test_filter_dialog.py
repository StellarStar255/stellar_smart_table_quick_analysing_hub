"""筛选对话框测试：搜索过滤 + 按值勾选的交互"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui.filter_dialog import FilterDialog


def make_dialog():
    df = pd.DataFrame({'F': ['SHIRTS', 'TSHIRTS', 'SWEATSHIRTS',
                             'BELT BAG', 'HAT']})
    return FilterDialog(None, df)


class TestFilterByValueSearch:
    def test_ok_uses_only_visible_checked(self):
        # 回归：搜索后被隐藏的值（默认全选）不得混进筛选结果
        dlg = make_dialog()
        dlg.search_edit.setText('SHIRTS')    # 可见: SHIRTS/TSHIRTS/SWEATSHIRTS
        dlg.select_all_cb.setChecked(False)  # 取消可见项勾选
        for i in range(dlg.value_list.count()):
            item = dlg.value_list.item(i)
            if item.text() == 'SHIRTS':
                item.setCheckState(Qt.CheckState.Checked)
        dlg._on_ok()
        assert dlg.result == {'col': 'F', 'condition': '值在列表中',
                              'value': ['SHIRTS']}

    def test_search_all_visible_checked_still_filters(self):
        # 搜索后可见项全勾选 != 全表无筛选，应生成只含可见值的条件
        dlg = make_dialog()
        dlg.search_edit.setText('SHIRTS')
        dlg._on_ok()
        assert dlg.result is not None
        assert sorted(dlg.result['value']) == ['SHIRTS', 'SWEATSHIRTS', 'TSHIRTS']

    def test_all_checked_without_search_means_no_filter(self):
        dlg = make_dialog()
        dlg._on_ok()
        assert dlg.result is None  # 全选等于没筛选

    def test_toggle_all_affects_visible_only(self):
        dlg = make_dialog()
        dlg.search_edit.setText('SHIRTS')
        dlg.select_all_cb.setChecked(False)
        hidden_states = [dlg.value_list.item(i).checkState()
                         for i in range(dlg.value_list.count())
                         if dlg.value_list.item(i).isHidden()]
        # 隐藏项不受"全选"开关影响（但 _on_ok 已不再统计它们）
        assert all(s == Qt.CheckState.Checked for s in hidden_states)
