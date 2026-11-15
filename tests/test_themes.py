"""Appearance switches must never be workbook edits or destroy active drafts."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QPalette, QTextDocument
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QAbstractItemDelegate, QDialog, QLineEdit, QStyleFactory

from qtui import file_io, main_window, themes
from qtui.python_analysis import PythonHighlighter

app = QApplication.instance() or QApplication([])


@pytest.fixture
def appearance(tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / 'theme.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(main_window, '_make_settings', lambda: settings)
    monkeypatch.setattr(main_window, 'FILE_CONFIG_PATH', str(tmp_path / 'files.json'))
    monkeypatch.setattr(file_io, 'RECENT_FILES_PATH', str(tmp_path / 'recent.json'))
    previous = getattr(app, '_appearance_manager', None)
    palette, sheet, style = QPalette(app.palette()), app.styleSheet(), app.style().objectName()
    scheme = app.styleHints().colorScheme()
    m = themes.ThemeManager(app, settings)
    app._appearance_manager = m
    yield m
    for w in list(m.windows):
        editor = w._cell_delegate.active_editor
        if editor:
            w.table.closeEditor(editor, QAbstractItemDelegate.EndEditHint.RevertModelCache)
        w.formula_workbench.cancel_bar()
        w._cancel_preview_formula()
        w.model.modified = False
        w.close()
    app.styleHints().colorSchemeChanged.disconnect(m._system_changed)
    app.setStyleSheet('')
    app.setStyle(QStyleFactory.create(style))
    app.styleHints().setColorScheme(scheme)
    app.setPalette(palette)
    app.setStyleSheet(sheet)
    if previous is None:
        del app._appearance_manager
    else:
        app._appearance_manager = previous
    m.deleteLater()
    app.processEvents()


def window():
    w = main_window.MainWindow(restore_session=False)
    w._update_check_timer.stop()
    w._initial_load_timer.stop()
    w.resize(1100, 760)
    w.show()
    app.processEvents()
    return w


@pytest.mark.parametrize('key', themes.THEMES)
def test_palette_readability(key):
    p = themes.theme_palette(key)
    for fg, bg in (('Text', 'Base'), ('WindowText', 'Window'), ('ButtonText', 'Button'),
                   ('HighlightedText', 'Highlight'), ('PlaceholderText', 'Base'), ('Link', 'Base')):
        assert themes.contrast(p.color(getattr(QPalette.ColorRole, fg)),
                               p.color(getattr(QPalette.ColorRole, bg))) >= 4.5
    for color in ('#2674d9', '#c05a24', '#9b45b6', '#168568', '#bd4775', '#7772b8'):
        assert themes.contrast(themes.readable(color, p.base().color()), p.base().color()) >= 4.5


def test_menus_sync_and_new_window_inherits(appearance):
    a, b = window(), window()
    a._theme_actions['purple'].trigger()
    a._skin_actions['rounded'].trigger()
    assert appearance.theme == 'purple' and appearance.skin == 'rounded'
    c = window()
    for w in (a, b, c):
        assert w._theme_actions['purple'].isChecked()
        assert w._skin_actions['rounded'].isChecked()
        assert sum(x.isChecked() for x in w._theme_actions.values()) == 1
        assert w.table.palette().base().color() == themes.theme_palette('purple').base().color()
    dialog = QDialog(b)
    edit = QLineEdit(dialog)
    assert edit.palette().text().color() == app.palette().text().color()
    assert appearance.settings.value('appearance/theme') == 'purple'
    assert appearance.settings.value('appearance/skin') == 'rounded'


def test_switch_preserves_workbook_draft_selection_and_zoom(appearance):
    w = window()
    w.model.set_dataframe(pd.DataFrame({'A': [2., 3.], 'B': [0., 0.]}))
    w.model.setData(w.model.index(1, 1), '=A1*2')
    w.model.cell_colors[(0, 1)] = '#ffff00'
    w.model.cell_colors[(-1, 0)] = '#335577'
    w.table.setCurrentIndex(w.model.index(2, 1))
    wb = w.formula_workbench
    wb.edit.setFocus()
    wb.edit.selectAll()
    wb.edit.insert('=SUM(A1:A2)+')
    wb.edit.setCursorPosition(5)
    before = (w.model.df.copy(deep=True), dict(w.model.formulas), dict(w.model.cell_colors),
              list(w.model._undo_stack), w.model.modified, wb.edit.text(), wb.edit.cursorPosition())
    w._zoom = 1.3
    spy = QSignalSpy(w.model.dataChanged)
    for key in themes.THEMES:
        appearance.set_theme(key)
        for skin in themes.SKINS:
            appearance.set_skin(skin)
            app.processEvents()
            pd.testing.assert_frame_equal(w.model.df, before[0])
            assert w.model.formulas == before[1] and w.model.cell_colors == before[2]
            assert w.model._undo_stack == before[3] and w.model.modified == before[4]
            assert wb.edit.text() == before[5] and wb.edit.cursorPosition() == before[6]
            assert wb.dirty and w.table.currentIndex() == w.model.index(2, 1)
            assert w._zoom == 1.3
            assert w.table.verticalHeader().defaultSectionSize() == int(w._base_row_height * 1.3 * (.82 if skin == 'compact' else 1.0)), (key, skin, w._appearance_density, w.table.verticalHeader().minimumSectionSize())
            formula = w.model.data(w.model.index(1, 1), Qt.ItemDataRole.ForegroundRole)
            assert themes.contrast(formula, '#ffff00') >= 4.5
    assert len(spy) == 0
    wb.edit.undo()
    assert wb.edit.text() != before[5]  # the editor undo history also survives


def test_native_editor_survives_and_references_remain_readable(appearance):
    w = window()
    w.table.setCurrentIndex(w.model.index(1, 0))
    w.table.edit(w.model.index(1, 0))
    app.processEvents()
    editor = w._cell_delegate.active_editor
    editor.setText('=SUM(B1:B3)')
    editor.setCursorPosition(7)
    for key in ('midnight', 'light', 'pink', 'system'):
        appearance.set_theme(key)
        app.processEvents()
        assert w._cell_delegate.active_editor is editor
        assert editor.text() == '=SUM(B1:B3)' and editor.cursorPosition() == 7
        editor.formula_assistant.highlight_references()
        for _, color in w.table._formula_reference_spans:
            assert themes.contrast(color, editor.palette().base().color()) >= 4.45


def test_hidden_preview_never_exposes_its_panel(appearance):
    w = window()
    w.cell_preview_dock.hide()
    inactive = w.cell_preview_text.formula_assistant
    for theme in ('purple', 'light', 'midnight'):
        appearance.set_theme(theme)
        app.processEvents()
        assert not inactive.panel.isVisible()
    w.hide(); w.show(); app.processEvents()
    assert not inactive.panel.isVisible()


def test_restore_defaults_and_saved_startup(appearance):
    w = window()
    original = QPalette(app.palette())
    appearance.set_theme('amber')
    appearance.set_skin('compact')
    settings_path = appearance.settings.fileName()
    appearance.settings.sync()
    saved = QSettings(settings_path, QSettings.Format.IniFormat)
    fresh = themes.ThemeManager(app, saved)
    assert fresh.theme == 'amber' and fresh.skin == 'compact'
    app.styleHints().colorSchemeChanged.disconnect(fresh._system_changed)
    fresh.deleteLater()
    appearance.set_theme('system')
    appearance.set_skin('standard')
    assert app.palette().base().color() == original.base().color()
    assert app.styleSheet() == appearance.original_sheet
    assert w.table.verticalHeader().defaultSectionSize() == w._base_row_height
    with pytest.raises(ValueError):
        appearance.set_theme('missing')
    assert appearance.theme == 'system'


def test_python_syntax_tracks_palette(appearance):
    doc = QTextDocument('def calculate():\n    return sum([1, 2]) # comment')
    h = PythonHighlighter(doc)
    for theme in ('light', 'midnight', 'amber'):
        appearance.set_theme(theme)
        h.apply_palette()
        h.rehighlight()
        block = doc.begin()
        while block.isValid():
            for span in block.layout().formats():
                assert themes.contrast(span.format.foreground().color(), app.palette().base().color()) >= 4.5
            block = block.next()


def test_invalid_saved_preferences_fall_back(appearance, tmp_path):
    settings = QSettings(str(tmp_path / 'invalid.ini'), QSettings.Format.IniFormat)
    settings.setValue('appearance/theme', 'removed-theme')
    settings.setValue('appearance/skin', 'removed-skin')
    m = themes.ThemeManager(app, settings)
    assert m.theme == 'system' and m.skin == 'standard'
    app.styleHints().colorSchemeChanged.disconnect(m._system_changed)
    m.deleteLater()


def test_older_qt_without_native_scheme_override():
    # Qt 6.6 still receives the custom palette/QSS, without the newer native API.
    themes.set_color_scheme(object(), Qt.ColorScheme.Dark)
