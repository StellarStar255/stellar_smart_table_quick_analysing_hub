"""Application appearance. Workbook formatting is deliberately kept separate."""
import weakref
from PyQt6 import sip

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QPalette
from PyQt6.QtWidgets import QApplication, QStyleFactory, QWidget

from .i18n import current_language


def label(zh, en):
    return en if current_language() == 'en' else zh


# Same palette family as Stellar Smart Terminal; no runtime dependency on it.
THEMES = {
    'deep_blue': ('深蓝', 'Deep blue', '#0f0f1a', '#1a1a2e', '#16213e', '#2d2d44', '#3d3d5c', '#667eea', '#eaeaea', '#888888'),
    'purple': ('暗夜紫', 'Night purple', '#13111a', '#1e1a2e', '#2a2440', '#3a3350', '#4a4360', '#a855f7', '#f0e6ff', '#9988aa'),
    'amber': ('琥珀', 'Amber', '#0f0d0b', '#171310', '#1f1a16', '#2a231d', '#362d25', '#f59e0b', '#f2ece4', '#a09384'),
    'midnight': ('午夜黑', 'Midnight', '#000000', '#0a0a0a', '#141414', '#1e1e1e', '#2a2a2a', '#3b82f6', '#f5f5f5', '#888888'),
    'light': ('浅色', 'Light', '#ececef', '#f5f5f7', '#ffffff', '#e3e3e8', '#d1d1d6', '#007aff', '#1d1d1f', '#6e6e73'),
    'pink': ('粉红', 'Pink', '#1a0a14', '#2e1a28', '#3e2838', '#4e3848', '#5e4858', '#ec4899', '#fce7f3', '#aa8899'),
}
SKINS = {'standard': ('标准', 'Standard'), 'rounded': ('圆润', 'Rounded'), 'compact': ('紧凑', 'Compact')}


def luminance(color):
    c = QColor(color)
    rgb = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4
           for v in (c.redF(), c.greenF(), c.blueF())]
    return sum(a * b for a, b in zip(rgb, (.2126, .7152, .0722)))


def contrast(a, b):
    x, y = sorted((luminance(a), luminance(b)))
    return (y + .05) / (x + .05)


def readable(color, background, minimum=4.5):
    """Retain a semantic hue while making text readable on either surface."""
    source = QColor(color)
    target = QColor('#ffffff' if contrast('#ffffff', background) > contrast('#000000', background) else '#000000')
    for step in range(101):
        ratio = step / 100
        candidate = QColor(QColor.fromRgbF(*[a + (b - a) * ratio for a, b in zip(
            (source.redF(), source.greenF(), source.blueF()),
            (target.redF(), target.greenF(), target.blueF()))]).name())
        if contrast(candidate, background) >= minimum:
            return candidate
    return target


def theme_palette(key):
    _, _, darkest, window, base, button, border, accent, text, dim = THEMES[key]
    p = QPalette()
    roles = {
        'Window': window, 'WindowText': text, 'Base': base, 'AlternateBase': window,
        'Text': text, 'Button': button, 'ButtonText': text, 'Highlight': accent,
        'HighlightedText': readable('#ffffff', accent), 'Link': readable(accent, base),
        'LinkVisited': readable('#a855f7', base), 'PlaceholderText': readable(dim, base),
        'Light': border, 'Midlight': button, 'Mid': border, 'Dark': darkest,
        'Shadow': darkest, 'ToolTipBase': button, 'ToolTipText': text,
    }
    for name, color in roles.items():
        p.setColor(getattr(QPalette.ColorRole, name), QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(dim))
    return p


def set_color_scheme(hints, scheme):
    # Explicit native appearance overrides were added after our Qt 6.6 minimum.
    setter = getattr(hints, 'setColorScheme', None)
    if setter is not None:
        if scheme == Qt.ColorScheme.Unknown and hasattr(hints, 'unsetColorScheme'):
            hints.unsetColorScheme()
        else:
            setter(scheme)


def stylesheet(skin):
    radius, padding = {'standard': (4, '4px 8px'), 'rounded': (10, '6px 10px'), 'compact': (2, '2px 5px')}[skin]
    # Palette roles also cover dialogs and widgets created after switching.
    return f"""
    QToolTip {{ background: palette(tool-tip-base); color: palette(tool-tip-text); border: 1px solid palette(mid); padding: 5px; }}
    QToolBar {{ border: none; spacing: 3px; padding: 2px; }}
    QPushButton, QToolButton, QComboBox {{ background: palette(button); color: palette(button-text); border: 1px solid palette(mid); border-radius: {radius}px; padding: {padding}; }}
    QPushButton:hover, QToolButton:hover, QComboBox:hover {{ border-color: palette(highlight); }}
    QPushButton:pressed, QToolButton:pressed, QToolButton:checked {{ background: palette(highlight); color: palette(highlighted-text); }}
    QPushButton:disabled, QToolButton:disabled, QComboBox:disabled {{ color: palette(placeholder-text); }}
    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{ background: palette(base); color: palette(text); border: 1px solid palette(mid); border-radius: {radius}px; padding: 3px; selection-background-color: palette(highlight); selection-color: palette(highlighted-text); }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: palette(highlight); }}
    QAbstractItemView {{ background: palette(base); alternate-background-color: palette(alternate-base); color: palette(text); gridline-color: palette(mid); selection-background-color: palette(highlight); selection-color: palette(highlighted-text); }}
    QHeaderView::section {{ background: palette(button); color: palette(button-text); border: 1px solid palette(mid); padding: 3px; }}
    QHeaderView::section:vertical {{ padding: 0px; }}
    QMenu {{ background: palette(window); color: palette(window-text); border: 1px solid palette(mid); }}
    QMenu::item {{ padding: 5px 24px; }}
    QMenu::item:selected {{ background: palette(highlight); color: palette(highlighted-text); }}
    QMenu::item:disabled {{ color: palette(placeholder-text); }}
    QMenu::separator {{ height: 1px; background: palette(mid); margin: 4px; }}
    QDockWidget::title {{ background: palette(button); padding: 4px; }}
    QTabBar::tab {{ background: palette(button); color: palette(text); padding: {padding}; border-radius: {radius}px; }}
    QTabBar::tab:selected {{ background: palette(base); color: palette(link); }}
    """


