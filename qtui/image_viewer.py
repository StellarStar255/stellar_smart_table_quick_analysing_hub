# -*- coding: utf-8 -*-
"""
PyQt6 版图片查看器 - 对应 Tkinter 版 ui/image_viewer.py。

ImageViewer: 独立图片查看窗口，QGraphicsView 实现缩放和拖拽。
copy_image_to_clipboard: 跨平台复制图片到剪贴板。
"""

import mimetypes
import os
import platform
import subprocess

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import (
    QAction, QKeySequence, QPainter, QPixmap, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
    QLabel, QMainWindow, QMenu, QMessageBox,
)

from qtui.i18n import tr
from qtui.image_utils import load_image


def copy_image_to_clipboard(image_path):
    """复制图片到剪贴板，成功返回 True"""
    if not os.path.exists(image_path):
        return False

    system = platform.system()
    try:
        if system == 'Darwin':
            # 复制文件引用（POSIX file），粘贴到飞书/微信时是图片文件本身。
            # 路径作为参数传入而不是拼进脚本文本，文件名含引号/反斜杠也安全。
            script = 'on run argv\nset the clipboard to (POSIX file (item 1 of argv))\nend run'
            subprocess.run(['osascript', '-e', script, image_path],
                           check=True, capture_output=True, timeout=10)
            return True
        elif system == 'Windows':
            # PowerShell 单引号字符串不做插值，只需把 ' 写成 ''
            ps_path = image_path.replace("'", "''")
            script = f'''
            Add-Type -AssemblyName System.Windows.Forms
            $image = [System.Drawing.Image]::FromFile('{ps_path}')
            [System.Windows.Forms.Clipboard]::SetImage($image)
            '''
            subprocess.run(['powershell', '-NoProfile', '-Command', script],
                           check=True, capture_output=True, timeout=10)
            return True
        else:
            mime = mimetypes.guess_type(image_path)[0]
            if not mime or not mime.startswith('image/'):
                mime = 'image/png'
            subprocess.run(['xclip', '-selection', 'clipboard',
                            '-t', mime, '-i', image_path],
                           check=True, capture_output=True, timeout=10)
            return True
    except Exception:
        # 系统工具失败时退回 Qt 剪贴板（粘贴为位图数据，已应用 EXIF 方向）
        image = load_image(image_path)
        if image.isNull():
            return False
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setImage(image)
        return True


class _ZoomableView(QGraphicsView):
    """滚轮缩放（以光标为中心）+ 拖拽平移的视图"""

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.1 if delta > 0 else 1 / 1.1
        self.scale(factor, factor)


class ImageViewer(QMainWindow):
    """独立的图片查看器窗口，支持缩放和拖拽"""

    def __init__(self, image_path, parent=None):
        super().__init__(parent)
        self.image_path = image_path
        self.setWindowTitle(os.path.basename(image_path))

        self._pixmap = (QPixmap.fromImage(load_image(image_path))
                        if os.path.exists(image_path) else QPixmap())
        self._fitted = False

        if self._pixmap.isNull():
            # 图片缺失或损坏时显示提示，不崩溃
            label = QLabel(tr("无法加载图片:\n{}").format(image_path))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            self.setCentralWidget(label)
            self.view = None
            self.resize(400, 200)
        else:
            self.scene = QGraphicsScene(self)
            self._item = QGraphicsPixmapItem(self._pixmap)
            self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            self.scene.addItem(self._item)
            self.view = _ZoomableView(self.scene, self)
            self.view.setBackgroundBrush(Qt.GlobalColor.darkGray)
            self.setCentralWidget(self.view)
            self._init_size()

        self._setup_shortcuts()

    def _init_size(self):
        """窗口大小取屏幕 80% 与图片尺寸的较小者"""
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            max_w = int(avail.width() * 0.8)
            max_h = int(avail.height() * 0.8)
        else:
            max_w, max_h = 1024, 768
        w = min(self._pixmap.width() + 20, max_w)
        h = min(self._pixmap.height() + 20, max_h)
        self.resize(max(w, 300), max(h, 200))

    def _setup_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.close)
        QShortcut(QKeySequence("Ctrl+W"), self, activated=self.close)  # macOS 上即 Cmd+W
        QShortcut(QKeySequence("Ctrl+C"), self, activated=self._copy_image)

    def showEvent(self, event):
        super().showEvent(event)
        # 打开时适配窗口
        if not self._fitted and self.view is not None:
            self.view.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)
            self._fitted = True

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        copy_action = QAction(tr("复制图片"), menu)
        copy_action.triggered.connect(self._copy_image)
        menu.addAction(copy_action)
        reveal_action = QAction(tr("在Finder中显示"), menu)
        reveal_action.triggered.connect(self._reveal_in_finder)
        menu.addAction(reveal_action)
        menu.addSeparator()
        close_action = QAction(tr("关闭"), menu)
        close_action.triggered.connect(self.close)
        menu.addAction(close_action)
        menu.exec(event.globalPos())

    def _copy_image(self):
        if copy_image_to_clipboard(self.image_path):
            name = os.path.basename(self.image_path)
            self.setWindowTitle(tr("{} (已复制)").format(name))
            QTimer.singleShot(1500, lambda: self.setWindowTitle(name))
        else:
            QMessageBox.warning(self, tr("错误"), tr("复制图片失败"))

    def _reveal_in_finder(self):
        system = platform.system()
        try:
            if system == 'Darwin':
                subprocess.run(['open', '-R', self.image_path])
            elif system == 'Windows':
                subprocess.run(['explorer', '/select,', self.image_path])
            else:
                subprocess.run(['xdg-open', os.path.dirname(self.image_path)])
        except Exception as e:
            QMessageBox.warning(self, tr("错误"), tr("无法打开文件位置: {}").format(e))
