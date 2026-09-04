# -*- coding: utf-8 -*-
"""
图片预览面板 - 主图 + 胶片条布局（macOS 预览风格）。

- 上方固定区域始终大图显示当前行的图片，切换时轻微淡入（无形变）
- 下方横向胶片条显示邻近行的小缩略图（恒定尺寸），选中项蓝框、
  平滑滚动居中；点击跳行、双击打开查看器、右键直接复制图片
- 缩略图与大图都在后台线程加载（QImage 可跨线程），双级缓存
"""

import os
from collections import OrderedDict

from PyQt6.QtCore import (
    Qt, QObject, QRunnable, QThreadPool, QSize, QTimer, pyqtSignal,
    QPropertyAnimation, QEasingCurve,
)
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QScrollArea, QFrame,
    QGraphicsOpacityEffect, QSizePolicy, QMenu,
)

from qtui.i18n import tr
from qtui.image_utils import load_image

STRIP_BEFORE, STRIP_AFTER = 10, 12      # 胶片条向前/向后渲染的行数
THUMB_CACHE_LIMIT = 300
MAIN_CACHE_LIMIT = 24
BASE_STRIP_THUMB = QSize(96, 72)        # 胶片条缩略图基准尺寸（缩放滑块调节）
MAIN_LOAD_CAP = QSize(1600, 1200)       # 大图后台加载的分辨率上限
HIGHLIGHT = "#4a9edb"


class _LoaderSignals(QObject):
    loaded = pyqtSignal(str, QImage)


class _ImageLoader(QRunnable):
    def __init__(self, path, size, signals):
        super().__init__()
        self._path = path
        self._size = size
        self._signals = signals

    def run(self):
        # 解码时直接缩放并应用 EXIF 方向（见 image_utils.load_image）
        img = load_image(self._path, self._size)
        self._signals.loaded.emit(self._path, img)


class _MainImageView(QLabel):
    """主图区域：保存原始 pixmap，随面板尺寸自适应缩放，切换时淡入。"""

    doubleClicked = pyqtSignal(str)
    menuRequested = pyqtSignal(str, object)   # (路径, 全局坐标)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._orig = None
        self.path = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self._effect.setEnabled(False)   # 常驻会让每次重绘走离屏缓冲，拖动缩放时闪烁
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(150)
        self._fade.setStartValue(0.4)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._fade.finished.connect(lambda: self._effect.setEnabled(False))
        # 连续 resize（拖动分隔条/缩放条）期间用快速缩放，停顿后再平滑精缩一次
        self._smooth_timer = QTimer(self)
        self._smooth_timer.setSingleShot(True)
        self._smooth_timer.setInterval(90)
        self._smooth_timer.timeout.connect(lambda: self._rescale(smooth=True))

    def set_image(self, pixmap, path, fade=True):
        self._orig = pixmap
        self.path = path
        if pixmap is None:
            self.setText(tr("(无图片)"))
        else:
            self._rescale()
            if fade:
                self._fade.stop()
                self._effect.setEnabled(True)
                self._fade.start()

    def _rescale(self, smooth=True):
        if self._orig is None:
            return
        mode = (Qt.TransformationMode.SmoothTransformation if smooth
                else Qt.TransformationMode.FastTransformation)
        self.setPixmap(self._orig.scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio, mode))

    def resizeEvent(self, event):
        self._rescale(smooth=False)
        self._smooth_timer.start()
        super().resizeEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.path:
            self.doubleClicked.emit(self.path)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        if self.path:
            self.menuRequested.emit(self.path, event.globalPos())


