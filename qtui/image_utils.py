# -*- coding: utf-8 -*-
"""图片加载公共工具。

load_image: 统一用 QImageReader 解码，
  - 自动应用 EXIF 方向（手机竖拍照片不再横躺）；
  - 给定 max_size 时让解码器直接按目标尺寸输出（JPEG 可按 1/2~1/8 比例
    解码，比"全量解码后再 scaled()"快数倍、内存也小得多）。
"""

import os

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QImage, QImageReader, QImageIOHandler


VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm", ".wmv",
                    ".mpg", ".mpeg", ".3gp", ".mts", ".m2ts", ".ts", ".ogv"}


def is_video(path):
    return bool(path) and os.path.splitext(str(path))[1].lower() in VIDEO_EXTENSIONS


def resolve_media_path(raw, base_dir="", prefix="", must_exist=True):
    """Resolve local media, applying a directory prefix only to relative cells."""
    raw = str(raw).strip()
    if not raw or raw.lower() in ("nan", "none", "<na>"):
        return None
    raw = os.path.expanduser(raw)
    if not os.path.isabs(raw):
        raw = os.path.join(os.path.expanduser(prefix.strip()), raw)
    candidates = ([os.path.normpath(raw)] if os.path.isabs(raw) else
                  [os.path.abspath(os.path.join(base, raw))
                   for base in (base_dir or os.getcwd(), os.getcwd())])
    if not must_exist:
        return candidates[0]
    return next((path for path in candidates if os.path.isfile(path)), None)


def load_image(path, max_size=None):
    """读取图片为 QImage（失败返回 null QImage）。

    max_size: QSize 或 (w, h)；给定时按 KeepAspectRatio 缩到不超过该尺寸。
    """
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    if max_size is not None:
        if not isinstance(max_size, QSize):
            max_size = QSize(*max_size)
        src = reader.size()   # 变换前的尺寸
        if src.isValid() and not src.isEmpty():
            t = reader.transformation()
            rotated = bool(t & (QImageIOHandler.Transformation.TransformationRotate90
                                | QImageIOHandler.Transformation.TransformationRotate270))
            # 目标尺寸按"变换后"的宽高约束，再换算回变换前的解码尺寸
            limit = QSize(max_size.height(), max_size.width()) if rotated else max_size
            if src.width() > limit.width() or src.height() > limit.height():
                scaled = src.scaled(limit, Qt.AspectRatioMode.KeepAspectRatio)
                if scaled.isValid() and not scaled.isEmpty():
                    reader.setScaledSize(scaled)
    img = reader.read()
    if img is None:
        return QImage()
    return img
