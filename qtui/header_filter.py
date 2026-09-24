# -*- coding: utf-8 -*-
"""
Excel 式列筛选：列头下拉箭头 + 值勾选弹层。

FilterHeaderView   字母表头，记录哪些列已筛选；箭头画在第 1 行（列名行）
                   单元格右侧（已筛选画成实心漏斗），由表格视图绘制/响应点击。
                   选中整列后按住字母列头拖动可移动列（Excel 式）。
ColumnFilterPopup  无边框弹层：升序/降序、搜索、带计数的值勾选列表、
                   全选/反选、清除筛选。结果放在 result / sort_ascending。
"""

from PyQt6.QtCore import (
    Qt, QEvent, QEventLoop, QModelIndex, QPoint, QRect, QTimer, pyqtSignal,
)
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygon
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from qtui.i18n import tr

ARROW_BOX = 14          # 箭头点击区边长（像素）
MIN_SECTION_FOR_ARROW = 28
ACCENT = "#4a9edb"
MAX_VALUES = 2000       # 弹层里最多列出的去重值个数
BLANK_LABEL = "(空白)"   # 空值在列表里的显示名，内部值是空串
RESIZE_MARGIN = 4       # 离列边界这么近算拖列宽，不算拖列
AUTOSCROLL_MARGIN = 24  # 拖列时鼠标离表头左右边缘这么近就自动滚动


