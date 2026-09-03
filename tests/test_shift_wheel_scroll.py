"""Shift + 滚轮横向滚动（普通滚轮仍纵向滚动）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from PyQt6.QtCore import Qt, QPoint, QPointF
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui.main_window import MainWindow


def _wheel(view, dy, mods):
    pos = QPointF(view.viewport().rect().center())
    return QWheelEvent(
        pos, view.viewport().mapToGlobal(pos.toPoint()).toPointF(),
        QPoint(0, 0), QPoint(0, dy), Qt.MouseButton.NoButton, mods,
        Qt.ScrollPhase.NoScrollPhase, False,
    )


def _make():
    win = MainWindow()
    win.df = pd.DataFrame({f'c{i}': list(range(200)) for i in range(40)})
    win.model.set_dataframe(win.df)
    win.resize(600, 400)
    win.show()
    _app.processEvents()
    return win


def test_shift_wheel_scrolls_horizontally():
    win = _make()
    hbar, vbar = win.table.horizontalScrollBar(), win.table.verticalScrollBar()
    hbar.setValue(0)
    vbar.setValue(0)
    win.table.wheelEvent(_wheel(win.table, -120, Qt.KeyboardModifier.ShiftModifier))
    assert hbar.value() > 0
    assert vbar.value() == 0
    win.model.modified = False
    win.close()


def test_plain_wheel_still_scrolls_vertically():
    win = _make()
    hbar, vbar = win.table.horizontalScrollBar(), win.table.verticalScrollBar()
    hbar.setValue(0)
    vbar.setValue(0)
    win.table.wheelEvent(_wheel(win.table, -120, Qt.KeyboardModifier.NoModifier))
    assert vbar.value() > 0
    assert hbar.value() == 0
    win.model.modified = False
    win.close()
