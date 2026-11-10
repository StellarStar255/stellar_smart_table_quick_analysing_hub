"""Shared video preview with explicit playback and seeking controls."""

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QLabel, QStackedWidget, QSizePolicy

from qtui.i18n import tr
from qtui.video_frames import VideoFrameLoader


class _Poster(QLabel):
    doubleClicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event):
        self.doubleClicked.emit()
        event.accept()


class _VideoSurface(QVideoWidget):
    doubleClicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event):
        self.doubleClicked.emit()
        event.accept()


class VideoPlayer(QWidget):
    openRequested = pyqtSignal(str)

    def __init__(self, parent=None, frame_loader=None):
        super().__init__(parent)
        self.path = None
        self._poster = None
        self._owns_loader = frame_loader is None
        self.frame_loader = frame_loader if frame_loader is not None else VideoFrameLoader(self)
        self.frame_loader.loaded.connect(self._preview_loaded)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.surface = _VideoSurface(self)
        self.surface.setMinimumSize(120, 120)
        self.player.setVideoOutput(self.surface)
        self.surface.doubleClicked.connect(self._open)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.poster = _Poster()
        self.poster.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.poster.setMinimumSize(120, 120)
        self.poster.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.poster.doubleClicked.connect(self._open)
        self.display = QStackedWidget()
        self.display.addWidget(self.poster)
        self.display.addWidget(self.surface)
        layout.addWidget(self.display, 1)
        controls = QHBoxLayout()
        self.play_button = QPushButton(tr("播放"))
        self.play_button.clicked.connect(self.toggle_playback)
        controls.addWidget(self.play_button)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.seek.sliderReleased.connect(lambda: self.player.setPosition(self.seek.value()))
        controls.addWidget(self.seek, 1)
        self.time_label = QLabel("0:00 / 0:00")
        controls.addWidget(self.time_label)
        layout.addLayout(controls)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.errorOccurred.connect(self._error)

    def set_path(self, path):
        if path == self.path:
            return
        self.player.stop()
        if self._owns_loader:
            self.frame_loader.clear()
        self.path = path
        self._poster = None
        self.poster.clear()
        self.poster.setText(tr("正在生成视频预览…") if path else "")
        self.display.setCurrentWidget(self.poster)
        self.error_label.hide()
        self.play_button.setEnabled(bool(path))
        self.seek.setRange(0, 0)
        self.time_label.setText("0:00 / 0:00")
        self.player.setSource(QUrl.fromLocalFile(path) if path else QUrl())
        if path:
            self.frame_loader.request(path, priority=True)

    def _preview_loaded(self, path, image):
        if path != self.path:
            return
        if image.isNull():
            self.poster.setText(tr("无法生成视频预览，可尝试播放"))
            return
        self._poster = QPixmap.fromImage(image)
        self._rescale_poster()

    def _rescale_poster(self):
        if self._poster is not None:
            self.poster.setPixmap(self._poster.scaled(
                self.poster.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale_poster()

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.display.setCurrentWidget(self.surface)
            self.player.play()

    def _open(self):
        if self.path:
            self.player.pause()
            self.openRequested.emit(self.path)

    def _state_changed(self, state):
        self.play_button.setText(tr("暂停") if state == QMediaPlayer.PlaybackState.PlayingState
                                 else tr("播放"))

    @staticmethod
    def _time(ms):
        seconds = max(0, ms // 1000)
        return f"{seconds // 60}:{seconds % 60:02d}"

    def _position_changed(self, position):
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
        self.time_label.setText(f"{self._time(position)} / {self._time(self.player.duration())}")

    def _duration_changed(self, duration):
        self.seek.setRange(0, duration)
        self._position_changed(self.player.position())

    def _error(self, error, message):
        if error != QMediaPlayer.Error.NoError:
            self.error_label.setText(tr("无法播放视频: {}").format(message))
            self.error_label.show()

    def hideEvent(self, event):
        self.player.pause()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.player.stop()
        if self._owns_loader:
            self.frame_loader.clear()
        super().closeEvent(event)