class ThemeManager(QObject):
    changed = pyqtSignal(str, str)

    def __init__(self, app, settings):
        super().__init__(app)
        self.app, self.settings = app, settings
        self.windows = weakref.WeakSet()
        self.original_palette = QPalette(app.palette())
        self.original_style = app.style().objectName()
        self.original_sheet = app.styleSheet()
        self.original_scheme = app.styleHints().colorScheme()
        theme = str(settings.value('appearance/theme', 'system'))
        skin = str(settings.value('appearance/skin', 'standard'))
        self.theme = theme if theme == 'system' or theme in THEMES else 'system'
        self.skin = skin if skin in SKINS else 'standard'
        self._applying = False
        self._styled = False
        app.styleHints().colorSchemeChanged.connect(self._system_changed)
        if self.theme != 'system' or self.skin != 'standard':
            self.apply()

    def register(self, window):
        self.windows.add(window)
        self.refresh_window(window)

    def set_theme(self, theme):
        if theme != 'system' and theme not in THEMES:
            raise ValueError(theme)
        if theme != self.theme:
            self.theme = theme
            self.settings.setValue('appearance/theme', theme)
            self.settings.sync()
            self.apply()

    def set_skin(self, skin):
        if skin not in SKINS:
            raise ValueError(skin)
        if skin != self.skin:
            self.skin = skin
            self.settings.setValue('appearance/skin', skin)
            self.settings.sync()
            self.apply()

    def _system_changed(self, scheme):
        if self._applying or self.theme != 'system':
            return
        # When Qt follows an OS appearance change, capture its new native palette.
        if not self._styled:
            self.original_palette = QPalette(self.app.palette())
        else:
            self.original_palette = theme_palette('light' if scheme == Qt.ColorScheme.Light else 'midnight')
        self.original_scheme = scheme
        self.apply()

    def apply(self):
        self._applying = True
        try:
            hints = self.app.styleHints()
            self.app.setStyleSheet('')
            if self.theme == 'system':
                set_color_scheme(hints, Qt.ColorScheme.Unknown)
                self.app.setStyle(QStyleFactory.create(self.original_style))
                scheme = hints.colorScheme()
                palette = self.original_palette
                if scheme != self.original_scheme and scheme != Qt.ColorScheme.Unknown:
                    palette = theme_palette('light' if scheme == Qt.ColorScheme.Light else 'midnight')
            else:
                set_color_scheme(hints, Qt.ColorScheme.Light if self.theme == 'light' else Qt.ColorScheme.Dark)
                self.app.setStyle(QStyleFactory.create('Fusion'))
                palette = theme_palette(self.theme)
            # Set the palette before stylesheet polishing (also affects native popups).
            self.app.setPalette(palette)
            self._styled = self.theme != 'system' or self.skin != 'standard'
            self.app.setStyleSheet(self.original_sheet + (stylesheet(self.skin) if self._styled else ''))
            for window in list(self.windows):
                if sip.isdeleted(window):
                    self.windows.discard(window)
                    continue
                self.refresh_window(window)
            self.changed.emit(self.theme, self.skin)
        finally:
            self._applying = False

    def refresh_window(self, window):
        window._appearance_density = .82 if self.skin == 'compact' else 1.0
        window._apply_zoom()
        p = self.app.palette()
        custom = self.theme != 'system'
        window.model.set_ui_palette(p, custom)
        accent = p.color(QPalette.ColorRole.Link).name() if custom else '#e67e22'
        window.sheet_tabs.setStyleSheet(window._sheet_tab_stylesheet.replace('rgb(230, 126, 34)', accent))
        window.table.viewport().update()
        for editor in window.findChildren(QWidget):
            if sip.isdeleted(editor):
                continue
            assistant = getattr(editor, 'formula_assistant', None)
            if assistant and getattr(editor, 'hasFocus', lambda: False)():
                assistant.highlight_references()
                assistant.refresh()
            elif assistant:
                assistant.hide()
        for key, action in getattr(window, '_theme_actions', {}).items():
            action.setChecked(key == self.theme)
        for key, action in getattr(window, '_skin_actions', {}).items():
            action.setChecked(key == self.skin)


def manager(settings):
    app = QApplication.instance()
    if not hasattr(app, '_appearance_manager'):
        app._appearance_manager = ThemeManager(app, settings)
    return app._appearance_manager


def add_appearance_menu(window, view_menu):
    appearance = view_menu.addMenu(label('外观', 'Appearance'))
    theme_menu = appearance.addMenu(label('主题', 'Theme'))
    skin_menu = appearance.addMenu(label('控件皮肤', 'Control skin'))
    window._theme_actions, window._skin_actions = {}, {}
    for menu, choices, actions, setter in (
        (theme_menu, {'system': ('跟随系统', 'Follow system'), **{k: v[:2] for k, v in THEMES.items()}}, window._theme_actions, 'set_theme'),
        (skin_menu, SKINS, window._skin_actions, 'set_skin'),
    ):
        group = QActionGroup(window)
        group.setExclusive(True)
        for key, names in choices.items():
            action = QAction(label(*names), group)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, k=key, method=setter: getattr(window.appearance, method)(k))
            menu.addAction(action)
            actions[key] = action
