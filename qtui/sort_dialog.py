# -*- coding: utf-8 -*-
"""多列排序对话框：若干行"列 + 升/降序"，从上到下为优先级。"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QGridLayout, QLabel, QComboBox,
    QPushButton, QDialogButtonBox, QWidget,
)

from qtui.i18n import tr


class SortDialog(QDialog):
    """收集 [(列号, 是否升序), ...]；至少一行，最多与列数相同。"""

    def __init__(self, columns, parent=None, preset_col=0):
        super().__init__(parent)
        self.setWindowTitle(tr("排序"))
        self.setMinimumWidth(420)
        self._columns = [str(c) for c in columns]
        self._rows = []          # [(容器, 列下拉, 方向下拉)]

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("按以下条件排序（从上到下为优先级）：")))
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._grid_host)

        self._add_btn = QPushButton(tr("添加排序条件"))
        self._add_btn.clicked.connect(lambda: self.add_key())
        layout.addWidget(self._add_btn)
        layout.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        first = preset_col if 0 <= preset_col < len(self._columns) else 0
        self.add_key(first, True)

    # ---- 行管理 ----

    def add_key(self, col=None, ascending=True):
        if len(self._rows) >= len(self._columns):
            return
        if col is None:
            used = {c.currentIndex() for _w, c, _o in self._rows}
            col = next((i for i in range(len(self._columns)) if i not in used), 0)
        row = len(self._rows)
        label = QLabel(tr("排序依据") if row == 0 else tr("然后按"))
        col_box = QComboBox()
        col_box.addItems(self._columns)
        col_box.setCurrentIndex(col)
        order_box = QComboBox()
        order_box.addItems([tr("升序"), tr("降序")])
        order_box.setCurrentIndex(0 if ascending else 1)
        remove_btn = QPushButton("−")
        remove_btn.setFixedWidth(28)
        remove_btn.setToolTip(tr("删除此条件"))
        holder = (label, col_box, order_box, remove_btn)
        remove_btn.clicked.connect(lambda _=False, h=holder: self._remove(h))
        self._grid.addWidget(label, row, 0)
        self._grid.addWidget(col_box, row, 1)
        self._grid.addWidget(order_box, row, 2)
        self._grid.addWidget(remove_btn, row, 3)
        self._rows.append((holder, col_box, order_box))
        self._refresh()

    def _remove(self, holder):
        if len(self._rows) <= 1:
            return
        self._rows = [r for r in self._rows if r[0] is not holder]
        for w in holder:
            self._grid.removeWidget(w)
            w.deleteLater()
        # 重新摆放并刷新标签（第一行始终是"排序依据"）
        for row, (h, _c, _o) in enumerate(self._rows):
            for column, w in enumerate(h):
                self._grid.addWidget(w, row, column)
            h[0].setText(tr("排序依据") if row == 0 else tr("然后按"))
        self._refresh()

    def _refresh(self):
        self._add_btn.setEnabled(len(self._rows) < len(self._columns))
        for h, _c, _o in self._rows:
            h[3].setEnabled(len(self._rows) > 1)

    # ---- 结果 ----

    def keys(self):
        """[(列号, 是否升序), ...]，重复选择的列只保留优先级最高的一次。"""
        seen, out = set(), []
        for _h, col_box, order_box in self._rows:
            col = col_box.currentIndex()
            if col in seen:
                continue
            seen.add(col)
            out.append((col, order_box.currentIndex() == 0))
        return out
