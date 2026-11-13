#!/usr/bin/env python3
"""Render and check navigator layout using isolated synthetic workbooks.

Run under Xvfb with QT_QPA_PLATFORM=xcb to exercise native Linux layouts.
Screenshots are test artifacts; no user configuration or documents are touched.
"""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import traceback

import PyQt6

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(
    Path(PyQt6.__file__).parent / "Qt6/plugins/platforms")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from PyQt6.QtCore import QPoint, QRect, QSettings, Qt, QTimer
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QToolBar, QDockWidget

from qtui import file_io, main_window
from qtui.window_manager import WindowManager


def rect_in(widget, window):
    return QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())


def assert_layout(window):
    panel = window.navigator_panel
    assert panel.parentWidget() is window.document_splitter
    assert not panel.isWindow()
    assert panel.isVisible()
    assert 200 <= panel.width() <= 360
    assert panel.height() == window.document_splitter.height()
    assert panel.geometry().right() < window.document_widget.geometry().left()
    sidebar_rect = rect_in(panel, window)
    assert not sidebar_rect.intersects(rect_in(window.table, window))
    if not window.menuBar().isNativeMenuBar():
        assert not sidebar_rect.intersects(rect_in(window.menuBar(), window))
    for toolbar in window.findChildren(QToolBar):
        if toolbar.isVisible() and not toolbar.isFloating():
            assert not sidebar_rect.intersects(rect_in(toolbar, window))
    for dock in (window.cell_preview_dock, window.image_dock):
        if dock.isVisible() and not dock.isFloating():
            assert not sidebar_rect.intersects(rect_in(dock, window))


def dark_palette():
    palette = QPalette()
    for role, color in (
            (QPalette.ColorRole.Window, "#252525"),
            (QPalette.ColorRole.WindowText, "#eeeeee"),
            (QPalette.ColorRole.Base, "#1e1e1e"),
            (QPalette.ColorRole.AlternateBase, "#252525"),
            (QPalette.ColorRole.Text, "#eeeeee"),
            (QPalette.ColorRole.Button, "#303030"),
            (QPalette.ColorRole.ButtonText, "#eeeeee"),
            (QPalette.ColorRole.Highlight, "#a35113"),
            (QPalette.ColorRole.HighlightedText, "#ffffff"),
            (QPalette.ColorRole.Mid, "#555555"),
            (QPalette.ColorRole.PlaceholderText, "#a0a0a0")):
        palette.setColor(role, QColor(color))
    return palette


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    normal_palette = app.palette()
    exit_code = [0]

    with tempfile.TemporaryDirectory(prefix="table-layout-") as temp:
        root = Path(temp)
        settings = QSettings(str(root / "ui.ini"), QSettings.Format.IniFormat)
        main_window._make_settings = lambda: settings
        main_window.FILE_CONFIG_PATH = str(root / "file-config.json")
        file_io.RECENT_FILES_PATH = str(root / "recent-files.json")
        manager = WindowManager(app)

        def run():
            try:
                # Recover from the old navigator's floating geometry as well.
                donor = main_window.MainWindow(restore_session=False)
                old = QDockWidget("Legacy navigator", donor)
                old.setObjectName("windowNavigatorDock")
                donor.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, old)
                old.setFloating(True)
                old.setGeometry(0, 50, 268, 200)
                legacy_state = donor.saveState()
                donor.close()
                settings.setValue("window/state", legacy_state)

                df = pd.DataFrame({
                    "case_id": [f"xm3600-{i:05d}" for i in range(1, 31)],
                    "question": ["Describe this image."] * 30,
                    "image_path": [f"images/xm3600-{i:05d}.jpg" for i in range(1, 31)],
                    "audio_path": [""] * 30,
                    "ground_truth": ["A"] * 30,
                })
                for folder, name in (("MMBench-CN", "index.xlsx"),
                                     ("Image-Recognition", "index.xlsx"),
                                     ("Evaluation", "weekly-review-with-a-long-filename.xlsx")):
                    path = root / "Datasets" / folder / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    df.to_excel(path, index=False)
                    window = manager.create_window()
                    window._update_check_timer.stop()
                    manager.open_file(str(path), source=window)
                    window.table.setColumnWidth(0, 140)
                    window.table.setColumnWidth(1, 210)
                    window.table.setColumnWidth(2, 200)
                window = manager.windows[0]
                manager.activate(window)
                window.model.setData(window.model.index(1, 1), "Updated description")
                for theme, palette in (("light", normal_palette), ("dark", dark_palette())):
                    app.setPalette(palette)
                    for width, height in ((1280, 800), (800, 600)):
                        window.resize(width, height)
                        app.processEvents()
                        assert_layout(window)
                        filename = args.output_dir / f"embedded-{theme}-{width}.png"
                        assert window.grab().save(str(filename))
                        window.navigator_panel.hide_button.click()
                        app.processEvents()
                        assert not window.navigator_panel.isVisible()
                        assert not window.navigator_action.isChecked()
                        window.navigator_action.trigger()
                        app.processEvents()
                        assert_layout(window)
                        assert window.navigator_action.isChecked()
                        window.navigator_panel.mode_button.click()
                        app.processEvents()
                        assert manager.navigator.isVisible()
                        assert not window.navigator_panel.isVisible()
                        assert manager.navigator.grab().save(str(args.output_dir / f"floating-{theme}.png"))
                        manager.navigator.close()
                        app.processEvents()
                        assert not window.navigator_action.isChecked()
                        window.navigator_action.trigger()
                        app.processEvents()
                        assert manager.navigator.isVisible()
                        assert window.navigator_action.isChecked()
                        manager.navigator.mode_button.click()
                        app.processEvents()
                        assert_layout(window)
                        print(f"PASS: {theme} {width}x{height}; no overlap after hide/show and mode switching", flush=True)
                print("PASS: legacy dock state, three real Excel workbooks, two matching filenames, elided long name", flush=True)
            except BaseException:
                traceback.print_exc()
                exit_code[0] = 1
            finally:
                for window in list(manager.windows):
                    window.model.modified = False
                    window.close()
                manager.navigator.close()
                app.exit(exit_code[0])

        QTimer.singleShot(0, run)
        app.exec()
    return exit_code[0]


if __name__ == "__main__":
    sys.exit(main())
