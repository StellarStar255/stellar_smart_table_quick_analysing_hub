"""Video routing, mixed-column navigation and player lifecycle."""
import pandas as pd
import pytest
from PyQt6.QtGui import QImage
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import QApplication, QMenu

from qtui.image_panel import ImagePreviewPanel
from qtui.image_utils import is_video
from qtui.image_viewer import ImageViewer
from qtui.i18n import tr
from qtui.video_player import VideoPlayer

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def media(tmp_path):
    video = tmp_path / "clip.MP4"
    video.write_bytes(b"")  # Routing tests do not require a particular codec/backend.
    image = tmp_path / "photo.png"
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    img.fill(0x112233)
    img.save(str(image))
    return str(video), str(image)


def test_mixed_column_routes_video_without_image_decoding(media, tmp_path, monkeypatch):
    video, image = media
    panel = ImagePreviewPanel()
    requested = []
    monkeypatch.setattr(panel, "_request_thumb", requested.append)
    panel.set_context(pd.DataFrame({"media": ["clip.MP4", "", "photo.png"]}),
                      "media", str(tmp_path))
    panel.set_current_row(0)
    assert video not in requested
    assert image in requested
    assert not panel.video_view.isHidden()
    assert panel.main_view.isHidden()
    assert panel.video_view.player.source().toLocalFile() == video
    assert panel.neighbor_with_image(1) == (2, image)
    assert panel._entries[0].img_label.text() == tr("▶ 视频")
    panel.set_current_row(2)
    assert panel.video_view.player.source().isEmpty()
    assert panel.video_view.isHidden()
    assert not panel.main_view.isHidden()
    panel.set_current_row(0)
    panel.set_context(None, None, "")
    assert panel.video_view.player.source().isEmpty()
    panel._pool.waitForDone()
    panel.close()


def test_video_menu_does_not_offer_image_copy(media, monkeypatch):
    panel = ImagePreviewPanel()
    menus, opened = [], []
    monkeypatch.setattr(QMenu, "exec", lambda menu, *_: menus.append(menu))
    panel.openImageRequested.connect(opened.append)
    panel._show_image_menu(media[0], None)
    assert [a.text() for a in menus[0].actions()] == [tr("打开视频")]
    menus[0].actions()[0].trigger()
    assert opened == [media[0]]
    panel.close()


def test_viewer_reuses_window_across_media_types(media):
    video, image = media
    viewer = ImageViewer(image)
    viewer.set_image(video)
    assert viewer.view is None
    player = viewer.video_view
    assert player.player.source().toLocalFile() == video
    viewer.set_image(image)
    assert player.player.source().isEmpty()
    assert viewer.video_view is None
    assert viewer.view is not None
    viewer.set_image(video)
    viewer.close()
    assert viewer.video_view.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState


def test_player_controls_and_errors():
    player = VideoPlayer()
    player._duration_changed(125000)
    assert player.seek.maximum() == 125000
    player._error(QMediaPlayer.Error.FormatError, "Unsupported codec")
    assert "Unsupported codec" in player.error_label.text()
    assert not player.error_label.isHidden()
    player.set_path("/missing/new.mp4")
    assert player.error_label.isHidden()
    assert player.seek.maximum() == 0
    assert player._time(125000) == "2:05"
    assert is_video("clip.MOV") and not is_video("photo.png")
    player.set_path(None)
    player.close()