class _StripCell(QFrame):
    """胶片条单元：恒定尺寸缩略图 + 行号。"""

    clicked = pyqtSignal(int)
    doubleClicked = pyqtSignal(str)
    menuRequested = pyqtSignal(str, object)   # (路径, 全局坐标)

    def __init__(self, row, path, thumb_size, parent=None):
        super().__init__(parent)
        self.row = row
        self.path = path
        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 1)
        layout.setSpacing(1)
        self.img_label = QLabel()
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_label.setFixedSize(thumb_size)
        layout.addWidget(self.img_label)
        self.caption = QLabel(str(row + 2))  # 网格第 1 行是表头，数据行号从 2 起
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setStyleSheet("font-size: 10px; color: gray;")
        layout.addWidget(self.caption)

    def set_focused(self, focused):
        if focused:
            self.setStyleSheet(
                f"_StripCell {{ border: 2px solid {HIGHLIGHT}; border-radius: 4px; }}")
            self.caption.setStyleSheet(
                f"font-size: 10px; font-weight: bold; color: {HIGHLIGHT};")
        else:
            self.setStyleSheet("_StripCell { border: 2px solid transparent; }")
            self.caption.setStyleSheet("font-size: 10px; color: gray;")

    def mousePressEvent(self, event):
        self.clicked.emit(self.row)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.path:
            self.doubleClicked.emit(self.path)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        if self.path:
            # 右键也顺便把当前行切过去，和左键点击一致
            self.clicked.emit(self.row)
            self.menuRequested.emit(self.path, event.globalPos())


