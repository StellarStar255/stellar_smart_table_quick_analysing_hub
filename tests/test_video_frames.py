"""Actual decoder regression: metadata alone must not count as a video preview."""
import shutil
import subprocess
import time

import pandas as pd
import pytest
from PyQt6.QtMultimedia import QMediaFormat, QMediaPlayer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from qtui.image_panel import ImagePreviewPanel

_app = QApplication.instance() or QApplication([])


def _wait_until(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate(), "Decoder did not produce the expected preview frame"


def test_real_mp4_frames_appear_before_play_and_change_with_row(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is needed only to generate synthetic test clips")
    formats = QMediaFormat().supportedFileFormats(QMediaFormat.ConversionMode.Decode)
    if QMediaFormat.FileFormat.MPEG4 not in formats:
        pytest.skip("This test environment has no MP4 decoding backend")
    paths = []
    for color in ("red", "blue"):
        path = str(tmp_path / f"{color}.mp4")
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"color=c={color}:s=160x120:r=10:d=1", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-y", path],
                       check=True, capture_output=True, timeout=15)
        paths.append(path)
    panel = ImagePreviewPanel()
    try:
        panel.set_context(pd.DataFrame({"video": paths}), "video", "")
        panel.set_current_row(0)
        _wait_until(lambda: panel.video_view._poster is not None
                    and panel._thumb_cache.get(paths[1]) is not None)
        frame = panel.video_view._poster.toImage()
        assert frame.pixelColor(20, 20).red() > 200
        assert frame.pixelColor(20, 20).blue() < 30
        assert panel._thumb_cache[paths[1]].toImage().pixelColor(20, 20).blue() > 200
        assert panel.video_view.player.position() == 0
        assert panel.video_view.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
        panel.set_current_row(1)
        _wait_until(lambda: panel.video_view._poster is not None)
        frame = panel.video_view._poster.toImage()
        assert frame.pixelColor(20, 20).blue() > 200
        assert frame.pixelColor(20, 20).red() < 30
    finally:
        panel.close()
