"""Formula guidance must explain real engine behavior without evaluating cells."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import Qt, QEvent, QPoint, QSettings
from PyQt6.QtGui import QKeyEvent, QInputMethodEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog

from core.formula_engine import FormulaEngine
from core.formula_help import (
    FUNCTIONS, diagnose, active_call, completion_span, reference_tokens,
    search_functions,
)
from qtui.main_window import MainWindow
from qtui.formula_assistant import FormulaLibrary

_app = QApplication.instance() or QApplication([])


class TestAnalysis:
    def test_catalogue_matches_engine(self):
        assert set(FUNCTIONS) == set(FormulaEngine.FUNC_MAP)
        for spec in FUNCTIONS.values():
            assert spec.description and spec.example.startswith('=')
            assert diagnose(spec.example, final=True) is None, spec.name

    @pytest.mark.parametrize('formula', [
        '=SUM(B2:B5)', '=IF(SUM(A2:A5)>10,"a,b","a""b")',
        '=IFERROR(1/0,0)', '=2^3+10%', '=1E5+2',
        '=CONCAT("A2", "it\'s")', "=CONCAT('hello',A2)",
        '=SUM($A$2:B4)', '=SUM()', '=TODAY()', '=IF(TRUE,#REF!,0)',
    ])
    def test_valid_formulas(self, formula):
        assert diagnose(formula, final=True) is None

    def test_cursor_context_handles_nested_calls_and_strings(self):
        text = '=IF(A2>0,SUM(B2,B3),"a,b")'
        call, arg = active_call(text, text.index('B3') + 1)
        assert (call.name, arg) == ('SUM', 1)
        call, arg = active_call(text, text.index('"a,b"') + 3)
        assert (call.name, arg) == ('IF', 2)
        call, arg = active_call('=SUM((A2+B2),C2)', 13)
        assert call.name == 'SUM' and arg == 1

    @pytest.mark.parametrize('text,cursor', [('="SU', 4), ('="SUM"', 3), ('=A2', 2), ('=$A', 3), ('=A:A', 4), ('=1E', 3)])
    def test_no_completion_inside_strings_or_references(self, text, cursor):
        assert completion_span(text, cursor) is None

    def test_replace_only_function_token(self):
        text = '=IF(A2,SUM(B2),0)'
        span = completion_span(text, text.index('SUM') + 2)
        assert text[span[0]:span[1]] == 'SUM'
        assert completion_span('=', 1) == (1, 1, '')

    @pytest.mark.parametrize('formula,code,replacement', [
        ('=SUMM(B2:B10)', 'function', 'SUM'),
        ('=SUM(B2:B10', 'parenthesis', ')'),
        ('=SUM(B2))', 'parenthesis', ''),
        ('=IF(B2>0,1)', 'arguments', None),
        ('=XLOOKUP(A2,B2:B4,C2:C4,0,0,1)', 'arguments', None),
        ('=SUMIFS(A2:A4,B2:B4)', 'arguments', None),
        ('=COUNTIFS(A2:A4,1,B2:B4)', 'arguments', None),
        ('=SUM(A2，B2)', 'punctuation', ','),
        ('=SUM(A2,)', 'arguments', None),
        ('=A2+', 'syntax', None),
        ('=hello', 'name', None),
        ('="oops', 'quote', None),
    ])
    def test_actionable_diagnostics(self, formula, code, replacement):
        d = diagnose(formula, final=True)
        assert d.code == code
        assert d.replacement == replacement
        assert 0 <= d.start <= d.end <= len(formula)

    def test_incomplete_is_gentle_and_strings_are_untouched(self):
        assert diagnose('=SUM(').incomplete
        assert diagnose('=A2+') is None
        assert diagnose('=CONCAT("中文，（）",A2)', final=True) is None
        assert [t.text for t in reference_tokens('=CONCAT("A2",$B$3,C2:D4)')] == ['$B$3', 'C2:D4']

    def test_library_search(self):
        assert 'SUM' in [s.name for s in search_functions('求和')]
        assert 'XLOOKUP' in [s.name for s in search_functions('lookup')]
        assert all(s.category == 'text' for s in search_functions('', 'text'))


@pytest.fixture
def win(tmp_path, monkeypatch):
    from qtui import main_window, file_io
    settings = QSettings(str(tmp_path / 'ui.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(main_window, '_make_settings', lambda: settings)
    monkeypatch.setattr(main_window, 'FILE_CONFIG_PATH', str(tmp_path / 'files.json'))
    monkeypatch.setattr(file_io, 'RECENT_FILES_PATH', str(tmp_path / 'recent.json'))
    w = MainWindow()
    w._update_check_timer.stop()
    w.cell_preview_dock.show()
    w.model.set_dataframe(pd.DataFrame({'A': [1., 2., 3.], 'B': [2., 0., 4.], 'C': [0., 0., 0.]}))
    w.resize(1100, 720)
    w.show()
    _app.processEvents()
    yield w
    editor = w._cell_delegate.active_editor
    if editor:
        w.table.closeEditor(editor, w.table.itemDelegate().EndEditHint.RevertModelCache)
    w._cancel_preview_formula()
    w._preview_save_timer.stop()
    w.model.modified = False
    w.close()
    _app.processEvents()


def edit(w, text):
    idx = w.model.index(1, 2)
    w.table.setCurrentIndex(idx)
    w.table.edit(idx)
    _app.processEvents()
    editor = w._cell_delegate.active_editor
    editor.setFocus()
    editor.setText(text)
    editor.setCursorPosition(len(text))
    editor.formula_assistant.refresh()
    return editor, editor.formula_assistant


class TestEditor:
    def test_tab_accepts_then_enter_commits(self, win):
        editor, helper = edit(win, '=SU')
        assert helper.list.count() == 3
        QTest.keyClick(editor, Qt.Key.Key_Tab)
        assert editor.text() == '=SUM('
        assert win.model.formulas == {}
        editor.insert('A2:B2)')
        QTest.keyClick(editor, Qt.Key.Key_Return)
        _app.processEvents()
        assert win.model.formulas[(0, 2)] == '=SUM(A2:B2)'
        assert win.model.df.iat[0, 2] == 3
        assert win.table.currentIndex().row() == 2

    def test_invalid_enter_keeps_editor_and_original_value(self, win):
        editor, helper = edit(win, '=SUMM(A2:B2)')
        QTest.keyClick(editor, Qt.Key.Key_Return)
        assert win._cell_delegate.active_editor is editor
        assert helper.diagnostic.code == 'function'
        assert win.model.df.iat[0, 2] == 0
        helper.apply_fix()
        assert editor.text() == '=SUM(A2:B2)'
        QTest.keyClick(editor, Qt.Key.Key_Return)
        assert win.model.df.iat[0, 2] == 3

    def test_missing_parenthesis_fix_and_escape(self, win):
        editor, helper = edit(win, '=SUM(A2:B2')
        QTest.keyClick(editor, Qt.Key.Key_Return)
        assert helper.diagnostic.code == 'parenthesis'
        helper.apply_fix()
        assert editor.text() == '=SUM(A2:B2)'
        editor.setText('=SU')
        helper.refresh()
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        assert not helper.panel.isVisible()
        assert win._cell_delegate.active_editor is editor
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        _app.processEvents()
        assert win.model.formulas == {}

    def test_existing_parenthesis_and_native_undo(self, win):
        editor, helper = edit(win, '=IF(A2,SUM(B2),0)')
        editor.setCursorPosition(10)  # after SU, before M
        helper.refresh()
        helper.accept_completion()
        assert editor.text() == '=IF(A2,SUM(B2),0)'
        editor.setText('=SUMM(A2)')
        helper.refresh()
        helper.apply_fix()
        assert editor.text() == '=SUM(A2)'
        editor.undo()
        assert editor.text() == '=SUMM(A2)'

    def test_runtime_error_commits_and_explains(self, win):
        editor, helper = edit(win, '=A2/B3')
        QTest.keyClick(editor, Qt.Key.Key_Return)
        _app.processEvents()
        assert win.model.df.iat[0, 2] == '#DIV/0!'
        win.table.setCurrentIndex(win.model.index(1, 2))
        assert win.formula_feedback.isVisible()
        assert '#DIV/0!' in win.formula_error_label.text()
        tooltip = win.model.data(win.model.index(1, 2), Qt.ItemDataRole.ToolTipRole)
        assert '=A2/B3' in tooltip and len(tooltip) > 30
        win.model.undo()
        assert not win.formula_feedback.isVisible()

    def test_point_selection_and_f4_survive(self, win):
        editor, helper = edit(win, '=SUM(')
        assert win.table._insert_point_ref(editor, win.model.index(1, 0))
        assert editor.text() == '=SUM(A2'
        QTest.keyClick(editor, Qt.Key.Key_F4)
        assert editor.text() == '=SUM($A$2'
        helper.refresh()
        assert win.table._formula_reference_spans[0][0] == '$A$2'

    def test_ime_composition_hides_panel(self, win):
        editor, helper = edit(win, '=SU')
        _app.sendEvent(editor, QInputMethodEvent('求', []))
        assert helper.composing and not helper.panel.isVisible()
        _app.sendEvent(editor, QInputMethodEvent('', []))
        assert not helper.composing

    def test_library_metadata_search_and_insertion(self, win):
        dialog = FormulaLibrary(win)
        dialog.search.setText('XLOOKUP')
        assert dialog.list.count() == 1
        assert 'search_mode' in dialog.details.toPlainText()
        editor, helper = edit(win, '=IF(A2,')
        helper.insert_function('SUM')
        assert editor.text() == '=IF(A2,SUM('
        assert 'SUM' in helper.help.text()
        dialog.close()

    def test_completion_panel_is_on_screen(self, win):
        win.resize(520, 420)
        editor, helper = edit(win, '=SU')
        assert editor.screen().availableGeometry().contains(helper.panel.frameGeometry())


class TestPreview:
    def test_formula_typing_waits_for_commit_and_uses_shared_helper(self, win):
        win.table.setCurrentIndex(win.model.index(1, 2))
        preview = win.cell_preview_text
        preview.setFocus()
        preview.setPlainText('=SU')
        preview.setCursorPosition(3)
        preview.formula_assistant.refresh()
        assert not win._preview_save_timer.isActive()
        assert win.model.df.iat[0, 2] == 0
        QTest.keyClick(preview, Qt.Key.Key_Tab)
        assert preview.toPlainText() == '=SUM('
        preview.insertPlainText('A2:B2)')
        QTest.keyClick(preview, Qt.Key.Key_Return)
        assert win.model.df.iat[0, 2] == 3
        assert not win._preview_formula_pending

    def test_invalid_preview_enter_preserves_text_and_escape_restores(self, win):
        win.table.setCurrentIndex(win.model.index(1, 2))
        preview = win.cell_preview_text
        preview.setFocus()
        preview.setPlainText('=IF(1,2)')
        preview.setCursorPosition(8)
        QTest.keyClick(preview, Qt.Key.Key_Return)
        assert win.model.df.iat[0, 2] == 0
        assert preview.toPlainText() == '=IF(1,2)'
        QTest.keyClick(preview, Qt.Key.Key_Escape)
        QTest.keyClick(preview, Qt.Key.Key_Escape)
        assert not win._preview_formula_pending
        assert preview.toPlainText() == '0'

    def test_preview_context_change_preserves_invalid_formula_source(self, win, monkeypatch):
        monkeypatch.setattr(win.formula_workbench, "ask_draft", lambda _: "store")
        win.table.setCurrentIndex(win.model.index(1, 2))
        win.cell_preview_text.setPlainText('=SUMM(A2)')
        win.table.setCurrentIndex(win.model.index(2, 2))
        assert win.model.formulas[(0, 2)] == '=SUMM(A2)'
        assert win.model.df.iat[0, 2] == '#NAME?'
        win.table.setCurrentIndex(win.model.index(1, 2))
        assert win.cell_preview_text.toPlainText() == '=SUMM(A2)'

    def test_reading_formula_does_not_open_popup(self, win):
        win.model.setData(win.model.index(1, 2), '=SUM(A2:B2)')
        win.table.setFocus()
        win.table.setCurrentIndex(win.model.index(1, 2))
        win.cell_preview_text.formula_assistant.refresh()
        assert not win.cell_preview_text.formula_assistant.panel.isVisible()


class TestLibraryFocus:
    def test_f1_library_does_not_commit_partial_formula(self, win, monkeypatch):
        editor, helper = edit(win, '=SU')
        def choose(dialog):
            assert win._cell_delegate.active_editor is editor
            assert win.model.formulas == {}
            dialog.selected_function = 'SUM'
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(FormulaLibrary, 'exec', choose)
        QTest.keyClick(editor, Qt.Key.Key_F1)
        assert editor.text() == '=SUM('
        assert win.model.formulas == {}

    def test_mouse_accepts_without_committing(self, win):
        editor, helper = edit(win, '=SU')
        item = helper.list.item(0)
        QTest.mouseClick(helper.list.viewport(), Qt.MouseButton.LeftButton,
                         pos=helper.list.visualItemRect(item).center())
        assert editor.text() == '=SUM('
        assert win.model.formulas == {}


class TestErrorContext:
    def test_circular_path_names_cells(self, win):
        win.model.setData(win.model.index(1, 0), '=B2')
        win.model.setData(win.model.index(1, 1), '=A2')
        message = win.model.formula_error_details(0, 0)
        assert 'A2' in message and 'B2' in message and '→' in message

    def test_range_self_reference(self, win):
        win.model.setData(win.model.index(1, 0), '=SUM(A2:A4)')
        assert 'A2 → A2' in win.model.formula_error_details(0, 0)

    def test_upstream_error_is_identified(self, win):
        win.model.setData(win.model.index(1, 0), '=1/0')
        win.model.setData(win.model.index(1, 1), '=A2+1')
        assert 'A2' in win.model.formula_error_details(0, 1)

    def test_typing_analysis_never_evaluates_cells(self, win, monkeypatch):
        editor, helper = edit(win, '=SUM(A2:B3)')
        def forbidden(*args, **kwargs):
            raise AssertionError('typing triggered recalculation')
        monkeypatch.setattr(win.model.engine, 'evaluate', forbidden)
        helper.refresh()
        assert helper.validate_commit()

    def test_chinese_purpose_can_be_completed(self, win):
        editor, helper = edit(win, '=求和')
        assert helper.list.count() >= 3
        helper.accept_completion()
        assert editor.text().startswith('=SUM(')


class TestReferenceEditing:
    def test_f4_ignores_literal_and_preserves_editor_undo(self, win):
        editor, helper = edit(win, '=CONCAT("A2",B2)')
        editor.setCursorPosition(10)
        QTest.keyClick(editor, Qt.Key.Key_F4)
        assert editor.text() == '=CONCAT("A2",B2)'
        editor.setCursorPosition(14)
        QTest.keyClick(editor, Qt.Key.Key_F4)
        assert editor.text() == '=CONCAT("A2",$B$2)'
        editor.undo()
        assert editor.text() == '=CONCAT("A2",B2)'

    def test_f4_handles_non_bmp_text_before_reference(self, win):
        editor, helper = edit(win, '=CONCAT("😀",B2)')
        editor.setCursorPosition(15)
        QTest.keyClick(editor, Qt.Key.Key_F4)
        assert editor.text() == '=CONCAT("😀",$B$2)'

    def test_mouse_fix_keeps_editor_open(self, win):
        editor, helper = edit(win, '=SUMM(A2)')
        QTest.keyClick(editor, Qt.Key.Key_Return)
        QTest.mouseClick(helper.fix, Qt.MouseButton.LeftButton)
        assert editor.text() == '=SUM(A2)'
        assert win._cell_delegate.active_editor is editor
        assert win.model.formulas == {}


class TestAutomaticPopup:
    """Real key events and timer delivery, without calling helper.refresh()."""
    def start_typing(self, win, text):
        win.activateWindow()
        win.table.setCurrentIndex(win.model.index(1, 2))
        win.table.setFocus()
        _app.processEvents()
        QTest.keyClicks(win.table, '=')
        editor = win._cell_delegate.active_editor
        QTest.keyClicks(editor, text, delay=15)
        QTest.qWait(180)
        return editor, editor.formula_assistant

    def test_prefix_automatically_opens_and_stays_open(self, win):
        editor, helper = self.start_typing(win, 'SU')
        assert helper.panel.isVisible() and helper.list.count() == 3
        assert helper.panel.parentWidget() is win
        assert not helper.panel.isWindow()
        QTest.qWait(350)
        assert helper.panel.isVisible() and editor.hasFocus()
        QTest.keyClick(editor, Qt.Key.Key_Tab)
        assert editor.text() == '=SUM('

    def test_typo_before_parenthesis_shows_choices_and_fix(self, win):
        editor, helper = self.start_typing(win, 'SUMM')
        assert helper.panel.isVisible()
        assert helper.list.currentItem().data(Qt.ItemDataRole.UserRole) == 'SUM'
        assert helper.diagnostic.code == 'function'
        assert helper.fix.isVisible()
        helper.apply_fix()
        assert editor.text() == '=SUM('
        assert not win.model.formulas

    def test_typo_can_be_corrected_with_tab_without_enter(self, win):
        editor, helper = self.start_typing(win, 'SUMM')
        QTest.keyClick(editor, Qt.Key.Key_Tab)
        assert editor.text() == '=SUM('
        assert win._cell_delegate.active_editor is editor

    def test_live_syntax_problem_without_enter(self, win):
        editor, helper = self.start_typing(win, 'A2+*B2')
        assert helper.panel.isVisible()
        assert helper.diagnostic.code == 'syntax'
        assert helper.problem.isVisible()
        assert not win.model.formulas

    def test_arithmetic_input_keeps_guidance_visible(self, win):
        editor, helper = self.start_typing(win, 'A2+B2')
        assert helper.panel.isVisible()
        assert helper.help.text()
        assert helper.diagnostic is None

    def test_partial_function_name_is_not_an_error(self, win):
        editor, helper = self.start_typing(win, 'SU')
        assert helper.diagnostic is None

    def test_overlay_stays_in_narrow_window_and_clear_of_editor(self, win):
        win.resize(560, 460)
        editor, helper = self.start_typing(win, 'SU')
        assert win.rect().contains(helper.panel.geometry())
        editor_rect = editor.geometry()
        editor_rect.moveTopLeft(editor.mapTo(win, QPoint(0, 0)))
        assert not helper.panel.geometry().intersects(editor_rect)

    def test_preview_automatically_shows_typo_suggestions(self, win):
        win.table.setCurrentIndex(win.model.index(1, 2))
        preview = win.cell_preview_text
        preview.setFocus()
        preview.selectAll()
        QTest.keyClicks(preview, '=SUMM', delay=15)
        QTest.qWait(180)
        helper = preview.formula_assistant
        assert helper.panel.isVisible()
        assert helper.list.count() > 0
        assert helper.diagnostic.code == 'function'

    def test_fast_typing_then_tab_uses_current_prefix(self, win):
        win.activateWindow()
        win.table.setCurrentIndex(win.model.index(1, 2))
        win.table.setFocus()
        _app.processEvents()
        QTest.keyClicks(win.table, '=')
        editor = win._cell_delegate.active_editor
        QTest.keyClicks(editor, 'SUMIF')
        # No event-loop wait: the debounce timer has not refreshed the list.
        QTest.keyClick(editor, Qt.Key.Key_Tab)
        assert editor.text() == '=SUMIF('
        assert not win.model.formulas
