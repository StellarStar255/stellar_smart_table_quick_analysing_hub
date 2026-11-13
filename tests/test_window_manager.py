"""Independent documents, shared navigation, and cancellable window lifecycle."""
import pandas as pd
import pytest
from PyQt6.QtCore import QSettings, Qt, QPoint, QRect
from PyQt6.QtWidgets import QApplication, QMessageBox, QToolBar, QDockWidget

from qtui import main_window
from qtui.window_manager import WindowManager

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def manager(tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(main_window, "_make_settings", lambda: settings)
    monkeypatch.setattr(main_window.MainWindow, "_offer_header_promotion", lambda self: None)

    def run_sync(dialog, work, callback):
        callback(work())

    monkeypatch.setattr(main_window.LoadingProgressDialog, "run_in_background", run_sync)
    mgr = WindowManager(_app)
    yield mgr
    for window in list(mgr.windows):
        window.model.modified = False
        window.close()
    mgr.navigator.close()
    mgr.navigator.deleteLater()


@pytest.fixture
def files(tmp_path):
    paths = []
    for name, value in (("one", 10), ("two", 20)):
        path = tmp_path / f"{name}.xlsx"
        pd.DataFrame({"value": [value]}).to_excel(path, index=False)
        paths.append(str(path))
    return paths


def test_open_keeps_documents_and_edits_independent(manager, files):
    first = manager.create_window()
    assert manager.open_file(files[0], source=first) is first
    first.model.setData(first.model.index(1, 0), "99", Qt.ItemDataRole.EditRole)
    second = first.load_file(files[1])
    assert len(manager.windows) == 2
    assert first.current_file == files[0]
    assert first.model.df.iat[0, 0] == 99
    assert first.model.modified
    assert second.model.df.iat[0, 0] == 20
    assert not second.model.modified
    assert first._excel_file is not second._excel_file
    second.model.setData(second.model.index(1, 0), "42", Qt.ItemDataRole.EditRole)
    assert second.save_file()
    assert pd.read_excel(files[1]).iat[0, 0] == 42
    assert pd.read_excel(files[0]).iat[0, 0] == 10
    assert first.model.modified
    assert first.recent_files == second.recent_files


def test_duplicate_paths_and_symlinks_activate_existing(manager, files, tmp_path):
    first = manager.create_window(files[0])
    link = tmp_path / "alias.xlsx"
    link.symlink_to(files[0])
    assert manager.open_file(str(link)) is first
    assert manager.active_window is first
    assert len(manager.windows) == 1


def test_new_window_does_not_discard_unsaved_document(manager):
    first = manager.create_window()
    first.model.modified = True
    second = first.new_file()
    assert second is not first
    assert first.model.modified
    assert not second.model.modified
    assert len(manager.windows) == 2


def test_navigator_switches_modes_and_survives_owner_close(manager, files):
    first = manager.create_window(files[0])
    second = manager.create_window(files[1])
    for window in manager.windows:
        assert window.navigator_panel.list.count() == 2
    manager.set_floating(True)
    assert manager.navigator.isVisible()
    assert all(not w.navigator_panel.isVisible() for w in manager.windows)
    manager.navigator.activate_item(manager.navigator.list.item(0))
    assert manager.active_window is first
    assert first.close()
    assert manager.windows == [second]
    assert manager.navigator.isVisible()
    assert manager.navigator.list.count() == 1
    manager.set_floating(False)
    assert not manager.navigator.isVisible()
    assert second.navigator_panel.isVisible()
    manager.set_floating(True)
    assert second.close()
    assert not manager.navigator.isVisible()
    assert not manager.windows


def test_cancel_close_and_quit_preserve_modified_window(manager, monkeypatch):
    first = manager.create_window()
    second = manager.create_window()
    first.model.modified = True
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args: QMessageBox.StandardButton.Cancel)
    assert not first.close()
    assert manager.windows == [first, second]
    assert not manager.close_all()
    assert manager.windows == [first, second]
    assert manager.active_window is first


def test_save_cannot_overwrite_document_in_another_window(manager, files, monkeypatch):
    first = manager.create_window(files[0])
    second = manager.create_window(files[1])
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    assert not first._do_save(files[1])
    assert warnings
    assert second.model.df.iat[0, 0] == 20
    assert pd.read_excel(files[1]).iat[0, 0] == 20


def test_multiselect_and_drop_open_all_files(manager, files, monkeypatch):
    first = manager.create_window()
    monkeypatch.setattr(main_window.QFileDialog, "getOpenFileNames",
                        lambda *args: (files, ""))
    first.open_file_dialog()
    assert {w.current_file for w in manager.windows} == set(files)
    first._open_dropped_files(files)
    assert len(manager.windows) == 2


def test_cancelled_save_blocks_close_and_file_close_is_local(manager, files, monkeypatch):
    first = manager.create_window(files[0])
    second = manager.create_window(files[1])
    first.model.modified = True
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(first, "save_file", lambda: False)
    assert not first.close()
    assert first in manager.windows
    first.model.modified = False
    first.close_file()
    assert first.current_file is None
    assert second.current_file == files[1]
    assert len(manager.windows) == 2
    assert manager.navigator.list.count() == 2
    assert manager.close_all()
    assert not manager.windows


