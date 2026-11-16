"""Application appearance. Workbook formatting is deliberately kept separate."""
import weakref
from PyQt6 import sip

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QPalette
from PyQt6.QtWidgets import QApplication, QStyleFactory, QWidget

from .i18n import current_language


def label(zh, en):
    return en if current_language() == 'en' else zh


# Neutral surfaces carry the data; each theme's hue is reserved for accents.
# Fields: zh, en, shadow, window, canvas, control, border, accent, text, muted.
THEMES = {
    'deep_blue': ('深蓝', 'Deep blue', '#111720', '#1e2733', '#18212c', '#283443', '#344252', '#8bb8ec', '#e2eaf3', '#9aaabd'),
    'purple': ('暗夜紫', 'Night purple', '#17151e', '#26232f', '#1f1d27', '#312d3b', '#403a4c', '#b8a1df', '#e9e4f0', '#aaa0ba'),
    'amber': ('琥珀', 'Amber', '#191714', '#2b2722', '#231f1b', '#363029', '#463d33', '#d8b27c', '#eee7dd', '#b4a695'),
    'midnight': ('午夜黑', 'Midnight', '#101113', '#222428', '#1a1c20', '#2d3035', '#3a3e45', '#a0b9d8', '#e5e7eb', '#a0a6b0'),
    'light': ('浅色', 'Light', '#dce1e8', '#f3f5f8', '#ffffff', '#eaeef3', '#d5dce5', '#3569a8', '#263244', '#68778b'),
    'pink': ('粉红', 'Pink', '#1c1619', '#2e252a', '#251e22', '#3a2e34', '#4b3c43', '#d9a0b3', '#f0e5e9', '#b9a0aa'),
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


def blend(background, foreground, amount):
    a, b = QColor(background), QColor(foreground)
    return QColor.fromRgbF(*[
        x + (y - x) * amount for x, y in zip(
            (a.redF(), a.greenF(), a.blueF()),
            (b.redF(), b.greenF(), b.blueF()))])


def theme_palette(key):
    _, _, darkest, window, base, button, border, accent, text, dim = THEMES[key]
    p = QPalette()
    # Selection is a quiet tinted surface, independent of the focus/link accent.
    selection = blend(base, accent, .16 if key == 'light' else .26)
    roles = {
        'Window': window, 'WindowText': text, 'Base': base, 'AlternateBase': blend(base, window, .45),
        'Text': text, 'Button': button, 'ButtonText': text, 'Highlight': selection,
        'HighlightedText': readable(text, selection), 'Link': readable(accent, base),
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


def stylesheet(skin, custom=False):
    radius, padding = {'standard': (4, '4px 8px'), 'rounded': (10, '6px 10px'), 'compact': (2, '2px 5px')}[skin]
    # Palette roles also cover dialogs and widgets created after switching.
    sheet = f"""
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
    if custom:
        sheet += """
        QMainWindow::separator { background: palette(window); width: 5px; height: 5px; }
        QMainWindow::separator:hover { background: palette(mid); }
        QPushButton:hover, QToolButton:hover, QComboBox:hover { background: palette(midlight); border-color: palette(link); }
        QPushButton:default { border-color: palette(link); }
        QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: palette(link); }
        QHeaderView::section { border: none; border-right: 1px solid palette(mid); border-bottom: 1px solid palette(mid); }
        QDockWidget::title { background: palette(window); color: palette(window-text); padding: 6px; }
        QTabBar::tab { margin: 2px; }
        QTabBar::tab:selected { background: palette(highlight); color: palette(highlighted-text); }
        QMenu { padding: 4px; }
        QMenu::item { padding: 6px 24px; border-radius: 3px; }
        QScrollBar:vertical { background: palette(base); width: 12px; margin: 2px; }
        QScrollBar:horizontal { background: palette(base); height: 12px; margin: 2px; }
        QScrollBar::handle { background: palette(mid); border-radius: 4px; }
        QScrollBar::handle:vertical { min-height: 24px; }
        QScrollBar::handle:horizontal { min-width: 24px; }
        QScrollBar::handle:hover { background: palette(placeholder-text); }
        QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }
        QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
        """
    return sheet


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
            self.app.setStyleSheet(self.original_sheet + (stylesheet(self.skin, custom=self.theme != 'system') if self._styled else ''))
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
