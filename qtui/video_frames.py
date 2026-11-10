"""Decode one silent preview frame at a time, without an external ffmpeg executable."""

from collections import OrderedDict

from PyQt6.QtCore import QObject, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtMultimedia import QMediaPlayer, QVideoSink


class VideoFrameLoader(QObject):
    loaded = pyqtSignal(str, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending = OrderedDict()
        self._active = None
        self._generation = 0
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(6000)
        self._timeout.timeout.connect(self._timed_out)

    def request(self, path, priority=False):
        if self._active is not None and self._active[0] == path:
            return
        self._pending[path] = None
        if priority:
            self._pending.move_to_end(path, last=False)
        QTimer.singleShot(0, self._start_next)

    def retain(self, paths):
        """Cancel work for rows that have left the visible filmstrip."""
        paths = set(paths)
        removed = set(self._pending) - paths
        for path in removed:
            self._pending.pop(path)
        if self._active is not None and self._active[0] not in paths:
            removed.add(self._active[0])
            self._release_active()
            QTimer.singleShot(0, self._start_next)
        return removed

    def clear(self):
        self._pending.clear()
        self._release_active()

    def _release_active(self):
        self._generation += 1
        self._timeout.stop()
        active, self._active = self._active, None
        if active is not None:
            _, player, sink = active
            player.stop()
            player.setVideoSink(None)
            player.setSource(QUrl())
            player.deleteLater()
            sink.deleteLater()

    def _start_next(self):
        if self._active is not None or not self._pending:
            return
        path, _ = self._pending.popitem(last=False)
        player = QMediaPlayer(self)  # No audio output: preview extraction is silent.
        sink = QVideoSink(self)
        self._generation += 1
        generation = self._generation
        self._active = (path, player, sink)
        sink.videoFrameChanged.connect(lambda frame: self._frame(generation, frame))
        player.errorOccurred.connect(lambda *_: self._finish(generation, QImage()))
        player.setVideoSink(sink)
        self._timeout.start()
        player.setSource(QUrl.fromLocalFile(path))
        if generation == self._generation and self._active is not None:
            player.play()

    def _frame(self, generation, frame):
        if generation != self._generation or not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return
        # Limit retained preview memory, while preserving the original aspect ratio.
        if image.width() > 960 or image.height() > 720:
            image = image.scaled(QSize(960, 720), Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        else:
            image = image.copy()
        # Release the decoder outside its frame callback.
        QTimer.singleShot(0, lambda: self._finish(generation, image))

    def _finish(self, generation, image):
        if generation != self._generation or self._active is None:
            return
        path = self._active[0]
        self._release_active()
        self.loaded.emit(path, image)
        QTimer.singleShot(0, self._start_next)

    def _timed_out(self):
        self._finish(self._generation, QImage())
