# -*- coding: utf-8 -*-
"""
筛选对话框 - 对应 Tkinter 版 filter_data 的两种模式：按值筛选 / 按条件筛选。
"""

import pandas as pd

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QPushButton, QListWidget, QListWidgetItem, QTabWidget, QWidget,
    QCheckBox, QDialogButtonBox, QMessageBox,
)

from .filter_engine import CONDITIONS

MAX_UNIQUE_VALUES = 500


class FilterDialog(QDialog):
    """返回筛选条件 dict（self.result），或 None 表示取消。"""

    def __init__(self, parent, df: pd.DataFrame, preset_col=None, edit_filter=None):
        super().__init__(parent)
        self.setWindowTitle("筛选")
        self.resize(420, 520)
        self._df = df
        self.result = None

        layout = QVBoxLayout(self)

        # 列选择
        col_row = QHBoxLayout()
        col_row.addWidget(QLabel("列:"))
        self.col_combo = QComboBox()
        self.col_combo.addItems([str(c) for c in df.columns])
        if preset_col and str(preset_col) in [str(c) for c in df.columns]:
            self.col_combo.setCurrentText(str(preset_col))
        col_row.addWidget(self.col_combo, 1)
        layout.addLayout(col_row)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # --- 按值筛选 ---
        value_tab = QWidget()
        v_layout = QVBoxLayout(value_tab)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索值...")
        v_layout.addWidget(self.search_edit)
        self.select_all_cb = QCheckBox("全选")
        self.select_all_cb.setChecked(True)
        v_layout.addWidget(self.select_all_cb)
        self.value_list = QListWidget()
        v_layout.addWidget(self.value_list, 1)
        self.value_hint = QLabel("")
        v_layout.addWidget(self.value_hint)
        self.tabs.addTab(value_tab, "按值筛选")

        # --- 按条件筛选 ---
        cond_tab = QWidget()
        c_layout = QVBoxLayout(cond_tab)
        cond_row = QHBoxLayout()
        cond_row.addWidget(QLabel("条件:"))
        self.cond_combo = QComboBox()
        self.cond_combo.addItems(CONDITIONS)
        cond_row.addWidget(self.cond_combo, 1)
        c_layout.addLayout(cond_row)
        val_row = QHBoxLayout()
        val_row.addWidget(QLabel("值:"))
        self.value_edit = QLineEdit()
        val_row.addWidget(self.value_edit, 1)
        c_layout.addLayout(val_row)
        c_layout.addStretch(1)
        self.tabs.addTab(cond_tab, "按条件筛选")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # 信号
        self.col_combo.currentTextChanged.connect(self._reload_values)
        self.search_edit.textChanged.connect(self._apply_search)
        self.select_all_cb.toggled.connect(self._toggle_all)
        self.cond_combo.currentTextChanged.connect(self._on_condition_change)

        self._reload_values()

        # 编辑已有筛选时预填
        if edit_filter:
            self._prefill(edit_filter)

    # ---------- 值列表 ----------

    def _reload_values(self):
        col = self.col_combo.currentText()
        self.value_list.clear()
        if col not in [str(c) for c in self._df.columns]:
            return
        series = self._df[col].astype(str)
        uniques = series.dropna().unique()
        truncated = len(uniques) > MAX_UNIQUE_VALUES
        for v in sorted(uniques)[:MAX_UNIQUE_VALUES]:
            item = QListWidgetItem(v)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.value_list.addItem(item)
        self.value_hint.setText(
            f"共 {len(uniques)} 个唯一值" + ("（仅显示前 500 个）" if truncated else ""))

    def _apply_search(self, text):
        text = text.lower()
        for i in range(self.value_list.count()):
            item = self.value_list.item(i)
            item.setHidden(text not in item.text().lower())

    def _toggle_all(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.value_list.count()):
            item = self.value_list.item(i)
            if not item.isHidden():
                item.setCheckState(state)

    def _on_condition_change(self, cond):
        self.value_edit.setEnabled(cond not in ("为空", "不为空"))

    def _prefill(self, f):
        if f["condition"] == "值在列表中":
            self.tabs.setCurrentIndex(0)
            values = set(f["value"])
            for i in range(self.value_list.count()):
                item = self.value_list.item(i)
                item.setCheckState(
                    Qt.CheckState.Checked if item.text() in values
                    else Qt.CheckState.Unchecked)
        else:
            self.tabs.setCurrentIndex(1)
            self.cond_combo.setCurrentText(f["condition"])
            if f["condition"] not in ("为空", "不为空"):
                self.value_edit.setText(str(f["value"]))

    # ---------- 确定 ----------

    def _on_ok(self):
        col = self.col_combo.currentText()
        if self.tabs.currentIndex() == 0:
            checked = [self.value_list.item(i).text()
                       for i in range(self.value_list.count())
                       if self.value_list.item(i).checkState() == Qt.CheckState.Checked]
            if not checked:
                QMessageBox.warning(self, "筛选", "请至少勾选一个值")
                return
            if len(checked) == self.value_list.count():
                # 全选等于没筛选
                self.reject()
                return
            self.result = {"col": col, "condition": "值在列表中", "value": checked}
        else:
            cond = self.cond_combo.currentText()
            value = self.value_edit.text()
            if cond not in ("为空", "不为空") and value == "":
                QMessageBox.warning(self, "筛选", "请输入筛选值")
                return
            if cond in ("大于", "小于"):
                try:
                    float(value)
                except ValueError:
                    QMessageBox.warning(self, "筛选", "大于/小于 条件需要数值")
                    return
            self.result = {"col": col, "condition": cond, "value": value}
        self.accept()
