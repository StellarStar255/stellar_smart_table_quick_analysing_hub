"""预览面板右键：不打开查看器也能直接复制图片。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QContextMenuEvent, QImage
from PyQt6.QtWidgets import QApplication, QMenu

_app = QApplication.instance() or QApplication([])

from qtui import image_panel
from qtui.i18n import tr
from qtui.main_window import MainWindow


@pytest.fixture
def img(tmp_path):
    p = tmp_path / "a.png"
    im = QImage(10, 10, QImage.Format.Format_RGB32)
    im.fill(0x112233)
    im.save(str(p))
    return str(p)


def _menu_actions(monkeypatch):
    """拦住 QMenu.exec，返回菜单里的动作列表。"""
    grabbed = {}

    def fake_exec(self, *a, **k):
        grabbed["menu"] = self
        return None
    monkeypatch.setattr(QMenu, "exec", fake_exec)
    return grabbed


def _right_click(widget):
    ev = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(1, 1),
                           QPoint(100, 100))
    widget.contextMenuEvent(ev)


class TestPanelMenu:
    def test_main_view_menu_offers_copy_and_open(self, img, monkeypatch):
        panel = image_panel.ImagePreviewPanel()
        panel.main_view.path = img
        grabbed = _menu_actions(monkeypatch)
        copied, opened = [], []
        panel.copyImageRequested.connect(copied.append)
        panel.openImageRequested.connect(opened.append)
        _right_click(panel.main_view)
        actions = grabbed["menu"].actions()
        assert [a.text() for a in actions] == [tr("复制图片"), tr("打开大图")]
        actions[0].trigger()
        assert copied == [img]
        assert opened == []          # 复制不会顺带打开查看器
        panel.deleteLater()

    def test_strip_cell_menu_also_selects_the_row(self, img, monkeypatch):
        cell = image_panel._StripCell(4, img, image_panel.BASE_STRIP_THUMB)
        grabbed = _menu_actions(monkeypatch)
        rows, paths = [], []
        cell.clicked.connect(rows.append)
        cell.menuRequested.connect(lambda p, _pos: paths.append(p))
        _right_click(cell)
        assert rows == [4] and paths == [img]
        cell.deleteLater()

    def test_no_menu_without_image(self, monkeypatch):
        panel = image_panel.ImagePreviewPanel()
        panel.main_view.path = None
        grabbed = _menu_actions(monkeypatch)
        _right_click(panel.main_view)
        assert "menu" not in grabbed
        panel.deleteLater()


class TestWindowCopySlot:
    def test_copy_reports_success_in_statusbar(self, img, monkeypatch):
        win = MainWindow()
        calls = []
        monkeypatch.setattr("qtui.image_viewer.copy_image_to_clipboard",
                            lambda p: (calls.append(p), True)[1])
        win.copy_image_to_clipboard(img)
        assert calls == [img]
        assert os.path.basename(img) in win.statusBar().currentMessage()
        win.model.modified = False
        win.close()

    def test_missing_file_is_reported_not_copied(self, tmp_path, monkeypatch):
        win = MainWindow()
        calls = []
        monkeypatch.setattr("qtui.image_viewer.copy_image_to_clipboard",
                            lambda p: (calls.append(p), True)[1])
        win.copy_image_to_clipboard(str(tmp_path / "nope.png"))
        assert calls == []
        win.model.modified = False
        win.close()
