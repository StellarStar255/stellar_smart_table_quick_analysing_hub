#!/usr/bin/env python3
"""Render all appearance combinations with isolated settings and synthetic data.

Use --platform cocoa for native macOS verification. No installed app is replaced.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--platform', default='offscreen')
parser.add_argument('--output-dir', type=Path, required=True)
args = parser.parse_args()
os.environ['QT_QPA_PLATFORM'] = args.platform
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout
from qtui import file_io, main_window, python_analysis, themes

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
args.output_dir.mkdir(parents=True, exist_ok=True)
report = {'platform': args.platform, 'checks': [], 'screenshots': []}


def capture(widget, name):
    QTest.qWait(100)
    path = args.output_dir / (name + '.png')
    assert widget.grab().save(str(path))
    report['screenshots'].append(path.name)


with tempfile.TemporaryDirectory(prefix='table-theme-qa-') as temp:
    root = Path(temp)
    settings = QSettings(str(root / 'ui.ini'), QSettings.Format.IniFormat)
    main_window._make_settings = lambda: settings
    main_window.FILE_CONFIG_PATH = str(root / 'files.json')
    file_io.RECENT_FILES_PATH = str(root / 'recent.json')
    python_analysis._CONFIG_DIR = str(root)
    python_analysis.LAST_CODE_FILE = str(root / 'last.py')
    python_analysis.PRESETS_FILE = str(root / 'presets.json')
    python_analysis.HISTORY_FILE = str(root / 'history.json')
    w = main_window.MainWindow(restore_session=False)
    w._update_check_timer.stop()
    w.resize(1280, 840)
    w.cell_preview_dock.hide()
    w.python_toolbar.hide()
    w.model.set_dataframe(pd.DataFrame({
        '商品 Product': ['苹果 Apple', '橙子 Orange', '梨 Pear', '葡萄 Grape'],
        '数量 Qty': [5., 2., 3., 6.], '单价 Price': [3., 4., 2., 3.],
        '金额 Amount': [0., 0., 0., 0.], '备注 Notes': ['', '', '', ''],
    }))
    for row in range(4):
        w.model.setData(w.model.index(row + 1, 3), f'=B{row + 1}*C{row + 1}')
    w.model.cell_colors[(0, 4)] = '#a04040'
    w.show(); w.activateWindow(); w.raise_(); app.processEvents()
    w.table.setCurrentIndex(w.model.index(2, 3))
    wb = w.formula_workbench
    wb.edit.setFocus(); wb.edit.selectAll(); QTest.keyClicks(wb.edit, '=SUM(B1:B4)')
    original = (w.model.df.copy(deep=True), dict(w.model.formulas), dict(w.model.cell_colors), len(w.model._undo_stack))
    for theme in themes.THEMES:
        w._theme_actions[theme].trigger()
        for skin in themes.SKINS:
            w._skin_actions[skin].trigger()
            wb.edit.setFocus()
            wb.edit.formula_assistant.refresh()
            assert not w.cell_preview_text.formula_assistant.panel.isVisible()
            assert w._theme_actions[theme].isChecked() and w._skin_actions[skin].isChecked()
            assert wb.edit.text() == '=SUM(B1:B4)' and wb.dirty
            pd.testing.assert_frame_equal(w.model.df, original[0])
            assert (w.model.formulas, w.model.cell_colors, len(w.model._undo_stack)) == original[1:]
            assert w.table.palette().base().color() == themes.theme_palette(theme).base().color()
            capture(w, f'{theme}-{skin}')
            report['checks'].append(f'{theme}/{skin}: live switch, draft, data, formatting, undo')
    for theme, skin in (('light', 'compact'), ('purple', 'rounded')):
        w.appearance.set_theme(theme); w.appearance.set_skin(skin)
        w.resize(800, 700); app.processEvents()
        wb.edit.setFocus(); wb.edit.selectAll(); QTest.keyClicks(wb.edit, '=SUMM(')
        wb.edit.formula_assistant.refresh()
        assert wb.edit.formula_assistant.panel.isVisible()
        capture(w, theme + '-narrow-correction')
    wb.cancel_bar()
    w.resize(1280, 840)
    for theme in ('light', 'purple'):
        w.appearance.set_theme(theme)
        w.open_python_analysis()
        analysis = w._analysis_win
        analysis.code_edit.setPlainText('# 主题适配 / Theme-aware syntax\ndef total(values):\n    return sum(values)\n\nresult = df.head(10)\nprint(total([1, 2, 3]))')
        analysis.resize(1120, 800); analysis.show(); app.processEvents()
        for pattern, fmt in analysis._highlighter._rules:
            assert themes.contrast(fmt.foreground().color(), app.palette().base().color()) >= 4.5
        capture(analysis, theme + '-python')
        analysis.hide()
        dialog = QDialog(w)
        dialog.setWindowTitle('主题预览 / Theme preview')
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('输入与对话框 / Inputs and dialogs'))
        layout.addWidget(QLineEdit('SUM(A1:A10)'))
        disabled = QLineEdit('禁用状态 / Disabled'); disabled.setEnabled(False)
        layout.addWidget(disabled)
        layout.addWidget(QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel))
        dialog.resize(460, 180); dialog.show(); capture(dialog, theme + '-dialog'); dialog.close()
    report['checks'].append('light/dark Python syntax and lazy dialogs')
    w.appearance.set_theme('system'); w.appearance.set_skin('standard')
    w._analysis_win.close()
    w.model.modified = False
    w.close(); app.processEvents()

(args.output_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(f"PASS: {len(report['checks'])} checks, {len(report['screenshots'])} screenshots ({args.platform})")
