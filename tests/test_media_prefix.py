"""Folder prefixes apply consistently to preview, viewers and copy queues."""
import json

import pandas as pd
import pytest
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QInputDialog, QMenu

from qtui import main_window, image_queue
from qtui.image_panel import ImagePreviewPanel
from qtui.image_utils import resolve_media_path
from qtui.i18n import tr

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def media(tmp_path):
    folder = tmp_path / "media folder"
    folder.mkdir()
    image = folder / "photo.png"
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    img.fill(0x112233)
    img.save(str(image))
    video = folder / "clip.mp4"
    video.write_bytes(b"")
    return folder, image, video


@pytest.mark.parametrize("relative_prefix", [True, False])
def test_prefix_resolves_images_and_videos(media, tmp_path, relative_prefix):
    folder, image, video = media
    prefix = folder.name if relative_prefix else str(folder)
    assert resolve_media_path(image.name, str(tmp_path), prefix) == str(image)
    assert resolve_media_path(video.name, str(tmp_path), prefix) == str(video)
    assert resolve_media_path(str(image), str(tmp_path), "wrong folder") == str(image)
    assert resolve_media_path("", str(tmp_path), prefix) is None
    assert resolve_media_path("nan", str(tmp_path), prefix) is None
    assert resolve_media_path("missing.png", str(tmp_path), prefix) is None
    assert resolve_media_path(folder.name, str(tmp_path)) is None


def test_changing_prefix_refreshes_panel_and_navigation(media, tmp_path):
    folder, image, video = media
    panel = ImagePreviewPanel()
    df = pd.DataFrame({"media": [image.name, "", video.name]})
    panel.set_context(df, "media", str(tmp_path))
    panel.set_current_row(0)
    assert panel._current_path() is None
    panel.set_context(df, "media", str(tmp_path), folder.name)
    assert panel._current_path() == str(image)
    assert panel.neighbor_with_image(1) == (2, str(video))
    panel.set_current_row(2)
    assert panel.video_view.path == str(video)
    panel.set_context(df, "media", str(tmp_path), "missing folder")
    assert panel._current_path() is None
    assert panel.video_view.path is None
    panel._pool.waitForDone()
    panel.close()


@pytest.fixture
def win(media, tmp_path):
    folder, image, video = media
    win = main_window.MainWindow()
    win.current_file = str(tmp_path / "table.csv")
    win.model.set_dataframe(pd.DataFrame({"media": [image.name, video.name]}))
    yield win
    win.image_panel._pool.waitForDone()
    win.model.modified = False
    win.close()


def test_set_column_prompts_and_remembers_prefix(win, media, monkeypatch):
    folder, image, _ = media
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: (str(folder), True))
    menu = QMenu()
    win._add_image_actions(menu, "media")
    next(a for a in menu.actions() if a.text() == tr("设为图片列")).trigger()
    win.image_panel.set_current_row(0)
    assert win.image_panel._current_path() == str(image)
    with open(main_window.FILE_CONFIG_PATH, encoding="utf-8") as f:
        saved = json.load(f)[win.current_file]
    assert saved["image_prefixes"] == {"media": str(folder)}
    win.image_prefixes = {}
    win._load_file_config()
    assert win.image_prefixes == {"media": str(folder)}
    menu = QMenu()
    win._add_image_actions(menu, "media")
    assert tr("设置图片列前缀...") in [a.text() for a in menu.actions()]


def test_cancel_keeps_existing_setting_and_blank_clears_prefix(win, media, monkeypatch):
    folder, _, _ = media
    win._set_image_column("media", str(folder))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("ignored", False))
    win._configure_image_column("media")
    assert win.image_prefixes["media"] == str(folder)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("", True))
    win._configure_image_column("media")
    assert win.image_prefixes["media"] == ""
    assert win.image_panel._path_prefix == ""


def test_cell_viewer_and_copy_queue_use_same_prefix(win, media, monkeypatch):
    folder, image, video = media
    win._set_image_column("media", str(folder))
    opened, queued = [], []
    monkeypatch.setattr(win, "open_image_viewer", opened.append)
    win._view_cell_image(win.model.index(1, 0))
    win._view_cell_image(win.model.index(2, 0))
    assert opened == [str(image), str(video)]

    class Queue:
        def __init__(self, paths, parent=None):
            queued.extend(paths)

        def show(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(image_queue, "FloatingImageQueue", Queue)
    win._open_image_queue("media")
    assert queued == [str(image)]


def test_old_file_config_does_not_reuse_previous_prefix(win):
    with open(main_window.FILE_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({win.current_file: {"image_columns": ["media"]}}, f)
    win.image_prefixes = {"media": "/previous/file"}
    win._load_file_config()
    assert win.image_prefixes == {}
