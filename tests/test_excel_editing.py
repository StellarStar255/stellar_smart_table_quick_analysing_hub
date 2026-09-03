"""Excel 式单元格编辑手感（审查后重做）：

- 选中后直接打字 = 覆盖式编辑（打字模式），方向键提交并移动
- 回车提交后下移但不自动打开下一格编辑器；非编辑状态回车/Tab 只移动
- F2/双击 = 光标模式：光标在末尾、方向键移动光标
- 连续 Tab 后回车回到起点列；Shift+Enter 上移
- 公式输入时方向键保留为光标移动（不提交）

需要 QApplication（widgets）；CI 里用 QT_QPA_PLATFORM=offscreen 无头运行。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QAbstractItemView

_app = QApplication.instance() or QApplication([])

from qtui.main_window import MainWindow


class Harness:
    def __init__(self, win):
        self.win = win
        self.table = win.table

    def flush(self):
        for _ in range(3):
            _app.processEvents()
            _app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def editor(self):
        return self.table.itemDelegate().active_editor

    def editing(self):
        return self.table.state() == QAbstractItemView.State.EditingState

    def pos(self):
        idx = self.table.currentIndex()
        return idx.row(), idx.column()

    def go(self, row, col):
        self.table.setCurrentIndex(self.win.model.index(row, col))
        self.flush()

    def press(self, key, mod=Qt.KeyboardModifier.NoModifier):
        target = self.editor() if self.editing() else self.table
        QTest.keyClick(target, key, mod)
        self.flush()


@pytest.fixture
def h():
    w = MainWindow()
    w.show()
    w.model.set_dataframe(pd.DataFrame({'A': [10.0, 20.0, 30.0],
                                        'B': ['x', 'y', 'z'],
                                        'C': [1.0, 2.0, 3.0]}))
    w.model.modified = False
    w.table.setFocus()
    yield Harness(w)
    w.model.modified = False
    w.close()


def test_typing_starts_replace_edit(h):
    h.go(1, 0)
    h.press(Qt.Key.Key_5)
    assert h.editing() and h.editor().text() == '5'
    assert h.editor()._typing_mode is True


def test_enter_commits_moves_down_without_opening_editor(h):
    h.go(1, 0)
    h.press(Qt.Key.Key_5)
    h.press(Qt.Key.Key_Return)
    assert h.win.model.df.iat[0, 0] == 5.0
    assert h.pos() == (2, 0)
    assert not h.editing()


def test_enter_and_tab_navigate_when_not_editing(h):
    h.go(1, 0)
    h.press(Qt.Key.Key_Return)
    assert h.pos() == (2, 0) and not h.editing()
    h.press(Qt.Key.Key_Tab)
    assert h.pos() == (2, 1) and not h.editing()
    h.press(Qt.Key.Key_Backtab)
    assert h.pos() == (2, 0)
    h.press(Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert h.pos() == (1, 0)


def test_arrow_commits_in_typing_mode(h):
    h.go(2, 1)
    h.press(Qt.Key.Key_7)
    h.press(Qt.Key.Key_Down)
    assert h.win.model.df.iat[1, 1] == '7'
    assert h.pos() == (3, 1) and not h.editing()
    h.press(Qt.Key.Key_8)
    h.press(Qt.Key.Key_Right)
    assert h.win.model.df.iat[2, 1] == '8'
    assert h.pos() == (3, 2)


def test_tab_then_enter_returns_to_origin_column(h):
    h.go(1, 1)
    h.press(Qt.Key.Key_1)
    h.press(Qt.Key.Key_Tab)          # B2 -> C2
    h.press(Qt.Key.Key_2)
    h.press(Qt.Key.Key_Return)       # 回到 B 列下一行
    assert h.win.model.df.iat[0, 2] == 2.0
    assert h.pos() == (2, 1)


def test_f2_is_cursor_mode(h):
    h.go(1, 0)
    h.press(Qt.Key.Key_F2)
    e = h.editor()
    assert h.editing() and e.text() == '10'
    assert e.selectedText() == '' and e.cursorPosition() == 2
    h.press(Qt.Key.Key_Left)
    assert h.editing() and e.cursorPosition() == 1   # 方向键移光标而非提交
    h.press(Qt.Key.Key_Escape)
    assert not h.editing() and h.win.model.df.iat[0, 0] == 10.0


def test_double_click_is_cursor_mode(h):
    rect = h.table.visualRect(h.win.model.index(2, 1))
    QTest.mouseClick(h.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    h.flush()
    QTest.mouseDClick(h.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    h.flush()
    assert h.editing() and h.editor()._typing_mode is False
    assert h.editor().selectedText() == ''


def test_formula_typing_keeps_arrow_for_caret(h):
    h.go(1, 0)
    for k in (Qt.Key.Key_Equal, Qt.Key.Key_A, Qt.Key.Key_3):
        h.press(k)
    h.press(Qt.Key.Key_Left)
    assert h.editing() and h.editor().cursorPosition() == 2
    h.press(Qt.Key.Key_Return)
    assert h.win.model.formulas.get((0, 0), '').lower() == '=a3'
    assert h.win.model.df.iat[0, 0] == 20.0
    assert h.pos() == (2, 0)


def test_click_elsewhere_commits_without_extra_move(h):
    h.go(1, 2)
    h.press(Qt.Key.Key_4)
    rect = h.table.visualRect(h.win.model.index(3, 0))
    QTest.mouseClick(h.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    h.flush()
    assert h.win.model.df.iat[0, 2] == 4.0
    assert h.pos() == (3, 0) and not h.editing()
