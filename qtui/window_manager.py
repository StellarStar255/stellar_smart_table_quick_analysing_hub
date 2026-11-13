"""Application-owned document windows and their shared navigator."""
import os

from PyQt6.QtCore import QObject, QEvent, Qt, QSize, QRect
from PyQt6.QtGui import QFont, QIcon, QPalette
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QToolButton, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QMenu, QSizePolicy,
)

from .i18n import tr


def document_key(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


class DocumentDelegate(QStyledItemDelegate):
    """Two readable, elided lines without giving long paths control of layout."""
    def sizeHint(self, option, index):
        return QSize(0, max(54, option.fontMetrics.height() * 2 + 20))

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        name = opt.text
        opt.text = ""
        opt.icon = QIcon()
        style = opt.widget.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        painter.save()
        painter.setClipRect(option.rect)
        rect = option.rect.adjusted(12, 8, -12, -8)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        color = option.palette.color(role)
        painter.setPen(color)
        # Paint with the active palette: native file icons can be black on
        # dark Linux themes and disappear against the sidebar background.
        x, y = rect.left() + 2, rect.top() + 2
        painter.drawRoundedRect(QRect(x, y, 13, 17), 1, 1)
        painter.drawLine(x + 3, y + 6, x + 10, y + 6)
        painter.drawLine(x + 3, y + 10, x + 10, y + 10)
        rect.adjust(26, 0, 0, 0)
        font = QFont(option.font)
        font.setBold(bool(index.data(Qt.ItemDataRole.UserRole + 2)))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        line_height = metrics.height()
        painter.drawText(rect.left(), rect.top() + metrics.ascent(),
                         metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, rect.width()))
        font.setBold(False)
        if font.pointSizeF() > 0:
            font.setPointSizeF(max(8, font.pointSizeF() - 1))
        painter.setFont(font)
        color.setAlpha(190)
        painter.setPen(color)
        metrics = painter.fontMetrics()
        subtitle = str(index.data(Qt.ItemDataRole.UserRole + 1) or "")
        painter.drawText(rect.left(), rect.top() + line_height + 4 + metrics.ascent(),
                         metrics.elidedText(subtitle, Qt.TextElideMode.ElideLeft, rect.width()))
        painter.restore()


class Navigator(QWidget):
    def __init__(self, manager, floating=False, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                         if floating else Qt.WindowType.Widget)
        self.manager = manager
        self.floating = floating
        self.setObjectName("windowNavigator")
        self.setWindowTitle(tr("窗口导航"))
        self.setMinimumWidth(200)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel(tr("窗口导航"))
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        header.addWidget(title)
        self.count_label = QLabel("0")
        header.addWidget(self.count_label)
        header.addStretch(1)
        self.mode_button = QToolButton(self)
        self.mode_button.setText("↙" if floating else "↗")
        self.mode_button.setToolTip(tr("内嵌导航") if floating else tr("悬浮导航"))
        self.mode_button.setAccessibleName(self.mode_button.toolTip())
        self.mode_button.setAutoRaise(True)
        self.mode_button.clicked.connect(lambda: manager.set_floating(not floating))
        header.addWidget(self.mode_button)
        self.hide_button = QToolButton(self)
        self.hide_button.setText("‹")
        self.hide_button.setToolTip(tr("收起导航（可从“视图 → 窗口导航”恢复）"))
        self.hide_button.setAccessibleName(tr("收起导航"))
        self.hide_button.setAutoRaise(True)
        self.hide_button.clicked.connect(self.hide)
        self.hide_button.setVisible(not floating)
        header.addWidget(self.hide_button)
        layout.addLayout(header)
        self.list = QListWidget(self)
        self.list.setMinimumWidth(0)
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setWordWrap(False)
        self.list.setItemDelegate(DocumentDelegate(self.list))
        self.list.itemClicked.connect(self.activate_item)
        self.list.itemActivated.connect(self.activate_item)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.show_context_menu)
        layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        self.new_button = QPushButton(tr("新建窗口"))
        self.new_button.clicked.connect(lambda: manager.create_window())
        buttons.addWidget(self.new_button, 1)
        self.close_button = QToolButton(self)
        self.close_button.setText("×")
        self.close_button.setToolTip(tr("关闭选中的窗口"))
        self.close_button.setAccessibleName(tr("关闭选中的窗口"))
        self.close_button.clicked.connect(self.close_selected)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        hint = QLabel(tr("* 表示未保存"))
        hint.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        layout.addWidget(hint)
        if floating:
            self.resize(280, 420)

    def refresh(self):
        # Keep rows (and scroll position) when only titles/active markers change.
        windows = self.manager.windows
        while self.list.count() > len(windows):
            self.list.takeItem(self.list.count() - 1)
        self.count_label.setText(str(len(windows)))
        for i, window in enumerate(windows):
            name = (os.path.basename(window.current_file) if window.current_file
                    else tr("未命名"))
            item = self.list.item(i)
            if item is None:
                item = QListWidgetItem()
                self.list.addItem(item)
            item.setText(f"{name}{' *' if window.model.modified else ''}")
            item.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
            item.setData(Qt.ItemDataRole.UserRole, window)
            item.setData(Qt.ItemDataRole.UserRole + 1,
                         os.path.dirname(window.current_file) if window.current_file else tr("尚未保存到文件"))
            item.setData(Qt.ItemDataRole.UserRole + 2, window is self.manager.active_window)
            item.setToolTip(window.current_file or tr("未命名"))
            if window is self.manager.active_window:
                self.list.setCurrentItem(item)
        self.close_button.setEnabled(bool(windows))

    def show_context_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        window = item.data(Qt.ItemDataRole.UserRole)
        menu.addAction(tr("关闭窗口"), lambda: window.close())
        menu.exec(self.list.viewport().mapToGlobal(pos))

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
        window.navigator_panel.setVisible(not self.floating)
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
            if hasattr(window, "navigator_panel"):
                window.navigator_panel.refresh()

    def set_floating(self, floating):
        self.floating = floating
        for window in self.windows:
            window.navigator_panel.setVisible(not floating)
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
            window.navigator_panel.show()

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
