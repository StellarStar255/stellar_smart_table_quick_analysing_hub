"""公式点选引用（Excel point mode）测试

输入公式时点击其他单元格 -> 在光标处插入该格引用；
连续点击替换上次插入的引用；拖选扩成区域引用；
非公式/光标位置不可插入时不拦截（普通点击语义不变）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest

_app = QApplication.instance() or QApplication([])

from qtui.main_window import MainWindow


@pytest.fixture
def win():
    w = MainWindow()
    w.model.set_dataframe(
        pd.DataFrame({'X': ['a', 'b', 'c'], 'Y': ['1', '2', '3']}))
    w.show()
    yield w
    w.model.modified = False
    w.close()


def open_editor(w, view_row, col, text):
    """在 (view_row, col) 打开编辑器并预置公式文本，光标在末尾。"""
    idx = w.model.index(view_row, col)
    w.table.setCurrentIndex(idx)
    w.table.edit(idx)
    editor = w._cell_delegate.active_editor
    assert editor is not None
    editor.setText(text)
    editor.setCursorPosition(len(text))
    return editor


def click_cell(w, view_row, col):
    rect = w.table.visualRect(w.model.index(view_row, col))
    QTest.mouseClick(w.table.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, rect.center())


class TestPointMode:
    def test_click_inserts_reference(self, win):
        editor = open_editor(win, 1, 1, '=CONCAT(')
        click_cell(win, 2, 0)          # 视图行 2 = 引用行 3，列 0 = A
        assert editor.text() == '=CONCAT(A3'

    def test_second_click_replaces_previous_ref(self, win):
        editor = open_editor(win, 1, 1, '=CONCAT(')
        click_cell(win, 2, 0)
        click_cell(win, 3, 0)
        assert editor.text() == '=CONCAT(A4'

    def test_ref_after_comma_and_operator(self, win):
        editor = open_editor(win, 1, 1, '=CONCAT(A2,')
        click_cell(win, 3, 1)
        assert editor.text() == '=CONCAT(A2,B4'
        editor.setText('=A2&')
        editor.setCursorPosition(4)
        click_cell(win, 2, 0)
        assert editor.text() == '=A2&A3'

    def test_editor_keeps_focus_and_stays_open(self, win):
        editor = open_editor(win, 1, 1, '=CONCAT(')
        click_cell(win, 2, 0)
        assert win._cell_delegate.active_editor is editor

    def test_no_insert_when_not_formula(self, win):
        editor = open_editor(win, 1, 1, 'hello')
        assert win.table._formula_editor() is None

    def test_no_insert_after_plain_char(self, win):
        # 光标前是普通字符（手打的引用），点击不插入 -> 普通点击语义
        editor = open_editor(win, 1, 1, '=CONCAT(A2')
        assert win.table._point_insert_start(editor) is None

    def test_drag_creates_range_reference(self, win):
        editor = open_editor(win, 1, 1, '=SUM(')
        anchor = win.model.index(2, 0)
        win.table._point_anchor = anchor
        assert win.table._insert_point_ref(editor, anchor)
        win.table._drag_point_range(editor, win.model.index(3, 1))
        assert editor.text() == '=SUM(A3:B4'

    def test_header_row_click_gives_row1_ref(self, win):
        editor = open_editor(win, 1, 1, '=')
        click_cell(win, 0, 0)          # 视图行 0 = 表头行 = 引用第 1 行
        assert editor.text() == '=A1'

    def test_commit_after_point_insert(self, win):
        # 无头环境窗口无法激活，Enter 键提交链路走不通（与本功能无关），
        # 直接调用 commitData 验证点选后编辑器与单元格的映射未被破坏
        editor = open_editor(win, 1, 0, '=')
        click_cell(win, 2, 1)          # =B3 -> Y 列数据行 1 = '2'
        win.table.commitData(editor)
        assert win.model.formulas == {(0, 0): '=B3'}
        assert str(win.model.df.iat[0, 0]) == '2'


class TestClickThroughExpandedEditor:
    """长公式把编辑器撑宽盖住邻格时，点被盖住的格子仍能插入引用。"""

    LONG = '=CONCAT("/a/very/long/path/that/pushes/the/editor/over/its/neighbours/",'

    def _expanded_editor(self, win):
        editor = open_editor(win, 1, 0, self.LONG)
        _app.processEvents()
        own = win.table.visualRect(win.model.index(1, 0))
        assert editor.width() > own.width(), "编辑器应已被长文本撑宽"
        return editor, own

    def _press_on(self, win, editor, view_row, col, button=Qt.MouseButton.LeftButton):
        cell = win.table.visualRect(win.model.index(view_row, col))
        pos = editor.mapFrom(win.table.viewport(), cell.center())
        assert editor.rect().contains(pos), "该格应被编辑器盖住"
        QTest.mousePress(editor, button, Qt.KeyboardModifier.NoModifier, pos)
        return pos

    def test_click_on_covered_neighbour_inserts_ref(self, win):
        editor, _ = self._expanded_editor(win)
        pos = self._press_on(win, editor, 1, 1)      # B2 被编辑器盖住
        QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
        assert editor.text() == self.LONG + 'B2'
        assert win._cell_delegate.active_editor is editor
        assert win.table._point_anchor is None

    def test_drag_over_covered_cells_makes_range(self, win):
        editor, _ = self._expanded_editor(win)
        self._press_on(win, editor, 1, 1)
        cell = win.table.visualRect(win.model.index(2, 1))
        pos = editor.mapFrom(win.table.viewport(), cell.center())
        QTest.mouseMove(editor, pos)
        QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
        assert editor.text() == self.LONG + 'B2:B3'

    def test_click_inside_own_cell_moves_caret_not_ref(self, win):
        editor, own = self._expanded_editor(win)
        pos = editor.mapFrom(win.table.viewport(), own.center())
        QTest.mouseClick(editor, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
        assert editor.text() == self.LONG
        assert editor.cursorPosition() < len(self.LONG)

    def test_no_ref_when_caret_not_insertable(self, win):
        editor, _ = self._expanded_editor(win)
        editor.setText(self.LONG + 'A9')
        editor.setCursorPosition(len(editor.text()))
        _app.processEvents()
        pos = self._press_on(win, editor, 1, 1)
        QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
        assert editor.text() == self.LONG + 'A9'
