"""Formula editing has explicit commit boundaries and non-mutating previews."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import pytest
from PyQt6.QtCore import Qt, QSettings, QPersistentModelIndex, QPoint
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QAbstractItemDelegate
from qtui import main_window, file_io
from qtui.formula_workbench import ArgumentWizard

_app = QApplication.instance() or QApplication([])

@pytest.fixture
def win(tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / 'ui.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(main_window, '_make_settings', lambda: settings)
    monkeypatch.setattr(main_window, 'FILE_CONFIG_PATH', str(tmp_path / 'files.json'))
    monkeypatch.setattr(file_io, 'RECENT_FILES_PATH', str(tmp_path / 'recent.json'))
    w = main_window.MainWindow(restore_session=False)
    w._update_check_timer.stop()
    w.model.set_dataframe(pd.DataFrame({'数量': [2., 3., 4.], '单价': [5., 6., 7.], '金额': [0., 0., 0.]}))
    w.resize(1120, 760); w.show(); w.activateWindow()
    w.table.setCurrentIndex(w.model.index(1, 2)); w.table.setFocus()
    _app.processEvents()
    yield w
    wb = w.formula_workbench
    if wb.wizard: wb.wizard.reject()
    editor = w._cell_delegate.active_editor
    if editor: w.table.closeEditor(editor, QAbstractItemDelegate.EndEditHint.RevertModelCache)
    wb.cancel_bar(); w._cancel_preview_formula(); w._preview_save_timer.stop()
    w.model.modified = False; w.close(); _app.processEvents()


def type_bar(w, text):
    bar = w.formula_workbench.edit
    bar.setFocus(); bar.selectAll(); bar.insert(text)
    return bar


def wait_preview(w):
    for _ in range(100):
        QTest.qWait(20)
        if w.formula_workbench._job is None and not w.formula_workbench.timer.isActive(): break


class TestFormulaBar:
    def test_address_commit_cancel_and_undo(self, win):
        wb = win.formula_workbench
        assert wb.location.text() == 'C2' and wb.edit.text() == '0'
        type_bar(win, '=A2*B2')
        assert wb.dirty and win.model.df.iat[0, 2] == 0
        assert wb.commit_bar()
        assert win.model.df.iat[0, 2] == 10
        assert not wb.dirty
        type_bar(win, '=100')
        wb.cancel_bar()
        assert wb.edit.text() == '=A2*B2'
        assert win.model.df.iat[0, 2] == 10
        win.model.undo()
        assert win.model.df.iat[0, 2] == 0

    def test_native_cell_input_mirrors_full_formula(self, win):
        win.table.edit(win.model.index(1, 2)); _app.processEvents()
        editor = win._cell_delegate.active_editor
        editor.setText('=SUM(A2:B4)')
        assert win.formula_workbench.edit.text() == '=SUM(A2:B4)'
        assert win.model.df.iat[0, 2] == 0

    def test_bar_supports_reference_picking(self, win):
        bar = type_bar(win, '=SUM(')
        assert win.table._formula_editor() is bar
        assert win.table._insert_point_ref(bar, win.model.index(1, 0))
        assert bar.text() == '=SUM(A2'
        assert win.model.df.iat[0, 2] == 0

    def test_preview_draft_and_bar_share_one_input(self, win):
        preview = win.cell_preview_text
        preview.setPlainText('=A2*B2')
        assert win.formula_workbench.edit.text() == '=A2*B2'
        type_bar(win, '=A3*B3')
        assert not win._preview_formula_pending
        assert preview.toPlainText() == '=A3*B3'
        assert win.formula_workbench.commit_bar()
        assert win.model.df.iat[0, 2] == 18


class TestResultPreview:
    def test_preview_does_not_change_data_formulas_or_history(self, win):
        before = win.model.df.copy(deep=True)
        history = len(win.model._undo_stack)
        type_bar(win, '=A2*B2')
        wait_preview(win)
        assert '10' in win.formula_workbench.result.text()
        pd.testing.assert_frame_equal(before, win.model.df)
        assert not win.model.formulas and len(win.model._undo_stack) == history

    def test_errors_explained_before_commit(self, win):
        type_bar(win, '=1/0'); wait_preview(win)
        assert '#DIV/0!' in win.formula_workbench.result.text()
        assert not win.model.formulas

    def test_preview_detects_self_reference(self, win):
        type_bar(win, '=C2+1'); wait_preview(win)
        assert '#CIRC!' in win.formula_workbench.result.text()

    def test_large_range_is_explicit_and_background(self, win):
        win.formula_workbench.AUTO_PREVIEW_CELLS = 2
        type_bar(win, '=SUM(A2:B4)'); wait_preview(win)
        wb = win.formula_workbench
        assert wb.calculate.isVisible() and wb._job is None
        wb.preview(force=True); wait_preview(win)
        assert '27' in wb.result.text()
        assert not win.model.formulas

    def test_stale_result_cannot_replace_newer_input(self, win):
        wb = win.formula_workbench
        type_bar(win, '=1+1')
        old = wb._generation
        type_bar(win, '=3+3')
        wb.result.setText('new')
        wb.preview_done(old, 2)
        assert wb.result.text() == 'new'
        wait_preview(win)
        assert '6' in wb.result.text()


class TestDraftDecisions:
    def test_invalid_bar_draft_can_cancel_selection_change(self, win, monkeypatch):
        monkeypatch.setattr(win.formula_workbench, 'ask_draft', lambda _: 'edit')
        type_bar(win, '=SUM(')
        win.table.setCurrentIndex(win.model.index(2, 2))
        assert win.table.currentIndex() == win.model.index(1, 2)
        assert win.formula_workbench.edit.text() == '=SUM('
        assert not win.model.formulas

    def test_invalid_draft_can_be_discarded(self, win, monkeypatch):
        monkeypatch.setattr(win.formula_workbench, 'ask_draft', lambda _: 'discard')
        type_bar(win, '=SUM(')
        win.table.setCurrentIndex(win.model.index(2, 2))
        assert win.table.currentIndex().row() == 2
        assert not win.model.formulas and not win.formula_workbench.dirty

    def test_user_can_explicitly_store_error_formula(self, win, monkeypatch):
        monkeypatch.setattr(win.formula_workbench, 'ask_draft', lambda _: 'store')
        type_bar(win, '=SUM(')
        assert win._flush_preview()
        assert win.model.formulas[(0, 2)] == '=SUM('
        assert win.model.df.iat[0, 2] == '#ERROR'

    def test_invalid_preview_can_cancel_sheet_change(self, win, monkeypatch):
        win.sheet_names = ['S1', 'S2']; win.current_sheet = 'S1'
        win._cache_sheet('S1', win.model.df, pin=True)
        win._cache_sheet('S2', pd.DataFrame({'A': [99]}), pin=True)
        monkeypatch.setattr(win.formula_workbench, 'ask_draft', lambda _: 'edit')
        win.cell_preview_text.setPlainText('=SUM(')
        assert win.switch_sheet('S2') is False
        assert win.current_sheet == 'S1' and win._preview_formula_pending

    def test_save_and_close_cannot_discard_invalid_bar_draft(self, win, monkeypatch):
        monkeypatch.setattr(win.formula_workbench, 'ask_draft', lambda _: 'edit')
        type_bar(win, '=IF(')
        assert win.save_file() is False
        assert win.close() is False
        assert win.isVisible() and win.formula_workbench.dirty

    def test_valid_draft_commits_when_changing_cells(self, win):
        type_bar(win, '=A2*B2')
        win.table.setCurrentIndex(win.model.index(2, 2))
        assert win.model.df.iat[0, 2] == 10
        assert not win.formula_workbench.dirty


class TestWizard:
    def test_if_explains_and_assembles_three_arguments(self, win):
        dialog = ArgumentWizard(win, 'IF')
        assert len(dialog.fields) == 3
        assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        for field, value in zip(dialog.fields, ('A2>1', '"是"', '"否"')): field.setText(value)
        dialog.accept()
        assert dialog.selected_formula == '=IF(A2>1,"是","否")'

    def test_repeated_criteria_groups(self, win):
        dialog = ArgumentWizard(win, 'SUMIFS')
        dialog.add_group()
        assert len(dialog.fields) == 5
        for field, value in zip(dialog.fields, ('C2:C4', 'A2:A4', '">1"', 'B2:B4', '">0"')): field.setText(value)
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        dialog.remove_group()
        assert len(dialog.fields) == 3
        dialog.close()

    def test_range_picking_returns_to_wizard_without_changing_cell(self, win):
        wb = win.formula_workbench
        wb.open_wizard(); dialog = wb.wizard
        wb.start_pick(dialog.fields[0])
        wb.pick(win.model.index(1, 0))
        wb.pick(win.model.index(2, 1), finish=True)
        assert dialog.fields[0].text() == 'A2:B3' and dialog.isVisible()
        dialog.accept()
        assert wb.edit.text() == '=SUM(A2:B3)'
        assert not win.model.formulas
        assert wb.commit_bar() and win.model.df.iat[0, 2] == 16

    def test_existing_function_arguments_can_be_edited(self, win):
        type_bar(win, '=IF(A2>0,1,0)')
        wb = win.formula_workbench
        wb.open_wizard()
        assert wb.wizard.function.currentData() == 'IF'
        assert [f.text() for f in wb.wizard.fields] == ['A2>0', '1', '0']
        wb.wizard.fields[1].setText('2'); wb.wizard.accept()
        assert wb.edit.text() == '=IF(A2>0,2,0)'

    def test_wizard_does_not_append_invalid_function_to_arithmetic(self, win):
        type_bar(win, '=A2*B2')
        wb = win.formula_workbench; wb.open_wizard()
        wb.wizard.fields[0].setText('A2:B2'); wb.wizard.accept()
        assert wb.edit.text() == '=SUM(A2:B2)'


class TestErrorNavigation:
    def test_upstream_error_link_and_return(self, win):
        win.model.setData(win.model.index(1, 0), '=1/0')
        win.model.setData(win.model.index(1, 2), '=A2+1')
        win.table.setCurrentIndex(win.model.index(1, 2)); win._refresh_formula_feedback()
        assert 'href="1,0"' in win.formula_error_label.text()
        win.formula_error_label.linkActivated.emit('1,0')
        assert win.table.currentIndex() == win.model.index(1, 0)
        win.formula_workbench.return_to_error()
        assert win.table.currentIndex() == win.model.index(1, 2)

    def test_circular_path_targets_are_real_cells(self, win):
        win.model.setData(win.model.index(1, 0), '=B2')
        win.model.setData(win.model.index(1, 1), '=A2')
        targets = win.model.formula_error_targets(0, 0)
        assert ('A2', 1, 0) in targets and ('B2', 1, 1) in targets


class TestEditingHandoff:
    def test_native_draft_is_marked_and_confirmable_from_bar(self, win):
        win.table.edit(win.model.index(1, 2)); _app.processEvents()
        editor = win._cell_delegate.active_editor
        editor.setText('=A2*B2')
        wb = win.formula_workbench
        assert wb.confirm.isEnabled()
        assert wb.commit_bar()
        assert win.model.df.iat[0, 2] == 10

    def test_clicking_bar_transfers_incomplete_native_draft(self, win):
        win.table.edit(win.model.index(1, 2)); _app.processEvents()
        editor = win._cell_delegate.active_editor
        editor.setText('=SUM(')
        win.formula_workbench.edit.setFocus(); _app.processEvents()
        assert win.formula_workbench.edit.text() == '=SUM('
        assert win.formula_workbench.dirty
        assert not win.model.formulas

    def test_native_draft_must_be_resolved_before_save(self, win, monkeypatch):
        win.table.edit(win.model.index(1, 2)); _app.processEvents()
        editor = win._cell_delegate.active_editor
        editor.setText('=SUM(')
        monkeypatch.setattr(win.formula_workbench, 'ask_draft', lambda _: 'edit')
        assert win.save_file() is False
        assert not win.model.formulas

    def test_escape_cell_edit_restores_formula_bar(self, win):
        win.table.edit(win.model.index(1, 2)); _app.processEvents()
        editor = win._cell_delegate.active_editor
        editor.setText('=A2*B2')
        # First Esc dismisses the assistant, the second cancels the cell editor.
        editor.formula_assistant.refresh()
        QTest.keyClick(editor, Qt.Key.Key_Escape); QTest.keyClick(editor, Qt.Key.Key_Escape)
        _app.processEvents()
        assert win.formula_workbench.edit.text() == '0'
        assert not win.model.formulas

    def test_static_range_error_has_a_clickable_target(self, win):
        win.model.setData(win.model.index(1, 0), '#DIV/0!')
        win.model.setData(win.model.index(1, 2), '=SUM(A2:A4)')
        assert ('A2', 1, 0) in win.model.formula_error_targets(0, 2)

    def test_formula_bar_popup_does_not_cover_result_preview(self, win):
        bar = type_bar(win, '=A2*B2')
        wait_preview(win); QTest.qWait(150)
        helper = bar.formula_assistant
        assert helper.panel.isVisible()
        rect = win.formula_workbench.widget.geometry()
        rect.moveTopLeft(win.formula_workbench.widget.mapTo(win, QPoint(0, 0)))
        assert not helper.panel.geometry().intersects(rect)
        assert helper.panel.height() < 130

    def test_reading_and_enter_does_not_truncate_long_cell_text(self, win):
        text = 'x' * 40000
        win.model.setData(win.model.index(1, 2), text)
        wb = win.formula_workbench
        assert wb.edit.text() == text
        history = len(win.model._undo_stack)
        assert wb.commit_bar()
        assert win.model.df.iat[0, 2] == text
        assert len(win.model._undo_stack) == history

    def test_right_panel_draft_can_be_confirmed_from_fixed_bar(self, win):
        win.cell_preview_text.setPlainText('=A2*B2')
        wb = win.formula_workbench
        assert wb.confirm.isEnabled()
        assert wb.commit_bar()
        assert win.model.df.iat[0, 2] == 10 and not win._preview_formula_pending

    def test_right_panel_draft_can_be_cancelled_from_fixed_bar(self, win):
        win.cell_preview_text.setPlainText('=SUM(')
        wb = win.formula_workbench
        assert wb.cancel.isEnabled()
        wb.cancel_bar()
        assert not win._preview_formula_pending
        assert win.cell_preview_text.toPlainText() == '0'
        assert win.model.df.iat[0, 2] == 0

    def test_native_draft_can_continue_in_right_panel(self, win):
        win.table.edit(win.model.index(1, 2)); _app.processEvents()
        editor = win._cell_delegate.active_editor; editor.setText('=SUM(')
        assert win.cell_preview_text.toPlainText() == '=SUM('
        win.cell_preview_dock.show(); win.cell_preview_text.setFocus(); _app.processEvents()
        assert win._preview_formula_pending and not win.model.formulas
        assert win.cell_preview_text.toPlainText() == '=SUM('

    def test_bar_draft_can_continue_in_right_panel(self, win):
        type_bar(win, '=SUM(')
        win.cell_preview_dock.show(); win.cell_preview_text.setFocus(); _app.processEvents()
        assert not win.formula_workbench.dirty and win._preview_formula_pending
        assert win.cell_preview_text.toPlainText() == '=SUM('
        assert not win.model.formulas

    def test_right_panel_draft_can_continue_in_native_cell(self, win):
        win.cell_preview_text.setPlainText('=SUM(')
        win.table.edit(win.model.index(1, 2)); _app.processEvents()
        editor = win._cell_delegate.active_editor
        assert editor.text() == '=SUM('
        assert not win._preview_formula_pending and not win.model.formulas

    def test_floating_preview_panel_uses_main_window_commit(self, win):
        win.cell_preview_dock.setFloating(True); win.cell_preview_dock.show()
        preview = win.cell_preview_text; preview.setFocus()
        preview.setPlainText('=A2*B2'); preview.setCursorPosition(6)
        QTest.keyClick(preview, Qt.Key.Key_Return)
        assert win.model.df.iat[0, 2] == 10
        win.cell_preview_dock.setFloating(False)

    def test_header_editor_is_not_treated_as_a_formula_draft(self, win):
        win.table.setCurrentIndex(win.model.index(0, 0))
        win.table.edit(win.model.index(0, 0)); _app.processEvents()
        editor = win._cell_delegate.active_editor
        editor.setText('商品')
        assert win.formula_workbench.native_editor() is None
        win.table.commit_and_move(editor, 0, 0)
        assert win.model.df.columns[0] == '商品'
