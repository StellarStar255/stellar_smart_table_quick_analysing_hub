"""Internal packaged-application formula UI check using isolated synthetic data.

Called only by --smoke-formulas. The frozen executable runs the same imports,
widgets and keyboard paths that users run; it never opens real workbooks.
"""
import json
from pathlib import Path
import tempfile
import traceback

import pandas as pd
from PyQt6.QtCore import QSettings, QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (QApplication, QAbstractItemDelegate,
                            QDialogButtonBox, QMessageBox)


def run(output_dir):
    from . import main_window, file_io
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    from version import __version__
    report = {'version': __version__, 'checks': [], 'frozen': False}
    import sys
    report['frozen'] = bool(getattr(sys, 'frozen', False))
    original_settings = main_window._make_settings
    original_files = main_window.FILE_CONFIG_PATH
    original_recent = file_io.RECENT_FILES_PATH
    window = None
    with tempfile.TemporaryDirectory(prefix='smart-table-formula-smoke-') as temp:
        root = Path(temp)
        settings = QSettings(str(root / 'ui.ini'), QSettings.Format.IniFormat)
        main_window._make_settings = lambda: settings
        main_window.FILE_CONFIG_PATH = str(root / 'files.json')
        file_io.RECENT_FILES_PATH = str(root / 'recent.json')
        try:
            window = main_window.MainWindow(restore_session=False)
            window._update_check_timer.stop()
            window.python_toolbar.hide()
            window.cell_preview_dock.hide()
            window.model.set_dataframe(pd.DataFrame({'数量': [2., 3.], '单价': [5., 6.], '金额': [0., 0.]}))
            window.resize(1100, 760); window.show(); window.activateWindow(); window.raise_()
            index = window.model.index(1, 2)
            window.table.setCurrentIndex(index); window.table.setFocus(); app.processEvents()
            wb = window.formula_workbench
            QTest.keyClicks(window.table, '=')
            editor = window._cell_delegate.active_editor
            QTest.keyClicks(editor, 'SUMM', delay=40); QTest.qWait(220)
            helper = editor.formula_assistant
            assert helper.panel.isVisible() and helper.list.count()
            assert helper.list.currentItem().data(Qt.ItemDataRole.UserRole) == 'SUM'
            assert helper.diagnostic.code == 'function'
            helper.panel.grab().save(str(output / 'packaged-live-correction.png'))
            report['checks'].append('real-keyboard automatic completion and spelling correction')
            QTest.keyClick(editor, Qt.Key.Key_Tab)
            assert editor.text() == '=SUM('
            QTest.keyClicks(editor, 'A2:B2)'); QTest.keyClick(editor, Qt.Key.Key_Return)
            app.processEvents(); assert window.model.df.iat[0, 2] == 7
            window.table.setCurrentIndex(index)
            wb.edit.setFocus(); wb.edit.selectAll(); QTest.keyClicks(wb.edit, '=A2*B2')
            for _ in range(100):
                QTest.qWait(20)
                if wb._job is None and not wb.timer.isActive(): break
            assert '10' in wb.result.text() and window.model.df.iat[0, 2] == 7
            QTest.qWait(150)
            window.grab().save(str(output / 'packaged-formula-bar-preview.png'))
            QTest.keyClick(wb.edit, Qt.Key.Key_Return)
            assert window.model.df.iat[0, 2] == 10
            report['checks'].append('formula bar non-mutating preview and explicit commit')
            wb.open_wizard(); dialog = wb.wizard
            dialog.function.setCurrentIndex(dialog.function.findData('SUM'))
            wb.start_pick(dialog.fields[0])
            start = window.table.visualRect(window.model.index(1, 0)).center()
            end = window.table.visualRect(window.model.index(2, 1)).center()
            QTest.mousePress(window.table.viewport(), Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(window.table.viewport(), end)
            QTest.mouseRelease(window.table.viewport(), Qt.MouseButton.LeftButton, pos=end)
            assert dialog.isVisible() and dialog.fields[0].text() == 'A2:B3'
            dialog.grab().save(str(output / 'packaged-argument-wizard.png'))
            dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).click()
            assert wb.edit.text() == '=SUM(A2:B3)' and window.model.df.iat[0, 2] == 10
            wb.commit_bar(); assert window.model.df.iat[0, 2] == 16
            report['checks'].append('argument wizard actual mouse range picking')
            window.model.setData(window.model.index(1, 0), '=1/0')
            window.model.setData(index, '=A2+1')
            window.table.setCurrentIndex(index); window._refresh_formula_feedback()
            assert 'href="1,0"' in window.formula_error_label.text()
            window.formula_error_label.linkActivated.emit('1,0')
            assert window.table.currentIndex() == window.model.index(1, 0)
            wb.return_to_error(); assert window.table.currentIndex() == index
            report['checks'].append('error target navigation and return')
            wb.edit.setFocus(); wb.edit.selectAll(); QTest.keyClicks(wb.edit, '=SUM(')
            answered = []
            def keep_editing():
                box = app.activeModalWidget()
                assert isinstance(box, QMessageBox)
                answered.append(True)
                box.grab().save(str(output / 'packaged-draft-confirmation.png'))
                box.defaultButton().click()
            QTimer.singleShot(60, keep_editing)
            assert not wb.resolve_pending()
            assert answered and wb.dirty and window.model.formulas[(0, 2)] == '=A2+1'
            report['checks'].append('unfinished draft confirmation preserves previous value')
            from .themes import THEMES, SKINS, theme_palette
            before = (window.model.df.copy(deep=True), dict(window.model.formulas),
                      dict(window.model.cell_colors), len(window.model._undo_stack))
            for theme in THEMES:
                window._theme_actions[theme].trigger()
                for skin in SKINS:
                    window._skin_actions[skin].trigger()
                    app.processEvents()
                    assert window.appearance.theme == theme and window.appearance.skin == skin
                    assert window.table.palette().base().color() == theme_palette(theme).base().color()
                    assert wb.dirty and wb.edit.text() == '=SUM('
                    pd.testing.assert_frame_equal(window.model.df, before[0])
                    assert (window.model.formulas, window.model.cell_colors, len(window.model._undo_stack)) == before[1:]
                    assert not window.cell_preview_text.formula_assistant.panel.isVisible()
                if theme in ('light', 'purple'):
                    window.grab().save(str(output / ('packaged-theme-' + theme + '.png')))
            window.appearance.set_theme('system')
            window.appearance.set_skin('standard')
            report['checks'].append('18 theme/skin combinations preserve draft, data, formatting and undo')
            report['status'] = 'passed'
            print('PASS packaged formula UI:', json.dumps(report, ensure_ascii=False))
            return 0
        except Exception:
            report['status'] = 'failed'
            report['error'] = traceback.format_exc()
            print(report['error'], file=sys.stderr)
            return 1
        finally:
            if window is not None:
                wb = window.formula_workbench
                if wb.wizard: wb.wizard.reject()
                editor = window._cell_delegate.active_editor
                if editor: window.table.closeEditor(editor, QAbstractItemDelegate.EndEditHint.RevertModelCache)
                wb.cancel_bar(); window._cancel_preview_formula(); window._preview_save_timer.stop()
                window.model.modified = False; window.close(); app.processEvents()
            (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            main_window._make_settings = original_settings
            main_window.FILE_CONFIG_PATH = original_files
            file_io.RECENT_FILES_PATH = original_recent
