# -*- coding: utf-8 -*-
"""
PyQt6 版图片队列浮窗 - 对应 Tkinter 版 ui/image_queue.py。

用于逐一复制图片到剪贴板，方便依次粘贴到飞书等应用。
窗口始终置顶；macOS 上支持自动模式：检测全局 Cmd+V 后自动下一张并复制。
"""

import os
import threading
import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from qtui.image_viewer import ImageViewer, copy_image_to_clipboard
from qtui.i18n import tr

# macOS 全局按键监控（自动模式）：Quartz CGEventTap 在后台线程运行
try:
    from Quartz import (
        CGEventTapCreate, CGEventTapEnable,
        kCGSessionEventTap, kCGHeadInsertEventTap,
        kCGEventKeyDown,
        CGEventGetIntegerValueField, kCGKeyboardEventKeycode,
        CGEventGetFlags, kCGEventFlagMaskCommand,
        CFMachPortCreateRunLoopSource,
        CFRunLoopGetCurrent, CFRunLoopAddSource, CFRunLoopRun, CFRunLoopStop,
        kCFRunLoopCommonModes,
    )
    QUARTZ_AVAILABLE = True
except ImportError:
    QUARTZ_AVAILABLE = False

_VK_ANSI_V = 9  # macOS 'V' 键码
_PREVIEW_MAX_W = 260
_PREVIEW_MAX_H = 200


class _ThumbnailLabel(QLabel):
    """支持双击信号的缩略图标签"""
    doubleClicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event):
        self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)


