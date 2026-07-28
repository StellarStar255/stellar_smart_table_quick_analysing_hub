# -*- coding: utf-8 -*-
"""
PyQt6 版通用对话框。

LoadingProgressDialog: 对应 Tkinter 版 ui/dialogs.py 的同名类。
原版通过手动 pump 事件循环保持动画；Qt 版用 QThread + 信号，天然线程安全。
"""

import traceback

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QLabel, QProgressBar, QVBoxLayout, QApplication,
)

from qtui.i18n import tr


class _Worker(QThread):
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)
    progressed = pyqtSignal(str)

    def __init__(self, func, parent=None):
        super().__init__(parent)
        self._func = func

    def run(self):
        # 纯 Python 密集任务（如 openpyxl 写盘）会长时间占住 GIL，
        # 把主线程 UI 一起拖卡；缩短切换间隔让 UI 线程更容易抢到 GIL。
        import sys
        old_interval = sys.getswitchinterval()
        sys.setswitchinterval(0.001)
        try:
            result = self._func()
            self.finished_ok.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            sys.setswitchinterval(old_interval)


class LoadingProgressDialog(QDialog):
    """模态加载对话框，支持确定/不确定两种进度模式。

    API 与 Tkinter 版保持一致:
        update(progress, message=None)
        set_indeterminate(message=None)
        set_determinate(progress=0, message=None)
        run_in_background(func, callback=None)
        close()
    """

    def __init__(self, parent=None, title=None, message=None):
        title = title if title is not None else tr("加载中")
        message = message if message is not None else tr("请稍候...")
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
        )
        self.setFixedWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self._label = QLabel(message)
        self._label.setWordWrap(True)
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        layout.addWidget(self._bar)

        self._worker = None

    # ---------- 与 Tkinter 版一致的 API ----------

    def update(self, progress, message=None):
        self._bar.setRange(0, 100)
        self._bar.setValue(int(progress))
        if message is not None:
            self._label.setText(message)
        QApplication.processEvents()

    def set_indeterminate(self, message=None):
        self._bar.setRange(0, 0)  # Qt 的不确定模式
        if message is not None:
            self._label.setText(message)

    def set_determinate(self, progress=0, message=None):
        self._bar.setRange(0, 100)
        self._bar.setValue(int(progress))
        if message is not None:
            self._label.setText(message)

    def report(self, message):
        """供后台任务（工作线程）更新进度文案，线程安全。"""
        if self._worker is not None:
            self._worker.progressed.emit(message)

    def run_in_background(self, func, callback=None):
        """后台线程执行 func，完成后在主线程回调 callback(result)。"""
        self._worker = _Worker(func, self)
        self._worker.progressed.connect(self._label.setText)

        def _done(result):
            self.close()
            if callback:
                callback(result)

        def _fail(tb):
            self.close()
            if callback:
                callback(None)
            print(tb)

        self._worker.finished_ok.connect(_done)
        self._worker.failed.connect(_fail)
        self._worker.start()
        self.exec()
