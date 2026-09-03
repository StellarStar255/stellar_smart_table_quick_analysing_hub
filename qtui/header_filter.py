# -*- coding: utf-8 -*-
"""
Excel 式列筛选：列头下拉箭头 + 值勾选弹层。

FilterHeaderView   在每个列头右侧画一个下拉箭头（该列已筛选时画成实心漏斗），
                   点箭头发 filterClicked(列号)；点列头其它位置行为不变。
ColumnFilterPopup  无边框弹层：升序/降序、搜索、带计数的值勾选列表、
                   全选/反选、清除筛选。结果放在 result / sort_ascending。
"""

from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygon
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from qtui.i18n import tr

ARROW_BOX = 14          # 箭头点击区边长（像素）
MIN_SECTION_FOR_ARROW = 28
ACCENT = "#4a9edb"
MAX_VALUES = 2000       # 弹层里最多列出的去重值个数
BLANK_LABEL = "(空白)"   # 空值在列表里的显示名，内部值是空串


class FilterHeaderView(QHeaderView):
    """列头带筛选箭头的横向表头。"""

    filterClicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._filtered_cols = set()
        self.setSectionsClickable(True)
        self.setMouseTracking(True)

    def set_filtered_columns(self, cols):
        """更新哪些列已有筛选（画成漏斗）。"""
        cols = set(cols)
        if cols != self._filtered_cols:
            self._filtered_cols = cols
            self.viewport().update()

    # ---------- 箭头位置 ----------

    @staticmethod
    def _arrow_rect_in(rect):
        box = min(ARROW_BOX, rect.height() - 2)
        return QRect(rect.right() - box - 2,
                     rect.top() + (rect.height() - box) // 2, box, box)

    def arrow_rect_at(self, logical_index):
        """视口坐标下该列箭头的点击区。"""
        x = self.sectionViewportPosition(logical_index)
        w = self.sectionSize(logical_index)
        return self._arrow_rect_in(QRect(x, 0, w, self.height()))

    def has_arrow(self, logical_index):
        return self.sectionSize(logical_index) >= MIN_SECTION_FOR_ARROW

    # ---------- 绘制 ----------

    def paintSection(self, painter, rect, logical_index):
        super().paintSection(painter, rect, logical_index)
        if not self.has_arrow(logical_index):
            return
        filtered = logical_index in self._filtered_cols
        box = self._arrow_rect_in(rect)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(ACCENT) if filtered else QColor(150, 150, 150)
        painter.setPen(QPen(color))
        painter.setBrush(color)
        if filtered:
            painter.drawPolygon(self._funnel(box))
        else:
            painter.drawPolygon(self._triangle(box))
        painter.restore()

    @staticmethod
    def _triangle(box):
        cx = box.center().x()
        cy = box.center().y()
        half = max(3, box.width() // 4)
        return QPolygon([QPoint(cx - half, cy - half // 2),
                         QPoint(cx + half, cy - half // 2),
                         QPoint(cx, cy + half)])

    @staticmethod
    def _funnel(box):
        """漏斗：上宽下窄的梯形 + 短柄。"""
        cx = box.center().x()
        top = box.top() + box.height() // 4
        bottom = box.bottom() - box.height() // 4
        half = max(3, box.width() // 3)
        neck = max(1, half // 3)
        mid = (top + bottom) // 2
        return QPolygon([QPoint(cx - half, top), QPoint(cx + half, top),
                         QPoint(cx + neck, mid), QPoint(cx + neck, bottom),
                         QPoint(cx - neck, bottom), QPoint(cx - neck, mid)])

    # ---------- 交互 ----------

    def mousePressEvent(self, event):
        idx = self.logicalIndexAt(event.pos())
        if (event.button() == Qt.MouseButton.LeftButton and idx >= 0
                and self.has_arrow(idx)
                and self.arrow_rect_at(idx).contains(event.pos())):
            self.filterClicked.emit(idx)
            return          # 不走默认的点列头行为（选列/排序）
        super().mousePressEvent(event)


class ColumnFilterPopup(QDialog):
    """列头筛选弹层。

    result: None=取消；'clear'=清除该列筛选；'advanced'=改用条件筛选对话框；
            list=勾选的显示文本。
    sort_ascending: None=没点排序；True/False=升序/降序（此时 result 为 None）。
    """

    def __init__(self, parent, colname, value_counts, checked=None,
                 has_filter=False):
        super().__init__(parent, Qt.WindowType.Dialog
                         | Qt.WindowType.FramelessWindowHint)
        self.result = None
        self.sort_ascending = None
        self._target_pos = None
        self._truncated = len(value_counts) > MAX_VALUES
        self._values = value_counts[:MAX_VALUES]

        self.setSizeGripEnabled(False)
        # Popup 没有标题栏，自己画个边框，否则和表格糊成一片
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("ColumnFilterPopup { border: 1px solid #808080;"
                           " background: palette(window); }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel(str(colname))
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        sort_row = QHBoxLayout()
        asc = QPushButton(tr("升序"))
        desc = QPushButton(tr("降序"))
        asc.clicked.connect(lambda: self._sort(True))
        desc.clicked.connect(lambda: self._sort(False))
        sort_row.addWidget(asc)
        sort_row.addWidget(desc)
        layout.addLayout(sort_row)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("搜索值..."))
        self.search_edit.setClearButtonEnabled(True)
        layout.addWidget(self.search_edit)

        head_row = QHBoxLayout()
        self.select_all_cb = QCheckBox(tr("全选 ({})").format(len(self._values)))
        head_row.addWidget(self.select_all_cb)
        invert = QPushButton(tr("反选"))
        invert.setFlat(True)
        invert.clicked.connect(self._invert)
        head_row.addWidget(invert)
        head_row.addStretch(1)
        more = QPushButton(tr("更多条件..."))
        more.setFlat(True)
        more.clicked.connect(self._advanced)
        head_row.addWidget(more)
        layout.addLayout(head_row)

        self.value_list = QListWidget()
        self.value_list.setUniformItemSizes(True)
        layout.addWidget(self.value_list, 1)
        for text, count in self._values:
            item = QListWidgetItem("{} ({})".format(text or BLANK_LABEL, count))
            item.setData(Qt.ItemDataRole.UserRole, text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked
                               if checked is None or text in checked
                               else Qt.CheckState.Unchecked)
            self.value_list.addItem(item)
        self._initial_states = [self.value_list.item(i).checkState()
                                for i in range(self.value_list.count())]

        if self._truncated:
            hint = QLabel(tr("值太多，仅列出前 {} 个").format(MAX_VALUES))
            hint.setStyleSheet("color: gray; font-size: 11px;")
            layout.addWidget(hint)

        btn_row = QHBoxLayout()
        self.clear_btn = QPushButton(tr("清除筛选"))
        self.clear_btn.setEnabled(has_filter)
        self.clear_btn.clicked.connect(self._clear)
        btn_row.addWidget(self.clear_btn)
        btn_row.addStretch(1)
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        ok = QPushButton(tr("确定"))
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        btn_row.addWidget(ok)
        layout.addLayout(btn_row)

        self.search_edit.textChanged.connect(self._apply_search)
        self.select_all_cb.toggled.connect(self._toggle_all)
        self.value_list.itemChanged.connect(self._sync_select_all)
        self._sync_select_all()
        self.resize(280, 420)

    # ---------- 勾选状态 ----------

    def _visible_items(self):
        return [self.value_list.item(i) for i in range(self.value_list.count())
                if not self.value_list.item(i).isHidden()]

    def _apply_search(self, text):
        """搜索时只勾选命中的值（Excel 行为）；清空搜索恢复打开时的勾选。"""
        needle = text.strip().lower()
        self.value_list.blockSignals(True)
        for i in range(self.value_list.count()):
            item = self.value_list.item(i)
            value = item.data(Qt.ItemDataRole.UserRole) or BLANK_LABEL
            hit = needle in str(value).lower()
            item.setHidden(bool(needle) and not hit)
            if needle:
                item.setCheckState(Qt.CheckState.Checked if hit
                                   else Qt.CheckState.Unchecked)
            else:
                item.setCheckState(self._initial_states[i])
        self.value_list.blockSignals(False)
        self._sync_select_all()

    def _toggle_all(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.value_list.blockSignals(True)
        for item in self._visible_items():
            item.setCheckState(state)
        self.value_list.blockSignals(False)

    def _invert(self):
        self.value_list.blockSignals(True)
        for item in self._visible_items():
            item.setCheckState(
                Qt.CheckState.Unchecked
                if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked)
        self.value_list.blockSignals(False)
        self._sync_select_all()

    def _sync_select_all(self, *_):
        """全选框跟随列表：全勾 = 勾上，全不勾 = 取消，其余 = 半选。"""
        visible = self._visible_items()
        checked = sum(1 for i in visible
                      if i.checkState() == Qt.CheckState.Checked)
        self.select_all_cb.blockSignals(True)
        self.select_all_cb.setTristate(True)
        if not visible or checked == 0:
            self.select_all_cb.setCheckState(Qt.CheckState.Unchecked)
        elif checked == len(visible):
            self.select_all_cb.setCheckState(Qt.CheckState.Checked)
        else:
            self.select_all_cb.setCheckState(Qt.CheckState.PartiallyChecked)
        self.select_all_cb.setText(tr("全选 ({})").format(len(visible)))
        self.select_all_cb.blockSignals(False)

    def checked_values(self):
        return [self.value_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.value_list.count())
                if self.value_list.item(i).checkState() == Qt.CheckState.Checked]

    # ---------- 出口 ----------

    def _sort(self, ascending):
        self.sort_ascending = ascending
        self.accept()

    def _clear(self):
        self.result = "clear"
        self.accept()

    def _advanced(self):
        """转去"按条件筛选"（大于/包含/为空…）。"""
        self.result = "advanced"
        self.accept()

    def _accept(self):
        values = self.checked_values()
        if not values:
            # 一个都不勾 = 结果必为空表，按"清除筛选"处理更符合直觉
            self.result = "clear" if self.clear_btn.isEnabled() else None
            self.accept()
            return
        if len(values) == self.value_list.count() and not self._truncated:
            self.result = "clear"      # 全选等于没筛选
        else:
            self.result = values
        self.accept()

    def popup_at(self, global_pos):
        """在给定屏幕坐标下方弹出，超出屏幕时向上翻。"""
        # adjustSize 会按"列表想显示全部值"来算高度，值多时能撑到上千像素，
        # 于是超出屏幕后被推到别处——这里把尺寸夹在可控范围内
        self.adjustSize()
        self.resize(min(max(self.width(), 280), 400),
                    min(max(self.height(), 320), 480))
        self._target_pos = self._clamp_to_screen(global_pos)
        # QDialog 默认会把自己居中到父窗口（WA_Moved 未设时），加上部分窗口
        # 管理器还会自作主张摆位——显式设 WA_Moved，并在 show 之后再摆一次
        self.setAttribute(Qt.WidgetAttribute.WA_Moved, True)
        self.move(self._target_pos)
        self.search_edit.setFocus()
        return self.exec()

    def _clamp_to_screen(self, global_pos):
        pos = QPoint(global_pos)
        screen = self.screen()
        if screen is None:
            return pos
        avail = screen.availableGeometry()
        pos.setX(min(pos.x(), avail.right() - self.width() - 4))
        pos.setX(max(pos.x(), avail.left() + 4))
        if pos.y() + self.height() > avail.bottom():
            # 下方放不下就往上翻（贴着箭头上沿），别飘到屏幕中间
            above = global_pos.y() - self.height() - 24
            pos.setY(above if above >= avail.top() + 4
                     else max(avail.top() + 4, avail.bottom() - self.height() - 4))
        return pos

    def showEvent(self, event):
        super().showEvent(event)
        if self._target_pos is not None and self.pos() != self._target_pos:
            self.move(self._target_pos)      # 压过窗口管理器的摆位
