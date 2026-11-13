"""Application-owned document windows and their shared navigator."""
import os

from PyQt6.QtCore import QObject, QEvent, Qt
from PyQt6.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
    QListWidgetItem, QPushButton, QLabel,
)

from .i18n import tr


def document_key(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


class Navigator(QWidget):
    def __init__(self, manager, floating=False):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                         if floating else Qt.WindowType.Widget)
        self.manager = manager
        self.setWindowTitle(tr("窗口导航"))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("已打开的表格（* 表示未保存）")))
        self.list = QListWidget()
        self.list.itemClicked.connect(self.activate_item)
        self.list.itemActivated.connect(self.activate_item)
        layout.addWidget(self.list)
        buttons = QHBoxLayout()
        for title, callback in (
                (tr("新建窗口"), lambda: manager.create_window()),
                (tr("关闭窗口"), self.close_selected)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        mode = QPushButton(tr("内嵌导航") if floating else tr("悬浮导航"))
        mode.clicked.connect(lambda: manager.set_floating(not floating))
        layout.addWidget(mode)
        self.resize(300, 420)

    def refresh(self):
        self.list.clear()
        for i, window in enumerate(self.manager.windows, 1):
            name = (os.path.basename(window.current_file) if window.current_file
                    else tr("未命名"))
            item = QListWidgetItem(f"{i}. {name}{' *' if window.model.modified else ''}")
            item.setData(Qt.ItemDataRole.UserRole, window)
            item.setToolTip(window.current_file or tr("未命名"))
            self.list.addItem(item)
            if window is self.manager.active_window:
                self.list.setCurrentItem(item)

    def activate_item(self, item):
        self.manager.activate(item.data(Qt.ItemDataRole.UserRole))

    def close_selected(self):
        item = self.list.currentItem()
        if item:
            item.data(Qt.ItemDataRole.UserRole).close()


class WindowManager(QObject):
    """Keep strong references until an accepted close; share no document state."""
    def __init__(self, app):
        super().__init__(app)
        self.windows = []
        self.active_window = None
        self.floating = False
        self.navigator = Navigator(self, floating=True)
        self._pending_paths = {}
        self._quitting = False

    def create_window(self, path=None, restore_session=False):
        from .main_window import MainWindow
        window = MainWindow(window_manager=self, restore_session=restore_session)
        self.windows.append(window)
        window.installEventFilter(self)
        window.windowTitleChanged.connect(self.refresh)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if len(self.windows) == 1:
            self.floating = window._settings.value("navigator/floating", False, type=bool)
        panel = Navigator(self)
        dock = QDockWidget(tr("窗口导航"), window)
        dock.setObjectName("windowNavigatorDock")
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable |
                         QDockWidget.DockWidgetFeature.DockWidgetMovable)
        dock.setWidget(panel)
        window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        window.restoreDockWidget(dock)
        window.navigator_dock = dock
        dock.setVisible(not self.floating)
        self.active_window = window
        window.show()
        self.refresh()
        if self.floating:
            self.navigator.show()
        if path:
            self._load_into(window, path)
        return window

    def _load_into(self, window, path):
        window._initial_load_timer.stop()
        key = document_key(path)
        self._pending_paths[key] = window
        try:
            window.load_file(path, _in_window=True)
        finally:
            self._pending_paths.pop(key, None)
        self.refresh()
        return window

    def find_window(self, path):
        key = document_key(path)
        return self._pending_paths.get(key) or next(
            (w for w in self.windows if w.current_file and
             document_key(w.current_file) == key), None)

    def open_file(self, path, source=None):
        path = os.path.abspath(os.path.expanduser(os.fspath(path)))
        existing = self.find_window(path)
        if existing:
            self.activate(existing)
            return existing
        if source in self.windows:
            source._flush_preview()
            if not source.current_file and not source.model.modified:
                return self._load_into(source, path)
        return self.create_window(path)

    def activate(self, window):
        if window not in self.windows:
            return
        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()
        self.active_window = window
        self.refresh()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.WindowActivate and watched in self.windows:
            self.active_window = watched
            self.refresh()
        return False

    def refresh(self, *args):
        self.navigator.refresh()
        for window in self.windows:
            if hasattr(window, "navigator_dock"):
                window.navigator_dock.widget().refresh()

    def set_floating(self, floating):
        self.floating = floating
        for window in self.windows:
            window.navigator_dock.setVisible(not floating)
            window._settings.setValue("navigator/floating", floating)
        self.navigator.setVisible(floating and bool(self.windows))
        if floating:
            self.navigator.raise_()
        self.refresh()

    def show_navigator(self, window):
        if self.floating:
            self.navigator.show()
            self.navigator.raise_()
        else:
            window.navigator_dock.show()

    def unregister(self, window):
        if window in self.windows:
            self.windows.remove(window)
        window.removeEventFilter(self)
        if self.active_window is window:
            self.active_window = self.windows[-1] if self.windows else None
        if not self.windows:
            self.navigator.hide()
        self.refresh()

    def close_all(self):
        if self._quitting:
            return False
        self._quitting = True
        try:
            for window in list(self.windows):
                if not window.close():
                    self.activate(window)
                    return False
            return True
        finally:
            self._quitting = False
