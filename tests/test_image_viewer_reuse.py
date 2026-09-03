"""图片查看器窗口复用：双击多张图只用同一个窗口，Cmd/Ctrl 才另开一个。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui.image_viewer import ImageViewer
from qtui.main_window import MainWindow


@pytest.fixture
def images(tmp_path):
    paths = []
    for i, size in enumerate([(40, 30), (60, 20), (10, 10)]):
        img = QImage(size[0], size[1], QImage.Format.Format_RGB32)
        img.fill(0x336699 + i)
        p = tmp_path / f"img{i}.png"
        img.save(str(p))
        paths.append(str(p))
    return paths


@pytest.fixture
def win():
    w = MainWindow()
    w.df = pd.DataFrame({'a': [1, 2, 3]})
    w.model.set_dataframe(w.df)
    yield w
    for v in list(w._image_viewers):
        v.close()
    w.model.modified = False
    w.close()


def test_set_image_swaps_content_in_place(images):
    viewer = ImageViewer(images[0])
    viewer.show()
    _app.processEvents()
    geo = viewer.geometry()
    viewer.set_image(images[1])
    _app.processEvents()
    assert viewer.image_path == images[1]
    assert viewer.windowTitle() == os.path.basename(images[1])
    assert viewer.geometry() == geo          # 换图不动窗口大小/位置
    assert viewer._pixmap.width() == 60
    viewer.close()


def test_missing_image_after_valid_one_shows_message(images, tmp_path):
    viewer = ImageViewer(images[0])
    viewer.set_image(str(tmp_path / "nope.png"))
    assert viewer.view is None and viewer._item is None
    viewer._fit()                            # 不应抛异常
    viewer.close()


def test_double_click_reuses_single_window(win, images):
    for p in images:
        win.open_image_viewer(p, new_window=False)
    assert len(win._image_viewers) == 1
    assert win._image_viewers[0].image_path == images[-1]


def test_modifier_opens_second_window(win, images):
    win.open_image_viewer(images[0], new_window=False)
    win.open_image_viewer(images[1], new_window=True)
    assert len(win._image_viewers) == 2
    assert [v.image_path for v in win._image_viewers] == images[:2]


def test_closed_viewer_is_dropped_and_new_one_opens(win, images):
    win.open_image_viewer(images[0], new_window=False)
    win._image_viewers[0].close()
    win.open_image_viewer(images[1], new_window=False)
    assert len(win._image_viewers) == 1
    assert win._image_viewers[0].image_path == images[1]
