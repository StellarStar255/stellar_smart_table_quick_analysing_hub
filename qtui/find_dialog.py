# -*- coding: utf-8 -*-
"""
查找替换对话框（非模态）。
"""

import pandas as pd

from PyQt6.QtWidgets import (
    QDialog, QGridLayout, QLabel, QLineEdit, QPushButton, QCheckBox,
    QMessageBox,
)


class FindReplaceDialog(QDialog):
    """在表格中查找/替换。依赖宿主窗口提供 model 和 jump_to_cell。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("查找替换")
        self._host = parent
        self._matches = []
        self._pos = -1

        layout = QGridLayout(self)
        layout.addWidget(QLabel("查找:"), 0, 0)
        self.find_edit = QLineEdit()
        layout.addWidget(self.find_edit, 0, 1, 1, 3)
        layout.addWidget(QLabel("替换为:"), 1, 0)
        self.replace_edit = QLineEdit()
        layout.addWidget(self.replace_edit, 1, 1, 1, 3)

        self.case_cb = QCheckBox("区分大小写")
        layout.addWidget(self.case_cb, 2, 1)

        find_btn = QPushButton("查找下一个")
        find_btn.clicked.connect(self.find_next)
        layout.addWidget(find_btn, 3, 1)
        replace_btn = QPushButton("替换")
        replace_btn.clicked.connect(self.replace_current)
        layout.addWidget(replace_btn, 3, 2)
        replace_all_btn = QPushButton("全部替换")
        replace_all_btn.clicked.connect(self.replace_all)
        layout.addWidget(replace_all_btn, 3, 3)

        self.status = QLabel("")
        layout.addWidget(self.status, 4, 0, 1, 4)

        self.find_edit.returnPressed.connect(self.find_next)
        self.find_edit.textChanged.connect(self._invalidate)

    def _invalidate(self):
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
        for col_idx, col in enumerate(df.columns):
            series = df[col].astype(str)
            if not case:
                series = series.str.lower()
            for row_idx in series[series.str.contains(needle, regex=False, na=False)].index:
                matches.append((int(row_idx), col_idx))
        matches.sort()
        return matches

    def find_next(self):
        if not self._matches:
            self._matches = self._search()
            self._pos = -1
        if not self._matches:
            self.status.setText("未找到匹配项")
            return
        self._pos = (self._pos + 1) % len(self._matches)
        row, col = self._matches[self._pos]
        self._host.jump_to_cell(row, col)
        self.status.setText(f"第 {self._pos + 1} / {len(self._matches)} 个匹配")

    def replace_current(self):
        if self._pos < 0 or not self._matches:
            self.find_next()
            return
        row, col = self._matches[self._pos]
        model = self._host.model
        old = str(model.df.iat[row, col])
        find_text = self.find_edit.text()
        if self.case_cb.isChecked():
            new = old.replace(find_text, self.replace_edit.text())
        else:
            import re
            new = re.sub(re.escape(find_text), self.replace_edit.text().replace("\\", "\\\\"),
                         old, flags=re.IGNORECASE)
        model.setData(model.index(row, col), new)
        self._invalidate()
        self.find_next()

    def replace_all(self):
        matches = self._search()
        if not matches:
            self.status.setText("未找到匹配项")
            return
        model = self._host.model
        find_text = self.find_edit.text()
        replace_text = self.replace_edit.text()
        count = 0
        for row, col in matches:
            old = str(model.df.iat[row, col])
            if self.case_cb.isChecked():
                new = old.replace(find_text, replace_text)
            else:
                import re
                new = re.sub(re.escape(find_text), replace_text.replace("\\", "\\\\"),
                             old, flags=re.IGNORECASE)
            if model.setData(model.index(row, col), new):
                count += 1
        self._invalidate()
        self.status.setText(f"已替换 {count} 处")
        QMessageBox.information(self, "替换", f"已替换 {count} 处")
