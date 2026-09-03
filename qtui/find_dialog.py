# -*- coding: utf-8 -*-
"""
查找替换对话框（非模态）。
"""

import bisect
import re

import numpy as np

from PyQt6.QtWidgets import (
    QDialog, QGridLayout, QLabel, QLineEdit, QPushButton, QCheckBox,
    QMessageBox,
)

from qtui.i18n import tr


class FindReplaceDialog(QDialog):
    """在表格中查找/替换。依赖宿主窗口提供 model 和 jump_to_cell。

    对话框非模态且被复用：用户可能在两次点击之间编辑/删行/换文件，
    因此匹配列表会随模型结构变化自动失效，并在使用前做越界检查。
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(tr("查找替换"))
        self._host = parent
        self._matches = []
        self._pos = -1

        layout = QGridLayout(self)
        layout.addWidget(QLabel(tr("查找:")), 0, 0)
        self.find_edit = QLineEdit()
        layout.addWidget(self.find_edit, 0, 1, 1, 3)
        layout.addWidget(QLabel(tr("替换为:")), 1, 0)
        self.replace_edit = QLineEdit()
        layout.addWidget(self.replace_edit, 1, 1, 1, 3)

        self.case_cb = QCheckBox(tr("区分大小写"))
        layout.addWidget(self.case_cb, 2, 1)

        find_btn = QPushButton(tr("查找下一个"))
        find_btn.clicked.connect(self.find_next)
        layout.addWidget(find_btn, 3, 1)
        replace_btn = QPushButton(tr("替换"))
        replace_btn.clicked.connect(self.replace_current)
        layout.addWidget(replace_btn, 3, 2)
        replace_all_btn = QPushButton(tr("全部替换"))
        replace_all_btn.clicked.connect(self.replace_all)
        layout.addWidget(replace_all_btn, 3, 3)

        self.status = QLabel("")
        layout.addWidget(self.status, 4, 0, 1, 4)

        self.find_edit.returnPressed.connect(self.find_next)
        self.find_edit.textChanged.connect(self._invalidate)
        self.case_cb.toggled.connect(self._invalidate)

        # 模型任何结构/内容变化都让缓存的匹配位置失效（避免用旧坐标访问新数据）
        model = getattr(parent, "model", None)
        if model is not None:
            for sig in (model.modelReset, model.layoutChanged,
                        model.rowsInserted, model.rowsRemoved,
                        model.columnsInserted, model.columnsRemoved,
                        model.dataChanged):
                sig.connect(self._invalidate)

    def _invalidate(self, *_args):
        self._matches = []
        self._pos = -1

    def _search(self):
        text = self.find_edit.text()
        if not text:
            return []
        df = self._host.model.df
        matches = []
        case = self.case_cb.isChecked()
        needle = text if case else text.lower()
        for col_idx in range(len(df.columns)):
            series = df.iloc[:, col_idx].astype(str)
            if not case:
                series = series.str.lower()
            mask = series.str.contains(needle, regex=False, na=False).to_numpy()
            # 用位置而非索引标签，确保与 iat/model.index 的 0 基行号一致
            for row_pos in np.flatnonzero(mask):
                matches.append((int(row_pos), col_idx))
        matches.sort()
        return matches

    def _in_bounds(self, row, col):
        df = self._host.model.df
        return 0 <= row < len(df) and 0 <= col < len(df.columns)

    def _replace_in(self, old):
        find_text = self.find_edit.text()
        replace_text = self.replace_edit.text()
        if self.case_cb.isChecked():
            return old.replace(find_text, replace_text)
        return re.sub(re.escape(find_text), lambda _m: replace_text,
                      old, flags=re.IGNORECASE)

    def find_next(self):
        if not self._matches:
            self._matches = self._search()
            self._pos = -1
        if not self._matches:
            self.status.setText(tr("未找到匹配项"))
            return
        self._pos = (self._pos + 1) % len(self._matches)
        row, col = self._matches[self._pos]
        if not self._in_bounds(row, col):
            self._invalidate()
            self.find_next()
            return
        self._host.jump_to_cell(row, col)
        self.status.setText(
            tr("第 {} / {} 个匹配").format(self._pos + 1, len(self._matches)))

    def replace_current(self):
        if self._pos < 0 or not self._matches:
            self.find_next()
            return
        row, col = self._matches[self._pos]
        if not self._in_bounds(row, col):
            self._invalidate()
            self.find_next()
            return
        model = self._host.model
        old = str(model.df.iat[row, col])
        new = self._replace_in(old)
        model.setData(model.index(row + model.HEADER_ROWS, col), new)  # 视图行偏移表头行
        # 重新搜索后从"刚替换位置之后"继续，而不是跳回第一个匹配
        self._matches = self._search()
        self._pos = bisect.bisect_right(self._matches, (row, col)) - 1
        self.find_next()

    def replace_all(self):
        matches = self._search()
        if not matches:
            self.status.setText(tr("未找到匹配项"))
            return
        model = self._host.model
        count = 0
        for row, col in matches:
            if not self._in_bounds(row, col):
                continue
            old = str(model.df.iat[row, col])
            new = self._replace_in(old)
            if model.setData(model.index(row + model.HEADER_ROWS, col), new):  # 视图行偏移表头行
                count += 1
        self._invalidate()
        self.status.setText(tr("已替换 {} 处").format(count))
        QMessageBox.information(self, tr("替换"), tr("已替换 {} 处").format(count))
