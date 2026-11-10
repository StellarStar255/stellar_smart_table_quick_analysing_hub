"""Compare media columns against the same visible row, including after filters/sorts."""
import json

import pandas as pd
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from qtui import main_window
from qtui.image_panel import ImagePreviewPanel
from qtui.video_player import VideoPlayer
from qtui.video_frames import VideoFrameLoader

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def win(tmp_path):
    paths = []
    for i in range(6):
        path = tmp_path / f"photo{i}.png"
        image = QImage(16, 12, QImage.Format.Format_RGB32)
        image.fill(0x224466 + i)
        image.save(str(path))
        paths.append(str(path))
    win = main_window.MainWindow()
    win.current_file = str(tmp_path / "table.csv")
    win.model.set_dataframe(pd.DataFrame({"left": paths[:3], "right": paths[3:]}))
    win.jump_to_cell(1, 0)
    yield win, paths
    for viewer in win._image_viewers:
        viewer.close()
    for panel in win._image_panels.values():
        panel._pool.waitForDone()
    win.model.modified = False
    win.close()


def test_columns_split_vertically_and_track_current_row(win):
    win, paths = win
    win._set_image_column("left")
    win._set_image_column("right")
    assert win.image_columns == {"left", "right"}
    assert win.image_splitter.orientation() == Qt.Orientation.Vertical
    assert win.image_splitter.count() == 2
    assert [win.image_splitter.widget(i).column_title.text() for i in range(2)] == ["left", "right"]
    assert [p._current_path() for p in win._image_panels.values()] == [paths[1], paths[4]]
    win.jump_to_cell(2, 1)
    assert [p._current_path() for p in win._image_panels.values()] == [paths[2], paths[5]]
    # Clicking a thumbnail in either panel updates both columns.
    win._image_panels["right"].rowActivated.emit(0)
    assert [p._current_path() for p in win._image_panels.values()] == [paths[0], paths[3]]
    assert all(p._strip_scroll.isHidden() for p in win._image_panels.values())
    win._image_panels["right"]._strip_toggle.click()
    assert not win._image_panels["right"]._strip_scroll.isHidden()


def test_changing_prefix_does_not_remove_other_column_and_settings_persist(win):
    win, _ = win
    win._set_image_column("left", "/left-prefix")
    win._set_image_column("right", "/right-prefix")
    left = win._image_panels["left"]
    win._set_image_column("right", "/changed-prefix")
    assert win._image_panels["left"] is left
    assert left._path_prefix == "/left-prefix"
    assert win._image_panels["right"]._path_prefix == "/changed-prefix"
    with open(main_window.FILE_CONFIG_PATH, encoding="utf-8") as f:
        entry = json.load(f)[win.current_file]
    assert entry["image_columns"] == ["left", "right"]
    win.image_columns.clear()
    win._load_file_config()
    win._sync_image_panel()
    assert list(win._image_panels) == ["left", "right"]


def test_unsetting_one_column_preserves_other_and_last_can_be_replaced(win):
    win, paths = win
    win._set_image_column("left")
    win._set_image_column("right")
    right = win._image_panels["right"]
    win._unset_image_column("left")
    assert win.image_panel is right
    assert win.image_splitter.count() == 1
    assert right._current_path() == paths[4]
    assert not right._strip_scroll.isHidden()
    win._unset_image_column("right")
    assert win.image_dock.isHidden()
    assert win.image_splitter.count() == 1
    win._set_image_column("left")
    assert win.image_splitter.count() == 1
    assert win.image_panel._current_path() == paths[1]


def test_viewer_navigation_uses_its_own_column_and_moves_all_panels(win):
    win, paths = win
    win._set_image_column("left")
    win._set_image_column("right")
    win.open_image_viewer(paths[4], colname="right")
    viewer = win._image_viewers[-1]
    win._viewer_navigate(viewer, 1)
    assert viewer.image_path == paths[5]
    assert [p._current_path() for p in win._image_panels.values()] == [paths[2], paths[5]]
    # Removing its column must not make the viewer navigate in another column.
    win._unset_image_column("right")
    win._viewer_navigate(viewer, -1)
    assert viewer.image_path == paths[5]


def test_sort_or_filter_replacement_keeps_columns_aligned(win):
    win, paths = win
    win._set_image_column("left")
    win._set_image_column("right")
    win.model.set_dataframe(win.model.df.iloc[[2, 0]].reset_index(drop=True))
    win.jump_to_cell(0, 0)
    assert [p._current_path() for p in win._image_panels.values()] == [paths[2], paths[5]]
    win.model.set_dataframe(win.model.df[["right", "left"]])
    assert list(win._image_panels) == ["right", "left"]
    assert win.image_splitter.widget(0)._image_col == "right"


def test_cell_viewer_keeps_column_identity_even_if_two_columns_share_a_path(win):
    win, paths = win
    win.model.setData(win.model.index(1, 1), paths[0])
    win._set_image_column("left")
    win._set_image_column("right")
    win.jump_to_cell(0, 1)
    win._view_cell_image(win.model.index(1, 1))
    viewer = win._image_viewers[-1]
    assert viewer._preview_col == "right"
    win._viewer_navigate(viewer, 1)
    assert viewer.image_path == paths[4]


def test_video_poster_and_filmstrip_use_real_frame_and_ignore_stale_results(tmp_path):
    path = str(tmp_path / "video.mp4")
    (tmp_path / "video.mp4").touch()
    panel = ImagePreviewPanel()
    panel.set_context(pd.DataFrame({"video": [path]}), "video", "")
    panel.set_current_row(0)
    image = QImage(80, 60, QImage.Format.Format_RGB32)
    image.fill(0x11CC33)
    panel._video_frames.loaded.emit(path, image)
    assert panel.video_view._poster is not None
    assert panel.video_view.display.currentWidget() is panel.video_view.poster
    assert not panel._entries[0].img_label.pixmap().isNull()
    panel.video_view.set_path("/other/video.mp4")
    panel._video_frames.loaded.emit(path, image)
    assert panel.video_view._poster is None
    panel.close()


def test_preview_queue_prioritizes_current_row_and_cancels_old_rows():
    loader = VideoFrameLoader()
    loader.request("old.mp4")
    loader.request("next.mp4")
    loader.request("current.mp4", priority=True)
    assert list(loader._pending) == ["current.mp4", "old.mp4", "next.mp4"]
    removed = loader.retain(["current.mp4", "next.mp4"])
    assert removed == {"old.mp4"}
    assert list(loader._pending) == ["current.mp4", "next.mp4"]
    loader.clear()
    assert not loader._pending


def test_collapsed_filmstrip_decodes_only_current_video(tmp_path):
    paths = []
    for i in range(3):
        path = tmp_path / f"video{i}.mp4"
        path.touch()
        paths.append(str(path))
    panel = ImagePreviewPanel()
    panel.set_context(pd.DataFrame({"video": paths}), "video", "")
    panel.set_current_row(1)
    panel.set_compact(True)
    assert list(panel._video_frames._pending) == [paths[1]]
    panel._strip_toggle.click()
    assert set(panel._video_frames._pending) == set(paths)
    panel.close()
