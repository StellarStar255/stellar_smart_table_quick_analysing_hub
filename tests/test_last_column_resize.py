"""最后一列贴着视口边缘时，字母/列名行都能从内侧抓住边缘调宽。"""
import pandas as pd
import pytest
from PyQt6.QtCore import QPoint, QSettings, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from qtui import main_window as mw

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setattr(mw, "_make_settings", lambda: QSettings(
        str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    w = mw.MainWindow()
    w.model.set_dataframe(pd.DataFrame({chr(65 + i): [i] for i in range(10)}))
    w.resize(800, 500)
    w.show()
    QApplication.processEvents()
    bar = w.table.horizontalScrollBar()
    bar.setValue(bar.maximum())
    QApplication.processEvents()
    yield w
    w.model.modified = False
    w.close()


@pytest.mark.parametrize("surface", ["letters", "names"])
@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("delta", [-40, 40])
def test_last_column_edge_resizes_without_moving_columns(win, surface, selected, delta):
    table = win.table
    header = table.horizontalHeader()
    last = header.count() - 1
    if selected:
        table.selectColumn(last)
    before = header.sectionSize(last)
    columns = list(win.model.df.columns)
    right = header.sectionViewportPosition(last) + before
    assert right == header.viewport().width()
    if surface == "letters":
        vp, y = header.viewport(), header.height() // 2
    else:
        vp = table.viewport()
        y = table.visualRect(win.model.index(0, last)).center().y()
    start = QPoint(right - 6, y)
    end = start + QPoint(delta, 0)
    QTest.mouseMove(vp, start)
    assert vp.cursor().shape() == Qt.CursorShape.SplitHCursor
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(vp, end)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=end)
    assert header.sectionSize(last) == before + delta
    assert list(win.model.df.columns) == columns
    assert header.drop_gap is None
    assert header._last_resize is None
    assert not table._resizing_last_column


def test_last_column_filter_arrow_still_opens(win):
    table = win.table
    col = table.model().columnCount() - 1
    triggered = []
    table.filterArrowClicked.disconnect(win.open_column_filter)
    table.filterArrowClicked.connect(triggered.append)
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                     pos=table.filter_arrow_rect(col).center())
    assert triggered == [col]
    assert not table._resizing_last_column


def test_clipped_last_column_does_not_create_fake_resize_handle(win):
    win.table.horizontalScrollBar().setValue(0)
    QApplication.processEvents()
    header = win.table.horizontalHeader()
    assert not header.last_resize_handle_at(header.viewport().width() - 3)


def test_resize_cursor_clears_away_from_edge(win):
    header = win.table.horizontalHeader()
    right = header.sectionViewportPosition(9) + header.sectionSize(9)
    QTest.mouseMove(header.viewport(), QPoint(right - 6, header.height() // 2))
    assert header.cursor().shape() == Qt.CursorShape.SplitHCursor
    QTest.mouseMove(header.viewport(), QPoint(right - 50, header.height() // 2))
    assert header.cursor().shape() != Qt.CursorShape.SplitHCursor


def test_resizing_cannot_shrink_below_minimum(win):
    header = win.table.horizontalHeader()
    right = header.sectionViewportPosition(9) + header.sectionSize(9)
    start = QPoint(right - 6, header.height() // 2)
    end = start - QPoint(300, 0)
    QTest.mousePress(header.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(header.viewport(), end)
    QTest.mouseRelease(header.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert header.sectionSize(9) == header.minimumSectionSize()