def _window_rect(widget, window):
    return QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())


def _assert_sidebar_layout(window):
    sidebar = window.navigator_panel
    assert not sidebar.isWindow()
    assert sidebar.parentWidget() is window.document_splitter
    assert sidebar.isVisible()
    assert sidebar.width() >= 200
    assert sidebar.height() == window.document_splitter.height()
    assert sidebar.geometry().right() < window.document_widget.geometry().left()
    nav_rect = _window_rect(sidebar, window)
    assert not nav_rect.intersects(_window_rect(window.table, window))
    if not window.menuBar().isNativeMenuBar():
        assert not nav_rect.intersects(_window_rect(window.menuBar(), window))
    for toolbar in window.findChildren(QToolBar):
        if toolbar.isVisible() and not toolbar.isFloating():
            assert not nav_rect.intersects(_window_rect(toolbar, window))
    for dock in (window.cell_preview_dock, window.image_dock):
        if dock.isVisible() and not dock.isFloating():
            assert not nav_rect.intersects(_window_rect(dock, window))


@pytest.mark.parametrize("size", [(720, 480), (960, 640), (1400, 860)])
def test_sidebar_geometry_after_resize_collapse_and_mode_switch(manager, size):
    window = manager.create_window()
    window.resize(*size)
    _app.processEvents()
    _assert_sidebar_layout(window)
    width_with_sidebar = window.document_widget.width()
    window.navigator_panel.hide_button.click()
    _app.processEvents()
    assert not window.navigator_panel.isVisible()
    assert window.document_widget.width() > width_with_sidebar
    manager.show_navigator(window)
    _app.processEvents()
    _assert_sidebar_layout(window)
    window.navigator_panel.mode_button.click()
    _app.processEvents()
    assert manager.navigator.isVisible()
    assert not window.navigator_panel.isVisible()
    manager.navigator.mode_button.click()
    _app.processEvents()
    _assert_sidebar_layout(window)


def test_old_floating_dock_state_cannot_position_sidebar_over_table(manager):
    donor = main_window.MainWindow(restore_session=False)
    old_dock = QDockWidget("Old navigator", donor)
    old_dock.setObjectName("windowNavigatorDock")
    donor.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, old_dock)
    old_dock.setFloating(True)
    old_dock.setGeometry(0, 50, 268, 200)
    state = donor.saveState()
    donor.model.modified = False
    donor.close()
    donor._settings.setValue("window/state", state)
    window = manager.create_window()
    _app.processEvents()
    _assert_sidebar_layout(window)


def test_long_paths_do_not_expand_sidebar_and_rows_survive_refresh(manager, tmp_path):
    window = manager.create_window()
    window.current_file = str(tmp_path / ("folder-" * 30) / ("report-" * 30 + ".xlsx"))
    window._update_title()
    _app.processEvents()
    panel = window.navigator_panel
    item = panel.list.item(0)
    assert item.toolTip() == window.current_file
    assert panel.width() <= 360
    manager.refresh()
    assert panel.list.item(0) is item
    _assert_sidebar_layout(window)
    # Synthetic path is deliberately not a real workbook; do not persist it.
    window.current_file = None


def test_toolbar_and_view_action_restore_collapsed_sidebar(manager):
    window = manager.create_window()
    _app.processEvents()
    action = window.navigator_action
    toolbar = window.findChild(QToolBar, "main_toolbar")
    assert toolbar.actions()[0] is action
    assert toolbar.widgetForAction(action).isVisible()
    view_menu = next(a.menu() for a in window.menuBar().actions()
                     if a.text() == main_window.tr("视图"))
    assert action in view_menu.actions()
    assert action.isChecked()
    window.navigator_panel.hide_button.click()
    _app.processEvents()
    assert not action.isChecked()
    action.trigger()
    _app.processEvents()
    assert action.isChecked()
    _assert_sidebar_layout(window)
    # Repeated toolbar/menu toggles share the same state and shortcut.
    for _ in range(3):
        action.trigger()
        _app.processEvents()
        assert not window.navigator_panel.isVisible()
        assert not action.isChecked()
        action.trigger()
        _app.processEvents()
        _assert_sidebar_layout(window)
        assert action.isChecked()


def test_closed_floating_navigator_reopens_from_any_document_toolbar(manager):
    first = manager.create_window()
    second = manager.create_window()
    manager.set_floating(True)
    _app.processEvents()
    for _ in range(3):
        assert first.navigator_action.isChecked()
        assert second.navigator_action.isChecked()
        assert manager.navigator.close()
        _app.processEvents()
        assert not first.navigator_action.isChecked()
        assert not second.navigator_action.isChecked()
        second.navigator_action.trigger()
        _app.processEvents()
        assert manager.navigator.isVisible()
        assert first.navigator_action.isChecked()
        assert second.navigator_action.isChecked()