def arrow_rect_in(rect):
    """单元格矩形右侧的箭头点击区。"""
    box = min(ARROW_BOX, rect.height() - 2)
    return QRect(rect.right() - box - 2,
                 rect.top() + (rect.height() - box) // 2, box, box)


def _triangle(box):
    cx = box.center().x()
    cy = box.center().y()
    half = max(3, box.width() // 4)
    return QPolygon([QPoint(cx - half, cy - half // 2),
                     QPoint(cx + half, cy - half // 2),
                     QPoint(cx, cy + half)])


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


def paint_arrow(painter, box, filtered, color):
    """在 box 里画下拉三角（已筛选时画实心漏斗）。"""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(color))
    painter.setBrush(color)
    painter.drawPolygon(_funnel(box) if filtered else _triangle(box))
    painter.restore()


class FilterHeaderView(QHeaderView):
    """横向字母表头。

    只负责记录哪些列已筛选；箭头本身画在表格第 1 行（列名行）的单元格里，
    由表格视图绘制与响应点击——箭头挨着列名比挂在字母行上更自然。
    """

    # 拖动列头移动列：(被移动的列号列表, 目标间隙)。间隙 g 指原第 g 列之前，
    # g == 列数表示移到最后
    columnsMoveRequested = pyqtSignal(list, int)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._filtered_cols = set()
        self.setSectionsClickable(True)
        # 拖列状态：按下已选中列时记录，移动超过阈值才算开始拖
        self._drag_cols = None
        self._press_pos = None
        self._dragging = False
        self._drop_gap = None
        self._last_x = 0
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(40)
        self._scroll_timer.timeout.connect(self._auto_scroll)

    @property
    def filtered_columns(self):
        return self._filtered_cols

    def set_filtered_columns(self, cols):
        """更新哪些列已有筛选（列名行画成漏斗）。"""
        cols = set(cols)
        if cols != self._filtered_cols:
            self._filtered_cols = cols
            view = self.parentWidget()
            if view is not None and hasattr(view, "viewport"):
                view.viewport().update()

    # ---------- 拖动列头移动列 ----------

    @property
    def drop_gap(self):
        """拖列中的目标间隙（供表格视图画插入线）；未在拖动时为 None。"""
        return self._drop_gap if self._dragging else None

    def gap_x(self, gap):
        """间隙 gap 的视口 x 坐标。"""
        count = self.count()
        if count == 0:
            return 0
        if gap < count:
            return self.sectionViewportPosition(gap)
        last = count - 1
        return self.sectionViewportPosition(last) + self.sectionSize(last)

    def _selection_model(self):
        view = self.parentWidget()
        sm = view.selectionModel() if hasattr(view, "selectionModel") else None
        return sm if sm is not None and self.model() is not None else None

    def _selected_block(self, col):
        """col 若是整列选中，返回它所在的连续整列选中块；否则 None。"""
        sm = self._selection_model()
        if sm is None or not sm.isColumnSelected(col, QModelIndex()):
            return None
        lo = hi = col
        while lo > 0 and sm.isColumnSelected(lo - 1, QModelIndex()):
            lo -= 1
        while hi < self.count() - 1 and sm.isColumnSelected(hi + 1, QModelIndex()):
            hi += 1
        return list(range(lo, hi + 1))

    def _near_resize_handle(self, x, col):
        left = self.sectionViewportPosition(col)
        right = left + self.sectionSize(col)
        return x - left < RESIZE_MARGIN or right - x < RESIZE_MARGIN

    def _draggable_block_at(self, pos):
        col = self.logicalIndexAt(pos)
        if col < 0 or self._near_resize_handle(pos.x(), col):
            return None
        return self._selected_block(col)

    def _gap_at(self, x):
        count = self.count()
        col = self.logicalIndexAt(QPoint(x, 0))
        if col < 0:
            return 0 if x < 0 else count
        mid = self.sectionViewportPosition(col) + self.sectionSize(col) / 2
        return col if x < mid else col + 1

    def _update_drop(self, x):
        self._last_x = x
        gap = self._gap_at(x)
        if gap != self._drop_gap:
            self._drop_gap = gap
            self._repaint_all()

    def _repaint_all(self):
        self.viewport().update()
        view = self.parentWidget()
        if view is not None and hasattr(view, "viewport"):
            view.viewport().update()

    def _auto_scroll(self):
        """拖到表头左右边缘时持续横向滚动，好把列拖到屏幕外的位置。"""
        view = self.parentWidget()
        bar = view.horizontalScrollBar() if hasattr(view, "horizontalScrollBar") else None
        if not self._dragging or bar is None:
            self._scroll_timer.stop()
            return
        width = self.viewport().width()
        if self._last_x < AUTOSCROLL_MARGIN:
            step = -max(8, AUTOSCROLL_MARGIN - self._last_x)
        elif self._last_x > width - AUTOSCROLL_MARGIN:
            step = max(8, self._last_x - (width - AUTOSCROLL_MARGIN))
        else:
            return
        bar.setValue(bar.value() + step * 2)
        self._update_drop(self._last_x)

    def _end_drag(self):
        self._drag_cols = None
        self._press_pos = None
        self._dragging = False
        self._drop_gap = None
        self._scroll_timer.stop()
        self.unsetCursor()
        self._repaint_all()

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            block = self._draggable_block_at(event.position().toPoint())
            if block is not None:
                # 先不交给基类：若没拖动，松开时再补一次普通单击
                self._drag_cols = block
                self._press_pos = event.position().toPoint()
                self._dragging = False
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._drag_cols is not None:
            if not self._dragging:
                if ((pos - self._press_pos).manhattanLength()
                        < QApplication.startDragDistance()):
                    return
                self._dragging = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self._scroll_timer.start()
            self._update_drop(pos.x())
            event.accept()
            return
        super().mouseMoveEvent(event)
        # 悬停在已选中的整列上提示可拖动（基类已处理列宽边界的光标）
        if (event.buttons() == Qt.MouseButton.NoButton
                and self._draggable_block_at(pos) is not None):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif self.cursor().shape() == Qt.CursorShape.OpenHandCursor:
            self.unsetCursor()

    def mouseReleaseEvent(self, event):
        if self._drag_cols is None:
            super().mouseReleaseEvent(event)
            return
        cols, gap, dragged = self._drag_cols, self._drop_gap, self._dragging
        self._end_drag()
        if dragged:
            if gap is not None and not (cols[0] <= gap <= cols[-1] + 1):
                self.columnsMoveRequested.emit(cols, gap)
        else:
            # 没拖动 = 普通单击：照常选中这一列（收起多列选区）
            super().mousePressEvent(event)
            super().mouseReleaseEvent(event)
        event.accept()

    def paintEvent(self, event):
        super().paintEvent(event)
        gap = self.drop_gap
        if gap is None:
            return
        painter = QPainter(self.viewport())
        x = self.gap_x(gap)
        painter.fillRect(QRect(x - 1, 0, 3, self.viewport().height()), QColor(ACCENT))


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
        self._loop = None
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
            item = QListWidgetItem("{} ({})".format(text or tr(BLANK_LABEL), count))
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
            value = item.data(Qt.ItemDataRole.UserRole) or tr(BLANK_LABEL)
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
        """在给定屏幕坐标下方弹出并等待结果；点弹层外面 = 取消。

        没有用 exec()：模态窗口会让外面的点击压根送不到事件过滤器，
        就实现不了"点空白处自动关闭"。改成非模态 + 自己跑一个事件循环，
        效果等价（调用方仍然是同步等结果），但外部点击看得见。
        """
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
        self.show()
        self.raise_()
        self.activateWindow()
        self.search_edit.setFocus()

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._loop = QEventLoop()
        try:
            self._loop.exec()
        finally:
            self._loop = None
            if app is not None:
                app.removeEventFilter(self)
        # 注意：实例属性 self.result 盖住了 QDialog.result()，这里返回的是
        # 我们自己的结果（None / 'clear' / 'advanced' / 勾选值列表）
        return self.result

    def eventFilter(self, obj, event):
        """点在弹层外面就关掉（并吞掉这一下，免得顺手改了表格选区）。"""
        if event.type() in (QEvent.Type.MouseButtonPress,
                            QEvent.Type.NonClientAreaMouseButtonPress):
            try:
                pos = event.globalPosition().toPoint()
            except AttributeError:
                pos = event.globalPos()
            if self.isVisible() and not self.frameGeometry().contains(pos):
                self.reject()
                return True
        return super().eventFilter(obj, event)

    def done(self, code):
        super().done(code)
        if self._loop is not None:
            self._loop.quit()      # 结束 popup_at 里的事件循环

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
