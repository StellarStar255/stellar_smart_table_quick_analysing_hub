#!/usr/bin/env python3
"""Render formula interactions with synthetic data and isolated application state.

Use --platform cocoa for a native macOS pass, or run under Xvfb with xcb on Linux.
No real workbooks, recent-file lists or release installations are modified.
"""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output-dir', type=Path, required=True)
parser.add_argument('--platform', default='offscreen')
args = parser.parse_args()
os.environ['QT_QPA_PLATFORM'] = args.platform
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from PyQt6.QtCore import Qt, QSettings, QTimer
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QAbstractItemDelegate
from qtui import main_window, file_io
from qtui.formula_assistant import FormulaLibrary
from core import formula_help

app = QApplication([])
app.setStyle('Fusion')
app.setQuitOnLastWindowClosed(False)
args.output_dir.mkdir(parents=True, exist_ok=True)
normal_palette = QPalette()
for role, color in (
    ('Window', '#f4f4f4'), ('WindowText', '#202020'), ('Base', '#ffffff'),
    ('AlternateBase', '#f5f5f5'), ('Text', '#202020'), ('Button', '#eeeeee'),
    ('ButtonText', '#202020'), ('Highlight', '#2674d9'),
    ('HighlightedText', '#ffffff'), ('Mid', '#aaaaaa'), ('Midlight', '#e5e5e5'),
    ('PlaceholderText', '#666666')):
    normal_palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
results = []


def dark_palette():
    p = QPalette()
    for role, color in (
        ('Window', '#252525'), ('WindowText', '#eeeeee'), ('Base', '#1e1e1e'),
        ('AlternateBase', '#292929'), ('Text', '#eeeeee'), ('Button', '#303030'),
        ('ButtonText', '#eeeeee'), ('Highlight', '#366fba'),
        ('HighlightedText', '#ffffff'), ('Mid', '#666666'), ('Midlight', '#404040'),
        ('PlaceholderText', '#aaaaaa')):
        p.setColor(getattr(QPalette.ColorRole, role), QColor(color))
    return p


with tempfile.TemporaryDirectory(prefix='table-formula-ui-') as temp:
    root = Path(temp)
    settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
    main_window._make_settings = lambda: settings
    main_window.FILE_CONFIG_PATH = str(root / 'files.json')
    file_io.RECENT_FILES_PATH = str(root / 'recent.json')
    w = main_window.MainWindow(restore_session=False)
    w._update_check_timer.stop()
    w.python_toolbar.hide()
    w.cell_preview_dock.hide()
    w.model.set_dataframe(pd.DataFrame({
        '商品': ['苹果', '橙子', '梨', '苹果'],
        '数量': [5., 2., 3., 6.], '单价': [3., 4., 2., 3.],
        '金额': [0., 0., 0., 0.], '备注': ['', '', '', ''],
    }))
    w.show()
    app.processEvents()

    def close_editor():
        editor = w._cell_delegate.active_editor
        if editor:
            w.table.closeEditor(editor, QAbstractItemDelegate.EndEditHint.RevertModelCache)
        app.processEvents()

    def edit(text):
        close_editor()
        w.activateWindow()
        w.raise_()
        app.processEvents()
        index = w.model.index(1, 3)
        w.table.setCurrentIndex(index)
        w.table.edit(index)
        app.processEvents()
        editor = w._cell_delegate.active_editor
        editor.setText(text)
        editor.setCursorPosition(len(text))
        editor.setFocus()
        app.processEvents()
        editor.formula_assistant.refresh()
        return editor, editor.formula_assistant

    def type_formula(text):
        close_editor()
        w.activateWindow()
        w.raise_()
        w.table.setCurrentIndex(w.model.index(1, 3))
        w.table.setFocus()
        app.processEvents()
        QTest.keyClicks(w.table, '=')
        editor = w._cell_delegate.active_editor
        QTest.keyClicks(editor, text[1:], delay=15)
        QTest.qWait(180)
        return editor, editor.formula_assistant

    try:
        for language in ('zh', 'en'):
            formula_help.current_language = lambda lang=language: lang
            for theme, palette in (('light', normal_palette), ('dark', dark_palette())):
                app.setPalette(palette)
                for width, height in ((1120, 740), (560, 460)):
                    w.resize(width, height)
                    app.processEvents()
                    prefix = f'{language}-{theme}-{width}'
                    editor, helper = type_formula('=SU')
                    assert helper.list.count() == 3
                    assert helper.panel.isVisible()
                    assert w.rect().contains(helper.panel.geometry())
                    assert not helper.panel.isWindow()
                    QTest.qWait(150)
                    assert helper.panel.isVisible() and editor.hasFocus()
                    helper.panel.grab().save(str(args.output_dir / f'{prefix}-completion.png'))
                    QTest.keyClick(editor, Qt.Key.Key_Tab)
                    assert editor.text() == '=SUM('
                    editor.insert('B2:B5)')
                    QTest.keyClick(editor, Qt.Key.Key_Return)
                    assert w.model.df.iat[0, 3] == 16
                    editor, helper = edit('=IF(B2>0,SUM(B2:C4),0)')
                    editor.setCursorPosition(18)
                    helper.refresh()
                    assert len(w.table._formula_reference_spans) == 2
                    helper.panel.grab().save(str(args.output_dir / f'{prefix}-arguments.png'))
                    w.grab().save(str(args.output_dir / f'{prefix}-references.png'))
                    editor, helper = type_formula('=SUMM')
                    assert helper.panel.isVisible()
                    assert helper.diagnostic.code == 'function'
                    assert helper.list.currentItem().data(Qt.ItemDataRole.UserRole) == 'SUM'
                    helper.panel.grab().save(str(args.output_dir / f'{prefix}-error.png'))
                    helper.apply_fix()
                    assert editor.text() == '=SUM('
                    editor.insert('B2:B5)')
                    QTest.keyClick(editor, Qt.Key.Key_Return)
                    assert w.model.formulas[(0, 3)] == '=SUM(B2:B5)'
                    close_editor()
                    library = FormulaLibrary(w)
                    library.search.setText('XLOOKUP')
                    library.show()
                    app.processEvents()
                    assert library.list.count() == 1
                    library.grab().save(str(args.output_dir / f'{prefix}-library.png'))
                    library.close()
                    results.append(prefix)
        # A large DataFrame is deliberately not needed: analysis never touches it.
        source = '=SUM(' + ','.join('B2' for _ in range(1000)) + ')'
        start = time.perf_counter()
        assert formula_help.diagnose(source, final=True) is None
        elapsed = (time.perf_counter() - start) * 1000
        print(f'PASS: {len(results)} language/theme/size scenarios; 1,000-reference analysis {elapsed:.1f} ms')
    finally:
        close_editor()
        w._cancel_preview_formula()
        w.model.modified = False
        w.close()
