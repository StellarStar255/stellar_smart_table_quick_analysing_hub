"""Shared video preview with explicit playback and seeking controls."""

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QLabel

from qtui.i18n import tr


class _VideoSurface(QVideoWidget):
    doubleClicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event):
        self.doubleClicked.emit()
        event.accept()


class VideoPlayer(QWidget):
    openRequested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = None
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.surface = _VideoSurface(self)
        self.surface.setMinimumSize(120, 120)
        self.player.setVideoOutput(self.surface)
        self.surface.doubleClicked.connect(self._open)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.surface, 1)
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
        self.path = path
        self.error_label.hide()
        self.play_button.setEnabled(bool(path))
        self.seek.setRange(0, 0)
        self.time_label.setText("0:00 / 0:00")
        self.player.setSource(QUrl.fromLocalFile(path) if path else QUrl())

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
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
        super().closeEvent(event)
