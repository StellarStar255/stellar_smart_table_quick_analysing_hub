"""图片查看器 ←/→ 翻上一张/下一张：按图片列顺序走，跳过没图的行，表格当前行跟着走。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui.image_viewer import ImageViewer
from qtui.main_window import MainWindow


@pytest.fixture
def images(tmp_path):
    paths = []
    for i in range(3):
        img = QImage(8, 8, QImage.Format.Format_RGB32)
        img.fill(0x101010 * (i + 1))
        p = tmp_path / f"img{i}.png"
        img.save(str(p))
        paths.append(str(p))
    return paths


@pytest.fixture
def win(images):
    w = MainWindow()
    # 第 2 行没有图（空路径），翻页时要跳过它
    df = pd.DataFrame({"img": [images[0], images[1], "", images[2]],
                       "v": [1, 2, 3, 4]})
    w.model.set_dataframe(df)
    w._set_image_column("img")
    w.jump_to_cell(0, 0)
    w.image_panel.set_current_row(0)
    yield w
    for v in list(w._image_viewers):
        v.close()
    w.model.modified = False
    w.close()


class TestPanelNeighbor:
    def test_skips_rows_without_image(self, win, images):
        win.image_panel.set_current_row(1)
        assert win.image_panel.neighbor_with_image(1) == (3, images[2])
        assert win.image_panel.neighbor_with_image(-1) == (0, images[0])

    def test_none_at_the_ends(self, win):
        win.image_panel.set_current_row(0)
        assert win.image_panel.neighbor_with_image(-1) is None
        win.image_panel.set_current_row(3)
        assert win.image_panel.neighbor_with_image(1) is None


class TestViewerKeys:
    def test_arrow_keys_emit_navigation(self, images):
        viewer = ImageViewer(images[0])
        viewer.show()
        got = []
        viewer.navigateRequested.connect(got.append)
        QTest.keyClick(viewer, Qt.Key.Key_Right)
        QTest.keyClick(viewer, Qt.Key.Key_Left)
        assert got == [1, -1]
        viewer.close()

    def test_arrow_keys_work_even_when_view_has_focus(self, images):
        """QGraphicsView 自己也处理方向键（滚动），快捷键必须抢在它前面。"""
        viewer = ImageViewer(images[0])
        viewer.show()
        viewer.view.setFocus()
        got = []
        viewer.navigateRequested.connect(got.append)
        QTest.keyClick(viewer.view, Qt.Key.Key_Right)
        assert got == [1]
        viewer.close()


class TestWindowNavigation:
    def test_next_moves_row_and_swaps_image(self, win, images):
        win.open_image_viewer(images[0], new_window=False)
        viewer = win._image_viewers[0]
        win._viewer_navigate(viewer, 1)
        assert viewer.image_path == images[1]
        assert win.table.currentIndex().row() == 2          # 视图行 = 数据行 1 + 表头
        assert win.image_panel._current_row == 1
        win._viewer_navigate(viewer, 1)                     # 跳过第 2 行的空图
        assert viewer.image_path == images[2]
        assert win.image_panel._current_row == 3

    def test_end_of_list_is_a_noop(self, win, images):
        win.open_image_viewer(images[0], new_window=False)
        viewer = win._image_viewers[0]
        win._viewer_navigate(viewer, -1)
        assert viewer.image_path == images[0]
        assert win.image_panel._current_row == 0

    def test_key_press_in_viewer_drives_the_window(self, win, images):
        win.open_image_viewer(images[0], new_window=False)
        viewer = win._image_viewers[0]
        QTest.keyClick(viewer, Qt.Key.Key_Right)
        assert viewer.image_path == images[1]