class ImagePreviewPanel(QWidget):
    """右侧图片预览面板（主图 + 胶片条）。"""

    rowActivated = pyqtSignal(int)
    openImageRequested = pyqtSignal(str)
    copyImageRequested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._df = None
        self._image_col = None
        self._base_dir = ""
        self._current_row = -1
        self._zoom = 1.0
        self._entries = []   # 胶片条单元（按行号有序）

        self._thumb_cache = OrderedDict()   # 路径 -> 胶片条 QPixmap
        self._main_cache = OrderedDict()    # 路径 -> 大图 QPixmap
        self._thumb_inflight = set()
        self._main_inflight = set()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._thumb_signals = _LoaderSignals()
        self._thumb_signals.loaded.connect(self._on_thumb_loaded)
        self._main_signals = _LoaderSignals()
        self._main_signals.loaded.connect(self._on_main_loaded)

        self._scroll_anim = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(60)
        self._refresh_timer.timeout.connect(self._rebuild_strip)
        # 缩放滑块拖动结束后，对缩略图做一次平滑精缩
        self._zoom_settle_timer = QTimer(self)
        self._zoom_settle_timer.setSingleShot(True)
        self._zoom_settle_timer.setInterval(120)
        self._zoom_settle_timer.timeout.connect(self._settle_zoom)
        # 拖动期间的缩放应用按 ~80ms 节流：滑块每个像素都发 valueChanged，
        # 逐个应用意味着每秒几十次全条重布局，肉眼即闪烁
        self._zoom_pending = False
        self._zoom_throttle = QTimer(self)
        self._zoom_throttle.setSingleShot(True)
        self._zoom_throttle.setInterval(80)
        self._zoom_throttle.timeout.connect(self._flush_pending_zoom)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ---- 主图 ----
        self.main_view = _MainImageView()
        self.main_view.doubleClicked.connect(self.openImageRequested.emit)
        self.main_view.menuRequested.connect(self._show_image_menu)
        layout.addWidget(self.main_view, 1)
        self._main_caption = QLabel("")
        self._main_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._main_caption.setStyleSheet(f"color: {HIGHLIGHT}; font-weight: bold;")
        layout.addWidget(self._main_caption)

        # ---- 胶片条 ----
        self._strip_scroll = QScrollArea()
        self._strip_scroll.setWidgetResizable(True)
        self._strip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._strip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._strip_container = QWidget()
        self._strip_layout = QHBoxLayout(self._strip_container)
        self._strip_layout.setContentsMargins(0, 0, 0, 0)
        self._strip_layout.setSpacing(2)
        self._strip_layout.addStretch(1)
        self._strip_scroll.setWidget(self._strip_container)
        layout.addWidget(self._strip_scroll)
        self._update_strip_height()

        # ---- 缩放（控制胶片条缩略图大小） ----
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel(tr("缩放:")))
        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setRange(50, 200)
        self._zoom_slider.setValue(100)
        self._zoom_slider.valueChanged.connect(self._on_zoom_changed)
        self._zoom_slider.sliderReleased.connect(self._settle_zoom)
        zoom_row.addWidget(self._zoom_slider, 1)
        self._zoom_label = QLabel("100%")
        zoom_row.addWidget(self._zoom_label)
        layout.addLayout(zoom_row)

        self._hint = QLabel(tr("右键列头选择「设为图片列」"))
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._hint)

    # ---------- 外部接口 ----------

    def set_context(self, df, image_col, base_dir):
        self._df = df
        self._image_col = image_col
        self._base_dir = base_dir or ""
        self._hint.setVisible(image_col is None)
        if image_col is None:
            self._clear_strip()
            self.main_view.set_image(None, None, fade=False)
            self.main_view.setText("")
            self._main_caption.setText("")
        else:
            self._rebuild_strip()
            self._update_main_image(fade=False)

    def set_current_row(self, row):
        if row == self._current_row:
            return
        self._current_row = row
        if self._image_col is None:
            return
        self._update_main_image(fade=True)
        rows = [e.row for e in self._entries]
        if rows and rows[0] <= row <= rows[-1]:
            self._refresh_timer.stop()
            self._incremental_strip_update()
        else:
            self._refresh_timer.start()

    # ---------- 尺寸 ----------

    def _thumb_size(self):
        return QSize(int(BASE_STRIP_THUMB.width() * self._zoom),
                     int(BASE_STRIP_THUMB.height() * self._zoom))

    def _update_strip_height(self):
        # 缩略图 + 行号 + 边距
        self._strip_scroll.setFixedHeight(self._thumb_size().height() + 30)

    def _on_zoom_changed(self, value):
        self._zoom = value / 100.0
        self._zoom_label.setText(f"{value}%")
        if self._zoom_slider.isSliderDown():
            # 拖动中：立即应用一次，随后 80ms 窗口内的变化合并到定时器统一应用
            if self._zoom_throttle.isActive():
                self._zoom_pending = True
            else:
                self._apply_zoom(smooth=False)
                self._zoom_throttle.start()
            self._zoom_settle_timer.start()
        else:
            self._apply_zoom(smooth=True)

    def _flush_pending_zoom(self):
        if self._zoom_pending:
            self._zoom_pending = False
            self._apply_zoom(smooth=False)

    def _apply_zoom(self, smooth):
        # 缩略图缓存按 2 倍尺寸保存，缩放只需重设现有单元的尺寸和 pixmap，
        # 不清缓存、不重建胶片条。批量更新期间冻结重绘，避免逐格闪烁。
        self._strip_scroll.setUpdatesEnabled(False)
        try:
            self._update_strip_height()
            for cell in self._entries:
                self._set_cell_pixmap(cell, smooth=smooth)
        finally:
            self._strip_scroll.setUpdatesEnabled(True)

    # ---------- 路径 ----------

    def _resolve_path(self, raw):
        raw = str(raw).strip()
        if not raw or raw.lower() in ("nan", "none"):
            return None
        if os.path.isabs(raw):
            return raw if os.path.exists(raw) else None
        for base in (self._base_dir, os.getcwd()):
            if base:
                p = os.path.normpath(os.path.join(base, raw))
                if os.path.exists(p):
                    return p
        return None

    def neighbor_with_image(self, step):
        """从当前行往 step 方向（-1 上 / +1 下）找下一张有图的行。

        返回 (行号, 路径)；到头了或没有图片列返回 None。查看器 ←/→ 翻页用。
        """
        if self._df is None or self._image_col not in self._df.columns or not step:
            return None
        col_idx = list(self._df.columns).index(self._image_col)
        row = self._current_row + step
        n = len(self._df)
        while 0 <= row < n:
            path = self._resolve_path(self._df.iat[row, col_idx])
            if path:
                return row, path
            row += step
        return None

    def _current_path(self):
        if (self._df is None or self._image_col not in self._df.columns
                or not 0 <= self._current_row < len(self._df)):
            return None
        col_idx = list(self._df.columns).index(self._image_col)
        return self._resolve_path(self._df.iat[self._current_row, col_idx])

    # ---------- 主图 ----------

    def _update_main_image(self, fade=True):
        path = self._current_path()
        if self._current_row >= 0:
            name = os.path.basename(path) if path else tr("无图片")
            self._main_caption.setText(tr("行 {} · {}").format(self._current_row + 2, name))  # 与网格行号一致（表头占第 1 行）
        else:
            self._main_caption.setText("")
        if path is None:
            self.main_view.set_image(None, None, fade=False)
            return
        pix = self._main_cache.get(path)
        if pix is not None:
            self._main_cache.move_to_end(path)
            self.main_view.set_image(pix, path, fade=fade)
            return
        # 大图未就绪：先用胶片条缩略图occupy（避免空白），后台加载高清图
        thumb = self._thumb_cache.get(path)
        self.main_view.set_image(thumb, path, fade=False)
        if thumb is None:
            self.main_view.setText(tr("加载中..."))
        if path not in self._main_inflight:
            self._main_inflight.add(path)
            self._pool.start(_ImageLoader(path, MAIN_LOAD_CAP, self._main_signals))

    def _on_main_loaded(self, path, img):
        self._main_inflight.discard(path)
        if img.isNull():
            if path == self._current_path():
                self.main_view.set_image(None, path, fade=False)
                self.main_view.setText(tr("(无法加载图片)"))
            return
        pix = QPixmap.fromImage(img)
        self._main_cache[path] = pix
        while len(self._main_cache) > MAIN_CACHE_LIMIT:
            self._main_cache.popitem(last=False)
        if path == self._current_path():
            self.main_view.set_image(pix, path, fade=True)

    # ---------- 胶片条 ----------

    def _clear_strip(self):
        if self._scroll_anim is not None:
            self._scroll_anim.stop()
            self._scroll_anim = None
        for e in self._entries:
            e.setParent(None)
            e.deleteLater()
        self._entries = []

    def _show_image_menu(self, path, global_pos):
        """主图/缩略图的右键菜单：不用打开查看器就能复制。"""
        menu = QMenu(self)
        menu.addAction(tr("复制图片"), lambda: self.copyImageRequested.emit(path))
        menu.addAction(tr("打开大图"), lambda: self.openImageRequested.emit(path))
        menu.exec(global_pos)

    def _strip_range(self):
        center = self._current_row if self._current_row >= 0 else 0
        start = max(0, center - STRIP_BEFORE)
        end = min(len(self._df), center + STRIP_AFTER + 1)
        return start, end

    def _make_cell(self, row, col_idx):
        path = self._resolve_path(self._df.iat[row, col_idx])
        cell = _StripCell(row, path, self._thumb_size())
        cell.clicked.connect(self.rowActivated.emit)
        cell.doubleClicked.connect(self.openImageRequested.emit)
        cell.menuRequested.connect(self._show_image_menu)
        cell.set_focused(row == self._current_row)
        self._set_cell_pixmap(cell)
        if path and path not in self._thumb_cache:
            self._request_thumb(path)
        return cell

    def _rebuild_strip(self):
        self._clear_strip()
        if self._df is None or self._image_col not in self._df.columns:
            return
        col_idx = list(self._df.columns).index(self._image_col)
        start, end = self._strip_range()
        focused_cell = None
        for row in range(start, end):
            cell = self._make_cell(row, col_idx)
            self._strip_layout.insertWidget(self._strip_layout.count() - 1, cell)
            self._entries.append(cell)
            if row == self._current_row:
                focused_cell = cell
        if focused_cell:
            QTimer.singleShot(0, lambda c=focused_cell: self._center_cell(c, animated=False))

    def _incremental_strip_update(self):
        if self._df is None or self._image_col not in self._df.columns:
            return
        col_idx = list(self._df.columns).index(self._image_col)
        start, end = self._strip_range()
        existing = {e.row for e in self._entries}

        for e in list(self._entries):
            if e.row < start or e.row >= end:
                self._entries.remove(e)
                e.setParent(None)
                e.deleteLater()
        for row in range(start, end):
            if row in existing:
                continue
            cell = self._make_cell(row, col_idx)
            pos = sum(1 for e in self._entries if e.row < row)
            self._strip_layout.insertWidget(pos, cell)
            self._entries.insert(pos, cell)

        focused_cell = None
        for e in self._entries:
            e.set_focused(e.row == self._current_row)
            if e.row == self._current_row:
                focused_cell = e
        if focused_cell:
            QTimer.singleShot(0, lambda c=focused_cell: self._center_cell(c, animated=True))

    def _center_cell(self, cell, animated):
        if cell not in self._entries:
            return
        bar = self._strip_scroll.horizontalScrollBar()
        target = cell.x() + cell.width() // 2 - self._strip_scroll.viewport().width() // 2
        target = max(0, min(target, bar.maximum()))
        if self._scroll_anim is not None:
            self._scroll_anim.stop()
        if not animated:
            bar.setValue(target)
            return
        self._scroll_anim = QPropertyAnimation(bar, b"value", self)
        self._scroll_anim.setDuration(240)
        self._scroll_anim.setStartValue(bar.value())
        self._scroll_anim.setEndValue(target)
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scroll_anim.start()

    def _settle_zoom(self):
        self._zoom_pending = False
        self._apply_zoom(smooth=True)

    def _set_cell_pixmap(self, cell, smooth=True):
        size = self._thumb_size()
        cell.img_label.setFixedSize(size)
        if cell.path is None:
            cell.img_label.setText(tr("无图"))
            return
        pix = self._thumb_cache.get(cell.path)
        if pix is not None:
            self._thumb_cache.move_to_end(cell.path)
            mode = (Qt.TransformationMode.SmoothTransformation if smooth
                    else Qt.TransformationMode.FastTransformation)
            cell.img_label.setPixmap(pix.scaled(
                size, Qt.AspectRatioMode.KeepAspectRatio, mode))
        elif cell.path in self._thumb_cache:
            cell.img_label.setText(tr("加载失败"))
        else:
            cell.img_label.setText("…")

    def _request_thumb(self, path):
        if path in self._thumb_inflight:
            return
        self._thumb_inflight.add(path)
        # 按 2 倍请求，缩放滑块放大时不至于糊
        size = QSize(BASE_STRIP_THUMB.width() * 2, BASE_STRIP_THUMB.height() * 2)
        self._pool.start(_ImageLoader(path, size, self._thumb_signals))

    def _on_thumb_loaded(self, path, img):
        self._thumb_inflight.discard(path)
        if img.isNull():
            self._thumb_cache[path] = None   # 记住失败，不再反复重试解码
            for cell in self._entries:
                if cell.path == path:
                    cell.img_label.setText(tr("加载失败"))
            return
        pix = QPixmap.fromImage(img)
        self._thumb_cache[path] = pix
        while len(self._thumb_cache) > THUMB_CACHE_LIMIT:
            self._thumb_cache.popitem(last=False)
        for cell in self._entries:
            if cell.path == path:
                self._set_cell_pixmap(cell)
        # 大图还没就绪时，用刚到的缩略图先occupy主图区
        if path == self._current_path() and path not in self._main_cache:
            self.main_view.set_image(pix, path, fade=False)