class FloatingImageQueue(QWidget):
    """浮动图片队列窗口，逐一复制图片到剪贴板"""

    # 后台 tap 线程 → 主线程（信号跨线程安全）
    _cmd_v_detected = pyqtSignal()
    _tap_no_permission = pyqtSignal()

    def __init__(self, image_paths, parent=None, on_index_change=None, on_close=None):
        super().__init__(parent)
        self.image_paths = list(image_paths)
        self.on_index_change = on_index_change
        self.on_close = on_close
        self.current_index = 0
        self._last_auto_advance_time = 0
        self._viewers = []  # 保持打开的查看器引用

        # 自动模式相关
        self._auto_thread = None
        self._tap_runloop_ref = None

        self.setWindowFlags(Qt.WindowType.Window
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(tr("图片队列复制"))

        self._build_ui()

        self._cmd_v_detected.connect(self._auto_advance)
        self._tap_no_permission.connect(self._on_no_permission)

        # 打开时自动复制第一张
        QTimer.singleShot(100, self._copy_current)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        # 缩略图预览（双击打开查看器）
        self.preview_label = _ThumbnailLabel()
        self.preview_label.setFixedSize(_PREVIEW_MAX_W, _PREVIEW_MAX_H)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet(
            "background: #e9ecef; border: 1px solid #adb5bd; color: #666;")
        self.preview_label.doubleClicked.connect(self._open_viewer)
        layout.addWidget(self.preview_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        # 进度（可编辑跳转）
        progress_row = QHBoxLayout()
        progress_row.addStretch()
        progress_row.addWidget(QLabel(tr("进度:")))
        self.progress_edit = QLineEdit()
        self.progress_edit.setFixedWidth(48)
        self.progress_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_edit.returnPressed.connect(self._jump_to_index)
        progress_row.addWidget(self.progress_edit)
        self.total_label = QLabel("")
        progress_row.addWidget(self.total_label)
        progress_row.addStretch()
        layout.addLayout(progress_row)

        # 文件名
        self.filename_label = QLabel("")
        self.filename_label.setStyleSheet("color: #666; font-size: 11px;")
        self.filename_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.filename_label)

        # 状态反馈
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #28a745; font-size: 11px;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        # 按钮区域
        btn_row = QHBoxLayout()
        self.prev_btn = QPushButton(tr("◀ 上一张"))
        self.prev_btn.clicked.connect(self._go_prev)
        btn_row.addWidget(self.prev_btn)
        self.copy_btn = QPushButton(tr("复制当前"))
        self.copy_btn.clicked.connect(self._copy_current)
        btn_row.addWidget(self.copy_btn)
        self.next_btn = QPushButton(tr("下一张 ▶"))
        self.next_btn.clicked.connect(self._go_next)
        btn_row.addWidget(self.next_btn)
        self.close_btn = QPushButton(tr("关闭"))
        self.close_btn.clicked.connect(self.close)
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

        # 按钮不抢 Enter/方向键焦点
        for btn in (self.prev_btn, self.copy_btn, self.next_btn, self.close_btn):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # 自动模式（仅 macOS + Quartz 可用时显示）
        self.auto_check = QCheckBox(tr("粘贴后自动下一张"))
        self.auto_check.toggled.connect(self._on_auto_toggle)
        layout.addWidget(self.auto_check, alignment=Qt.AlignmentFlag.AlignHCenter)
        if not QUARTZ_AVAILABLE:
            self.auto_check.hide()

        self._update_display()

    # ---- 键盘 ----

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Left:
            self._go_prev()
        elif key == Qt.Key.Key_Right:
            self._go_next()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # 输入框回车由 returnPressed 处理
            if not self.progress_edit.hasFocus():
                self._copy_current()
        elif key == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    # ---- 自动模式：后台线程 CGEventTap 监控 Cmd+V ----

    def _on_auto_toggle(self, checked):
        if checked:
            self._start_auto_monitor()
        else:
            self._stop_auto_monitor()

    def _start_auto_monitor(self):
        if not QUARTZ_AVAILABLE:
            self.auto_check.setChecked(False)
            return
        self._stop_auto_monitor()
        self._auto_thread = threading.Thread(target=self._run_cmdv_tap, daemon=True)
        self._auto_thread.start()

    def _run_cmdv_tap(self):
        """后台线程：CGEventTap 监控全局 Cmd+V，通过信号通知主线程"""

        def tap_callback(proxy, event_type, event, refcon):
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
            flags = CGEventGetFlags(event)
            if keycode == _VK_ANSI_V and (flags & kCGEventFlagMaskCommand):
                self._cmd_v_detected.emit()
            return event

        # kCGEventTapOptionListenOnly = 1（只监听，不拦截）
        tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            0x00000001,
            (1 << kCGEventKeyDown),
            tap_callback,
            None,
        )

        if not tap:
            self._tap_no_permission.emit()
            return

        self._tap_runloop_ref = CFRunLoopGetCurrent()
        source = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(self._tap_runloop_ref, source, kCFRunLoopCommonModes)
        CGEventTapEnable(tap, True)
        CFRunLoopRun()  # 阻塞直到 CFRunLoopStop

    def _stop_auto_monitor(self):
        if self._tap_runloop_ref is not None:
            try:
                CFRunLoopStop(self._tap_runloop_ref)
            except Exception:
                pass
            self._tap_runloop_ref = None
        if self._auto_thread is not None and self._auto_thread.is_alive():
            self._auto_thread.join(timeout=1.0)
        self._auto_thread = None

    def _on_no_permission(self):
        self.auto_check.setChecked(False)
        QMessageBox.information(
            self, tr("需要权限"),
            tr("自动模式需要「辅助功能」权限\n"
               "请在 系统设置 → 隐私与安全性 → 辅助功能 中\n"
               "添加运行此程序的终端或 Python")
        )

    def _auto_advance(self):
        """检测到 Cmd+V 后自动下一张并复制"""
        if not self.auto_check.isChecked():
            return
        # 防抖：500ms 内不重复触发
        now = time.time()
        if now - self._last_auto_advance_time < 0.5:
            return
        self._last_auto_advance_time = now
        self._go_next()

    # ---- 显示更新 ----

    def _update_display(self):
        total = len(self.image_paths)
        idx = self.current_index

        self.progress_edit.setText(str(idx + 1))
        self.total_label.setText(f" / {total}")

        path = self.image_paths[idx]
        filename = os.path.basename(path)
        if len(filename) > 35:
            filename = filename[:32] + "..."
        self.filename_label.setText(filename)

        self._update_preview(path)

        self.prev_btn.setEnabled(idx > 0)
        self.next_btn.setEnabled(idx < total - 1)

        if self.on_index_change:
            try:
                self.on_index_change(idx)
            except Exception:
                pass

    def _update_preview(self, path):
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText(tr("(无法预览)"))
            return
        scaled = pixmap.scaled(
            _PREVIEW_MAX_W, _PREVIEW_MAX_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setText("")
        self.preview_label.setPixmap(scaled)

    # ---- 操作 ----

    def _copy_current(self):
        if not self.image_paths:
            return
        path = self.image_paths[self.current_index]
        total = len(self.image_paths)
        if copy_image_to_clipboard(path):
            self.status_label.setText(
                tr("已复制 {}/{}").format(self.current_index + 1, total))
        else:
            self.status_label.setText(tr("复制失败"))
        QTimer.singleShot(1500, lambda: self.status_label.setText(""))

    def _jump_to_index(self):
        try:
            num = int(self.progress_edit.text())
        except ValueError:
            self._update_display()  # 恢复为当前值
            return
        total = len(self.image_paths)
        num = max(1, min(num, total))
        self.current_index = num - 1
        self._update_display()
        self._copy_current()

    def _go_prev(self):
        """上一张并自动复制"""
        if self.current_index > 0:
            self.current_index -= 1
            self._update_display()
            self._copy_current()

    def _go_next(self):
        """下一张并自动复制"""
        if self.current_index < len(self.image_paths) - 1:
            self.current_index += 1
            self._update_display()
            self._copy_current()

    def _open_viewer(self):
        if not self.image_paths:
            return
        viewer = ImageViewer(self.image_paths[self.current_index])
        viewer.show()
        self._viewers.append(viewer)

    def closeEvent(self, event):
        self._stop_auto_monitor()
        if self.on_close:
            try:
                self.on_close()
            except Exception:
                pass
        super().closeEvent(event)
