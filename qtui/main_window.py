# -*- coding: utf-8 -*-
"""
Smart Table Hub PyQt6 主窗口。

对应旧版 Tkinter 实现的核心功能：
文件读写 / Sheet 管理 / 表格编辑 / 筛选 / 排序 / 剪贴板 / 查找替换 / 统计 / 缩放。

Qt 的 QTableView 是虚拟化渲染，因此旧版的行分页、列分页、增量加载、
虚拟滚动条等全部性能补丁在这里都不需要了。
"""

import csv
import io
import os
from collections import OrderedDict

import numpy as np
import pandas as pd

from PyQt6.QtCore import Qt, QTimer, QSettings
from PyQt6.QtGui import QAction, QKeySequence, QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QMainWindow, QTableView, QVBoxLayout, QHBoxLayout, QWidget, QLabel,
    QComboBox, QToolBar, QPushButton, QMessageBox, QFileDialog,
    QInputDialog, QAbstractItemView, QAbstractItemDelegate, QStyledItemDelegate,
    QStyle, QMenu, QCheckBox, QDialog,
    QDialogButtonBox, QListWidget, QListWidgetItem, QPlainTextEdit,
    QApplication, QFrame, QDockWidget, QLineEdit,
)

from . import file_io, filter_engine
from .pandas_model import PandasTableModel

# 视图第 0 行是虚拟表头行；所有视图行 <-> 数据行换算共用此常量
HEADER_ROWS = PandasTableModel.HEADER_ROWS
from .dialogs import LoadingProgressDialog
from .filter_dialog import FilterDialog
from .find_dialog import FindReplaceDialog
from .image_panel import ImagePreviewPanel
from qtui.i18n import tr

# 背景色选项（与 Tkinter 版一致的六色 + 清除）
CELL_COLORS = [
    ("红色", "#a04040"), ("橙色", "#b07030"), ("黄色", "#a89a30"),
    ("绿色", "#3f7a44"), ("蓝色", "#3a6ea5"), ("紫色", "#7a4f9d"),
]

FILE_CONFIG_PATH = os.path.expanduser("~/.smart_table_hub/qt_file_config.json")

MAX_SHEET_CACHE = 5
AUTO_SAVE_DELAY_MS = 30000
DEFAULT_ROWS, DEFAULT_COLS = 100, 26


def _file_config_entry(path):
    """读取单个文件的持久化配置（图片列、最后停留的 sheet 等）。"""
    import json
    try:
        with open(FILE_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get(os.path.abspath(path), {})
    except (OSError, ValueError):
        return {}


def _col_letter(n):
    """0 -> A, 25 -> Z, 26 -> AA"""
    result = ""
    n += 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


class _FastCellDelegate(QStyledItemDelegate):
    """精简单元格绘制：跳过 QStyle 全套状态机，只画背景/选中/文字。

    默认代理每格要取 7~8 种角色并跑完整样式流程，是大数据量滚动
    卡顿的最后一块开销；这里只取 3 种角色，且缓存文本截断结果。
    编辑器相关行为全部继承默认实现，不受影响。
    """

    _PAD = 4  # 文字左右内边距

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self._model = model
        self._elide_cache = {}   # (text, width) -> 截断后的文本

    def paint(self, painter, option, index):
        model = self._model
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        # 背景：选中 > 单元格色/行高亮 > 斑马纹
        if selected:
            painter.fillRect(rect, option.palette.highlight())
        else:
            bg = model.data(index, Qt.ItemDataRole.BackgroundRole)
            if bg is not None:
                painter.fillRect(rect, bg)
            elif index.row() % 2:
                painter.fillRect(rect, option.palette.alternateBase())

        text = model.data(index, Qt.ItemDataRole.DisplayRole)
        if text:
            if selected:
                pen_color = option.palette.highlightedText().color()
            else:
                fg = (model.data(index, Qt.ItemDataRole.ForegroundRole)
                      if model.formulas else None)
                pen_color = fg if fg is not None else option.palette.text().color()
            painter.setPen(pen_color)
            is_header = index.row() == 0
            if is_header:
                # 表头行加粗（自绘委托不走模型的 FontRole）
                bold = QFont(option.font)
                bold.setBold(True)
                painter.setFont(bold)
                metrics = QFontMetrics(bold)
            else:
                painter.setFont(option.font)
                metrics = option.fontMetrics
            avail = rect.width() - 2 * self._PAD
            # 粗体宽度不同，缓存键必须区分表头行，且按对应字体度量省略
            key = (text, avail, is_header)
            elided = self._elide_cache.get(key)
            if elided is None:
                elided = metrics.elidedText(
                    text, Qt.TextElideMode.ElideRight, avail)
                if len(self._elide_cache) > 100_000:
                    self._elide_cache.clear()
                self._elide_cache[key] = elided
            align = model.data(index, Qt.ItemDataRole.TextAlignmentRole)
            painter.drawText(rect.adjusted(self._PAD, 0, -self._PAD, 0),
                             align, elided)

        # 当前单元格边框（保留可见的焦点指示）
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setPen(option.palette.highlight().color())
            painter.drawRect(rect.adjusted(0, 0, -1, -1))

    def clear_cache(self):
        self._elide_cache.clear()


class _ExcelTableView(QTableView):
    """回车提交单元格编辑后，自动跳到下一行同列并继续编辑（Excel 风格）。"""

    def closeEditor(self, editor, hint):
        if hint == QAbstractItemDelegate.EndEditHint.SubmitModelCache:
            super().closeEditor(editor, QAbstractItemDelegate.EndEditHint.NoHint)
            cur = self.currentIndex()
            if cur.isValid():
                nxt = self.model().index(cur.row() + 1, cur.column())
                if nxt.isValid():
                    self.setCurrentIndex(nxt)
                    self.scrollTo(nxt)
                    # 等旧编辑器完全关闭后再打开新编辑器
                    QTimer.singleShot(0, self._edit_current)
            return
        super().closeEditor(editor, hint)

    def _edit_current(self):
        idx = self.currentIndex()
        if idx.isValid() and self.state() != QAbstractItemView.State.EditingState:
            self.edit(idx)


class MainWindow(QMainWindow):

    def __init__(self, initial_file=None):
        super().__init__()
        self.setWindowTitle("Smart Table Hub (PyQt)")
        self.resize(1400, 860)

        # ---------- 数据状态 ----------
        self.current_file = None
        self._excel_file = None          # pd.ExcelFile（懒加载）
        self.sheet_names = []
        self.current_sheet = None
        self._sheet_cache = OrderedDict()  # LRU: sheet名 -> DataFrame
        self.active_filters = []
        self.sheet_filters = {}          # sheet名 -> {'filters':…, 'original_df':…, 'idx_map':…}
        self._sheet_formulas = {}        # sheet名 -> {(row, col): "=..."}
        self.original_df = None          # 筛选前的完整数据
        self._filtered_idx_map = None    # 筛选行 -> original_df 索引标签
        self._suspended_formulas = None  # 筛选期间挂起的公式（original_df 坐标）
        self._sort_orders = {}           # 列名 -> 上次是否升序
        self.image_columns = set()       # 标记为图片列的列名
        self._image_queue_win = None
        self._image_viewers = []
        self._analysis_win = None
        self.recent_files, self.auto_save = file_io.load_recent_files()

        # ---------- 模型与视图 ----------
        self.model = PandasTableModel()
        self.model.dataChanged.connect(self._on_cell_edited)

        self.table = _ExcelTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        # 大数据量下的滚动性能：
        # 单元格不换行（省去每格文本布局计算），按像素平滑滚动
        # （默认按整格滚动，一格动辄上百像素，每步都要大面积重绘）
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._cell_delegate = _FastCellDelegate(self.model, self.table)
        self.table.setItemDelegate(self._cell_delegate)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.horizontalHeader().setDefaultSectionSize(140)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self._show_col_menu)
        self.table.horizontalHeader().sectionDoubleClicked.connect(self._rename_column_at)
        self.table.verticalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.verticalHeader().customContextMenuRequested.connect(self._show_row_menu)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_cell_menu)

        # ---------- 缩放 ----------
        self._base_font_size = 13
        self._base_row_height = 28
        self._zoom = 1.0

        # ---------- UI ----------
        self._build_toolbar()
        self._build_python_toolbar()
        self._build_filter_bar()
        self._build_central()
        self._build_docks()
        self._build_menu()
        self._build_statusbar()
        self._apply_zoom()
        self.table.selectionModel().currentChanged.connect(self._on_current_cell_changed)
        # 列重命名联动：同步筛选条件/original_df；失败时状态栏提示
        self.model.columnRenamed.connect(self._after_column_rename)
        self.model.renameFailed.connect(self.update_statusbar)

        # 数据结构变化（排序/筛选/粘贴/插删行列都会替换 df 对象）时，
        # 图片面板必须换成新 df，否则行号和图片对不上
        for sig in (self.model.modelReset, self.model.rowsInserted,
                    self.model.rowsRemoved, self.model.columnsInserted,
                    self.model.columnsRemoved):
            sig.connect(self._sync_image_panel)

        # ---------- 自动保存 ----------
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(True)
        self._auto_save_timer.setInterval(AUTO_SAVE_DELAY_MS)
        self._auto_save_timer.timeout.connect(self._do_auto_save)

        # ---------- 拖拽 ----------
        self.setAcceptDrops(True)

        self._find_dialog = None

        # ---------- 恢复上次的窗口布局（面板位置/大小、工具栏、窗口几何） ----------
        self._settings = QSettings("SmartTableHub", "SmartTableHubQt")
        geo = self._settings.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        state = self._settings.value("window/state")
        if state is not None:
            self.restoreState(state)

        self.new_file(confirm=False)

        if initial_file:
            QTimer.singleShot(100, lambda: self.load_file(initial_file))
        elif self.recent_files and os.path.exists(self.recent_files[0]):
            # 恢复上次会话（与 Tkinter 版行为一致）
            QTimer.singleShot(100, lambda: self.load_file(self.recent_files[0]))

        # ---------- 启动后静默检查更新 ----------
        from qtui.updater import UpdateManager
        self._update_manager = UpdateManager(self)
        QTimer.singleShot(3000, lambda: self._update_manager.check(silent=True))

    # ================= UI 构建 =================

    def _build_menu(self):
        menubar = self.menuBar()

        # 文件
        file_menu = menubar.addMenu(tr("文件"))
        self._add_action(file_menu, tr("新建"), self.new_file, "Ctrl+N")
        self._add_action(file_menu, tr("打开..."), self.open_file_dialog, "Ctrl+O")
        self._recent_menu = file_menu.addMenu(tr("打开最近的文件"))
        self._rebuild_recent_menu()
        self._add_action(file_menu, tr("保存"), self.save_file, "Ctrl+S")
        self._add_action(file_menu, tr("保存为..."), self.save_as_copy, "Ctrl+Shift+S")
        file_menu.addSeparator()
        self._add_action(file_menu, tr("新建Sheet..."), self.create_new_sheet)
        self._add_action(file_menu, tr("保存为新Sheet..."), self.save_as_new_sheet)
        self._add_action(file_menu, tr("删除Sheet..."), self.delete_sheets)
        file_menu.addSeparator()
        self._add_action(file_menu, tr("导入CSV..."), self.import_csv)
        self._add_action(file_menu, tr("导出CSV..."), self.export_csv)
        file_menu.addSeparator()
        self._add_action(file_menu, tr("退出"), self.close, "Ctrl+Q")

        # 编辑
        edit_menu = menubar.addMenu(tr("编辑"))
        self._add_action(edit_menu, tr("撤销"), self.undo, "Ctrl+Z")
        self._add_action(edit_menu, tr("重做"), self.redo, "Ctrl+Shift+Z")
        edit_menu.addSeparator()
        self._add_action(edit_menu, tr("复制"), self.copy_selection, "Ctrl+C")
        self._add_action(edit_menu, tr("粘贴"), self.paste_selection, "Ctrl+V")
        self._add_action(edit_menu, tr("删除选中行"), self.delete_selected_rows)
        edit_menu.addSeparator()
        self._add_action(edit_menu, tr("全选"), self.select_all, "Ctrl+A")
        self._add_action(edit_menu, tr("查找替换..."), self.open_find_dialog, "Ctrl+F")

        # 视图
        view_menu = menubar.addMenu(tr("视图"))
        self._add_action(view_menu, tr("放大"), self.zoom_in, "Ctrl+=")
        self._add_action(view_menu, tr("缩小"), self.zoom_out, "Ctrl+-")
        self._add_action(view_menu, tr("重置缩放"), self.zoom_reset, "Ctrl+0")
        view_menu.addSeparator()
        view_menu.addAction(self.cell_preview_dock.toggleViewAction())
        view_menu.addAction(self.image_dock.toggleViewAction())
        toggle_py = self.python_toolbar.toggleViewAction()
        toggle_py.setText(tr("Python数据分析工具栏"))
        view_menu.addAction(toggle_py)
        view_menu.addSeparator()
        self._add_action(view_menu, tr("自适应列宽"), self.refit_columns)
        view_menu.addSeparator()
        # 语言子菜单（两种语言都写明，方便任一语言环境的用户找到）
        lang_menu = view_menu.addMenu("语言 / Language")
        from qtui import i18n
        current = i18n.current_language()
        for code, label in ((i18n.LANG_ZH, "中文"), (i18n.LANG_EN, "English")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(code == current)
            action.triggered.connect(
                lambda checked=False, c=code: self._switch_language(c))
            lang_menu.addAction(action)

        # 分析
        analysis_menu = menubar.addMenu(tr("分析"))
        self._add_action(analysis_menu, tr("Python数据分析..."), self.open_python_analysis)

        # 数据
        data_menu = menubar.addMenu(tr("数据"))
        self._add_action(data_menu, tr("排序..."), self.sort_dialog)
        self._add_action(data_menu, tr("筛选..."), self.open_filter_dialog)
        self._add_action(data_menu, tr("清除所有筛选"), self.clear_all_filters, "Ctrl+Shift+L")
        data_menu.addSeparator()
        self._add_action(data_menu, tr("插入行"), self.insert_row)
        self._add_action(data_menu, tr("插入列"), self.insert_column)
        self._add_action(data_menu, tr("删除行"), self.delete_selected_rows)
        self._add_action(data_menu, tr("删除列"), self.delete_selected_columns)

        # 统计
        stats_menu = menubar.addMenu(tr("统计"))
        self._add_action(stats_menu, tr("描述性统计"), self.show_statistics)
        stats_menu.addSeparator()
        for label, func in (("求和", "sum"), ("平均值", "mean"), ("最大值", "max"),
                            ("最小值", "min"), ("计数", "count")):
            self._add_action(stats_menu, tr(label),
                             lambda checked=False, f=func: self.apply_function(f))

        # 帮助
        help_menu = menubar.addMenu(tr("帮助"))
        self._add_action(help_menu, tr("检查更新..."),
                         lambda: self._update_manager.check(silent=False))
        self._add_action(help_menu, tr("关于"), self._show_about)

    def _switch_language(self, lang):
        from qtui import i18n
        if lang == i18n.current_language():
            self._build_menu_refresh_language_checks(lang)
            return
        i18n.set_language(lang)
        box = QMessageBox(self)
        box.setWindowTitle(tr("切换语言"))
        box.setText(tr("语言设置已保存，重启应用后生效。\n是否立即重启？"))
        restart = box.addButton(tr("立即重启"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("稍后手动重启"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is restart:
            self._restart_app()
        else:
            self._build_menu_refresh_language_checks(lang)

    def _build_menu_refresh_language_checks(self, lang):
        """让语言子菜单的勾选状态反映已保存的选择。"""
        from qtui import i18n
        for action in self.findChildren(QAction):
            if action.text() in ("中文", "English"):
                action.setChecked(
                    (action.text() == "中文") == (lang == i18n.LANG_ZH))

    def _restart_app(self):
        import subprocess
        import sys as _sys
        self.close()
        if getattr(_sys, "frozen", False):
            args = [_sys.executable]
        else:
            args = [_sys.executable,
                    os.path.join(os.path.dirname(os.path.dirname(
                        os.path.abspath(__file__))),
                        "smart_table_quick_analysing_hub.py")]
        subprocess.Popen(args, start_new_session=True)
        QTimer.singleShot(0, QApplication.quit)

    def _show_about(self):
        from version import __version__, APP_NAME, GITHUB_REPO
        QMessageBox.about(
            self, tr("关于 {}").format(APP_NAME),
            f"<b>{APP_NAME}</b> v{__version__}<br><br>"
            + tr("智能表格快速分析工具") + "<br>"
            f"<a href='https://github.com/{GITHUB_REPO}'>"
            f"github.com/{GITHUB_REPO}</a>")

    def _add_action(self, menu, text, slot, shortcut=None):
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _build_toolbar(self):
        tb = QToolBar(tr("主工具栏"))
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        self.addToolBar(tb)
        for text, slot in (("新建", self.new_file), ("打开", self.open_file_dialog),
                           ("保存", self.save_file), ("保存为", self.save_as_copy)):
            btn = QPushButton(tr(text))
            btn.clicked.connect(slot)
            tb.addWidget(btn)
        tb.addSeparator()
        for text, slot in (("撤销", self.undo), ("重做", self.redo),
                           ("查找", self.open_find_dialog)):
            btn = QPushButton(tr(text))
            btn.clicked.connect(slot)
            tb.addWidget(btn)
        tb.addSeparator()
        for text, slot in (("插入行", self.insert_row), ("删除行", self.delete_selected_rows),
                           ("排序", self.sort_dialog), ("筛选", self.open_filter_dialog),
                           ("统计", self.show_statistics)):
            btn = QPushButton(tr(text))
            btn.clicked.connect(slot)
            tb.addWidget(btn)
        tb.addSeparator()

        tb.addWidget(QLabel(" Sheet: "))
        self.sheet_combo = QComboBox()
        self.sheet_combo.setMinimumWidth(150)
        self.sheet_combo.currentTextChanged.connect(self._on_sheet_combo_changed)
        tb.addWidget(self.sheet_combo)
        add_sheet_btn = QPushButton("+")
        add_sheet_btn.setFixedWidth(28)
        add_sheet_btn.clicked.connect(self.create_new_sheet)
        tb.addWidget(add_sheet_btn)
        tb.addSeparator()

        self.copy_headers_cb = QCheckBox(tr("复制列名"))
        self.copy_headers_cb.setChecked(True)
        tb.addWidget(self.copy_headers_cb)
        self.auto_save_cb = QCheckBox(tr("自动保存"))
        self.auto_save_cb.setChecked(self.auto_save)
        self.auto_save_cb.toggled.connect(self._on_auto_save_toggled)
        tb.addWidget(self.auto_save_cb)

    def _build_filter_bar(self):
        self.filter_bar = QWidget()
        self.filter_bar_layout = QHBoxLayout(self.filter_bar)
        self.filter_bar_layout.setContentsMargins(8, 2, 8, 2)
        self.filter_bar_layout.setSpacing(6)
        self.filter_bar.hide()

    def _build_central(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.filter_bar)
        layout.addWidget(self.table, 1)
        self.setCentralWidget(central)

    def _build_docks(self):
        # 单元格内容预览（可编辑，方便查看/修改长文本）
        self.cell_preview_dock = QDockWidget(tr("单元格内容"), self)
        self.cell_preview_dock.setObjectName("cell_preview_dock")
        self.cell_preview_text = QPlainTextEdit()
        self.cell_preview_text.setPlaceholderText(tr("选中单元格后在此查看/编辑内容"))
        self.cell_preview_dock.setWidget(self.cell_preview_text)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.cell_preview_dock)
        self._preview_loading = False
        self._preview_save_timer = QTimer(self)
        self._preview_save_timer.setSingleShot(True)
        self._preview_save_timer.setInterval(400)
        self._preview_save_timer.timeout.connect(self._save_cell_preview)
        self.cell_preview_text.textChanged.connect(self._on_preview_text_changed)

        # 图片预览
        self.image_dock = QDockWidget(tr("图片预览"), self)
        self.image_dock.setObjectName("image_dock")
        self.image_panel = ImagePreviewPanel()
        self.image_panel.rowActivated.connect(self._on_image_row_activated)
        self.image_panel.openImageRequested.connect(self.open_image_viewer)
        self.image_dock.setWidget(self.image_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.image_dock)
        self.image_dock.hide()

    def _build_python_toolbar(self):
        self.python_toolbar = QToolBar(tr("Python分析"))
        self.python_toolbar.setObjectName("python_toolbar")
        self.python_toolbar.setMovable(False)
        self.addToolBarBreak()
        self.addToolBar(self.python_toolbar)
        self.python_toolbar.addWidget(QLabel(" " + tr("Python分析") + ": "))
        open_btn = QPushButton(tr("打开分析窗口"))
        open_btn.clicked.connect(self.open_python_analysis)
        self.python_toolbar.addWidget(open_btn)
        self.python_toolbar.addSeparator()
        for label, code in (
            ("描述统计", "print(df.describe(include='all'))"),
            ("数据类型", "print(df.dtypes)"),
            ("缺失值统计", "print(df.isnull().sum())"),
            ("数据形状", "print(f'{df.shape[0]} 行 x {df.shape[1]} 列')"),
            ("唯一值计数", "print(df.nunique())"),
        ):
            btn = QPushButton(tr(label))
            btn.clicked.connect(lambda checked=False, c=code: self._quick_python(c))
            self.python_toolbar.addWidget(btn)
        self.python_toolbar.addSeparator()
        self.python_toolbar.addWidget(QLabel(" " + tr("快速执行") + ": "))
        self._python_quick_entry = QLineEdit()
        self._python_quick_entry.setMinimumWidth(320)
        self._python_quick_entry.setPlaceholderText(tr("df 可用，如: print(df['列名'].value_counts())"))
        self._python_quick_entry.returnPressed.connect(
            lambda: self._quick_python(self._python_quick_entry.text()))
        self.python_toolbar.addWidget(self._python_quick_entry)
        run_btn = QPushButton(tr("运行"))
        run_btn.clicked.connect(
            lambda: self._quick_python(self._python_quick_entry.text()))
        self.python_toolbar.addWidget(run_btn)
        self.python_toolbar.hide()

    def _quick_python(self, code):
        if not code.strip():
            return
        import io as _io
        import contextlib
        import traceback
        buf = _io.StringIO()
        env = {"df": self.model.df.copy(), "pd": pd, "np": np}
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                try:
                    # 表达式直接打印结果
                    result = eval(code, env)
                    if result is not None:
                        print(result)
                except SyntaxError:
                    exec(code, env)
        except Exception:
            buf.write(traceback.format_exc())
        output = buf.getvalue().strip() or tr("(无输出)")
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Python 快速分析"))
        dialog.resize(760, 480)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setFont(QFont("Menlo", 12))
        text.setPlainText(output)
        layout.addWidget(text)
        dialog.show()

    def open_python_analysis(self):
        try:
            from .python_analysis import PythonAnalysisWindow
        except Exception as e:
            QMessageBox.critical(self, tr("Python分析"), tr("分析窗口加载失败: {}").format(e))
            return
        if self._analysis_win is None or not self._analysis_win.isVisible():
            self._analysis_win = PythonAnalysisWindow(self, parent=self)
        self._analysis_win.show()
        self._analysis_win.raise_()

    def add_sheet_from_df(self, df, name):
        """供分析窗口回调：把 DataFrame 存为新 sheet。"""
        if not isinstance(df, pd.DataFrame):
            raise TypeError("需要 DataFrame")
        name = str(name)[:31] or tr("结果")
        if not self.sheet_names:
            self.sheet_names = ["Sheet1"]
            self.current_sheet = "Sheet1"
            self._cache_sheet("Sheet1", self.model.df)
        base = name
        i = 1
        while name in self.sheet_names:
            name = f"{base}_{i}"
            i += 1
        self.sheet_names.append(name)
        self._cache_sheet(name, df.reset_index(drop=True))
        self._refresh_sheet_combo()
        self._mark_modified()
        self.update_statusbar(tr("已添加 Sheet: {}（保存文件后写入文件）").format(name))

    def _build_statusbar(self):
        self.statusBar().showMessage(tr("就绪"))

    def update_statusbar(self, message=None):
        if message is None:
            df = self.model.df
            message = tr("共 {} 行 × {} 列").format(len(df), len(df.columns))
            if self.active_filters:
                message += tr("（已筛选，{} 个条件）").format(len(self.active_filters))
        self.statusBar().showMessage(message)

    # ================= 文件操作 =================

    def new_file(self, confirm=True):
        if confirm and not self._check_save_before_discard():
            return
        cols = [_col_letter(i) for i in range(10)]
        df = pd.DataFrame(np.full((DEFAULT_ROWS, len(cols)), np.nan), columns=cols)
        self.current_file = None
        self._excel_file = None
        self.sheet_names = []
        self.current_sheet = None
        self._sheet_cache.clear()
        self._reset_filter_state()
        self.sheet_filters.clear()
        self._sheet_formulas.clear()
        self.model.cell_colors.clear()
        self.model.set_dataframe(df)
        self.model.modified = False
        self.image_columns = set()
        self._update_image_context()
        self.image_dock.hide()
        self._refresh_sheet_combo()
        self._update_title()
        self.update_statusbar(tr("新建空白表格"))

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("打开文件"), "",
            tr("支持的文件 (*.xlsx *.xls *.csv *.tsv);;Excel (*.xlsx *.xls);;CSV (*.csv *.tsv);;所有文件 (*)"))
        if path:
            self.load_file(path)

    def load_file(self, path):
        if not os.path.exists(path):
            QMessageBox.warning(self, tr("打开文件"), tr("文件不存在:\n{}").format(path))
            self.recent_files = [p for p in self.recent_files if p != path]
            self._save_recent()
            return
        if not self._check_save_before_discard():
            return

        ext = os.path.splitext(path)[1].lower()
        dialog = LoadingProgressDialog(self, tr("加载中"), tr("正在加载 {} ...").format(os.path.basename(path)))
        dialog.set_indeterminate()

        def work():
            if ext in (".xlsx", ".xls"):
                excel_file, sheets = file_io.load_workbook_lazy(path)
                # 恢复上次停留的 sheet
                target = _file_config_entry(path).get("last_sheet")
                active = target if target in sheets else sheets[0]
                df = file_io.read_sheet(excel_file, active)
                formulas = file_io.read_sheet_formulas(path, active)
                return ("excel", excel_file, sheets, active, df, formulas)
            df = file_io.read_csv_any_encoding(path)
            return ("csv", None, [], None, df, {})

        def done(result):
            if result is None:
                QMessageBox.critical(self, tr("打开文件"), tr("加载失败:\n{}").format(path))
                return
            kind, excel_file, sheets, active_sheet, df, formulas = result
            self.current_file = path
            self._excel_file = excel_file
            self.sheet_names = sheets
            self.current_sheet = active_sheet if sheets else None
            self._sheet_cache.clear()
            self.sheet_filters.clear()
            self._sheet_formulas.clear()
            self._reset_filter_state()
            if self.current_sheet:
                self._cache_sheet(self.current_sheet, df)
            self.model.cell_colors.clear()
            # 本应用（新坐标系）保存的文件带标记，公式结果可放心写入；
            # 无标记（Excel/旧版来源）保守处理：错误不覆盖文件缓存值
            self.model.set_dataframe(
                df, formulas=formulas,
                from_file=not file_io.xlsx_has_coord_marker(path))
            self.model.modified = False
            self._load_file_config()
            self._update_image_context()
            self._refresh_sheet_combo()
            self._update_title()
            self.recent_files = file_io.add_recent_file(self.recent_files, path)
            self._save_recent()
            self._rebuild_recent_menu()
            self.update_statusbar(tr("已打开 {}：{} 行 × {} 列").format(os.path.basename(path), len(df), len(df.columns)))

        dialog.run_in_background(work, done)

    def save_file(self):
        if not self.current_file:
            self.save_as_copy()
            return
        self._do_save(self.current_file)

    def save_as_copy(self):
        default = ""
        if self.current_file:
            base, ext = os.path.splitext(self.current_file)
            default = tr("{}_副本{}").format(base, ext or ".xlsx")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("保存为"), default, "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        self._do_save(path, switch_to=False)

    def _collect_all_sheets(self):
        """收集所有 sheet 的数据引用（当前 sheet 用筛选前数据）。

        只取内存中已有的数据，不读盘；未缓存的 sheet 返回在 missing 列表里，
        由调用方在后台线程读取（大文件读盘放 UI 线程会冻结界面）。
        返回 (sheets, order, missing)。
        """
        current_full = self.original_df if self.original_df is not None else self.model.df
        if not self.sheet_names:
            return {"Sheet1": current_full}, ["Sheet1"], []
        sheets, missing = {}, []
        for name in self.sheet_names:
            if name == self.current_sheet:
                sheets[name] = current_full
            elif name in self._sheet_cache:
                sheets[name] = self._sheet_cache[name]
            elif name in self.sheet_filters and self.sheet_filters[name].get("original_df") is not None:
                sheets[name] = self.sheet_filters[name]["original_df"]
            elif self._excel_file is not None:
                missing.append(name)
        return sheets, list(self.sheet_names), missing

    def _do_save(self, path, switch_to=True):
        ext = os.path.splitext(path)[1].lower()
        dialog = LoadingProgressDialog(self, tr("保存中"), tr("正在保存 {} ...").format(os.path.basename(path)))
        dialog.set_indeterminate()

        if ext == ".csv":
            df = self.original_df if self.original_df is not None else self.model.df
            work = lambda: file_io.save_csv(path, df)
        else:
            sheets, order, missing = self._collect_all_sheets()
            excel_file = self._excel_file
            formulas_map = dict(self._sheet_formulas)
            # 当前 sheet 用权威公式表：筛选中为挂起的 original_df 坐标公式，
            # 与保存的 original_df 数据一致
            current_formulas = self._live_formulas()
            if self.current_sheet:
                formulas_map[self.current_sheet] = dict(current_formulas)
            elif not self.sheet_names and current_formulas:
                formulas_map["Sheet1"] = dict(current_formulas)

            def work():
                # 未缓存 sheet 的读盘和写盘都在后台线程完成，UI 不冻结
                for name in missing:
                    dialog.report(tr("正在读取 {} ...").format(name))
                    sheets[name] = file_io.read_sheet(excel_file, name)
                file_io.save_workbook(
                    path, sheets, order, formulas_map,
                    progress_cb=lambda name, i, total: dialog.report(
                        tr("正在写入 {} ({}/{}) ...").format(name, i, total)))

        def done(result):
            # run_in_background 失败时回调 None；成功且 work 返回 None 无法区分，
            # 因此 work 内部约定返回 True
            if result is not True:
                QMessageBox.critical(self, tr("保存"), tr("保存失败，详情见终端输出"))
                return
            if switch_to or path == self.current_file:
                self.current_file = path
                if ext != ".csv":
                    self._excel_file, self.sheet_names = file_io.load_workbook_lazy(path)
                    if self.current_sheet not in self.sheet_names:
                        self.current_sheet = self.sheet_names[0]
                    self._refresh_sheet_combo()
                self.model.modified = False
                self._update_title()
            self.recent_files = file_io.add_recent_file(self.recent_files, path)
            self._save_recent()
            self._rebuild_recent_menu()
            self._save_file_config()   # 记住当前 sheet，重开时恢复
            self.update_statusbar(tr("已保存: {}").format(path))

        inner = work
        dialog.run_in_background(lambda: (inner(), True)[1], done)

    def import_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("导入CSV"), "", tr("CSV (*.csv *.tsv);;所有文件 (*)"))
        if path:
            self.load_file(path)

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("导出CSV"), "", "CSV (*.csv)")
        if not path:
            return
        df = self.original_df if self.original_df is not None else self.model.df
        try:
            file_io.save_csv(path, df)
            self.update_statusbar(tr("已导出: {}").format(path))
        except Exception as e:
            QMessageBox.critical(self, tr("导出CSV"), tr("导出失败: {}").format(e))

    # ---------- 最近文件 ----------

    def _rebuild_recent_menu(self):
        self._recent_menu.clear()
        for path in self.recent_files:
            action = QAction(path, self)
            action.triggered.connect(lambda checked=False, p=path: self.load_file(p))
            self._recent_menu.addAction(action)
        if self.recent_files:
            self._recent_menu.addSeparator()
            clear = QAction(tr("清除记录"), self)
            clear.triggered.connect(self._clear_recent)
            self._recent_menu.addAction(clear)

    def _clear_recent(self):
        self.recent_files = []
        self._save_recent()
        self._rebuild_recent_menu()

    def _save_recent(self):
        try:
            file_io.save_recent_files(self.recent_files, self.auto_save_cb.isChecked())
        except OSError as e:
            print(f"保存最近文件记录失败: {e}")

    # ---------- 修改标记 / 自动保存 ----------

    def _update_title(self):
        name = os.path.basename(self.current_file) if self.current_file else tr("未命名")
        star = " *" if self.model.modified else ""
        self.setWindowTitle(f"Smart Table Hub (PyQt) - {name}{star}")

    def _mark_modified(self):
        self.model.modified = True
        self._update_title()
        if self.auto_save_cb.isChecked() and self.current_file:
            self._auto_save_timer.start()

    def _on_auto_save_toggled(self, checked):
        self.auto_save = checked
        self._save_recent()
        if not checked:
            self._auto_save_timer.stop()

    def _do_auto_save(self):
        if self.model.modified and self.current_file:
            self._do_save(self.current_file)

    def _check_save_before_discard(self):
        """返回 False 表示用户取消操作。"""
        if not self.model.modified:
            return True
        ret = QMessageBox.question(
            self, tr("未保存的修改"), tr("当前数据已修改，是否保存？"),
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel)
        if ret == QMessageBox.StandardButton.Cancel:
            return False
        if ret == QMessageBox.StandardButton.Save:
            self.save_file()
        return True

    # ================= Sheet 管理 =================

    def _refresh_sheet_combo(self):
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        self.sheet_combo.addItems(self.sheet_names)
        if self.current_sheet:
            self.sheet_combo.setCurrentText(self.current_sheet)
        self.sheet_combo.setEnabled(bool(self.sheet_names))
        self.sheet_combo.blockSignals(False)

    def _cache_sheet(self, name, df):
        self._sheet_cache[name] = df
        self._sheet_cache.move_to_end(name)
        while len(self._sheet_cache) > MAX_SHEET_CACHE:
            self._sheet_cache.popitem(last=False)

    def _on_sheet_combo_changed(self, name):
        if not name or name == self.current_sheet:
            return
        self.switch_sheet(name)

    def switch_sheet(self, name):
        # 保存当前 sheet 状态（含筛选和公式）
        dropped_view_formulas = 0
        if self.current_sheet:
            full = self.original_df if self.original_df is not None else self.model.df
            self._cache_sheet(self.current_sheet, full)
            self._sheet_formulas[self.current_sheet] = dict(self._live_formulas())
            if self.active_filters:
                # 筛选中输入的公式（视图坐标）无法随 sheet 保存，转为静态值
                dropped_view_formulas = len(self.model.formulas)
            if self.active_filters:
                self.sheet_filters[self.current_sheet] = {
                    "filters": list(self.active_filters),
                    "original_df": self.original_df,
                }
            else:
                self.sheet_filters.pop(self.current_sheet, None)

        # 加载目标 sheet
        formulas = self._sheet_formulas.get(name)
        if name in self._sheet_cache:
            df = self._sheet_cache[name]
        elif self._excel_file is not None:
            try:
                df = file_io.read_sheet(self._excel_file, name)
                self._cache_sheet(name, df)
                if formulas is None and self.current_file:
                    formulas = file_io.read_sheet_formulas(self.current_file, name)
            except Exception as e:
                QMessageBox.critical(self, tr("切换Sheet"), tr("读取 sheet 失败: {}").format(e))
                return
        else:
            df = pd.DataFrame()

        self.current_sheet = name
        self._reset_filter_state()

        # 恢复该 sheet 的筛选
        saved = self.sheet_filters.get(name)
        if saved and saved["filters"]:
            self.original_df = df
            self.active_filters = list(saved["filters"])
            # 公式保持挂起（original_df 坐标），清除筛选时恢复
            self._suspended_formulas = dict(formulas) if formulas else None
            filtered, idx_map = filter_engine.apply_filters(df, self.active_filters)
            self._filtered_idx_map = idx_map
            self.model.set_dataframe(filtered)
        else:
            self.model.set_dataframe(
                df, formulas=formulas,
                from_file=bool(self.current_file)
                and not file_io.xlsx_has_coord_marker(self.current_file))
        self.model.cell_colors.clear()
        self._refresh_sheet_combo()
        self._rebuild_filter_bar()
        self._update_image_context()
        self._save_file_config()   # 记住最后停留的 sheet
        if dropped_view_formulas:
            self.update_statusbar(
                tr("上一 sheet 筛选中输入的 {} 个公式已转为静态值").format(
                    dropped_view_formulas))
        else:
            self.update_statusbar()

    def create_new_sheet(self):
        name, ok = QInputDialog.getText(self, tr("新建Sheet"), tr("Sheet 名称:"))
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(c in name for c in r'\/?*[]:'):
            QMessageBox.warning(self, tr("新建Sheet"), tr(r"名称不能包含 \ / ? * [ ] :"))
            return
        if name in self.sheet_names:
            QMessageBox.warning(self, tr("新建Sheet"), tr("Sheet '{}' 已存在").format(name))
            return
        cols = [_col_letter(i) for i in range(10)]
        df = pd.DataFrame(np.full((DEFAULT_ROWS, len(cols)), np.nan), columns=cols)
        if not self.sheet_names:
            # 当前是单表（CSV/空白），把现有数据变成 Sheet1
            self.sheet_names = ["Sheet1"]
            self.current_sheet = "Sheet1"
            self._cache_sheet("Sheet1", self.model.df)
        self.sheet_names.append(name)
        self._cache_sheet(name, df)
        self.switch_sheet(name)
        self._mark_modified()

    def save_as_new_sheet(self):
        name, ok = QInputDialog.getText(self, tr("保存为新Sheet"), tr("新 Sheet 名称:"))
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self.sheet_names:
            QMessageBox.warning(self, tr("保存为新Sheet"), tr("Sheet '{}' 已存在").format(name))
            return
        if not self.sheet_names:
            self.sheet_names = ["Sheet1"]
            self.current_sheet = "Sheet1"
            self._cache_sheet("Sheet1", self.model.df)
        self.sheet_names.append(name)
        self._cache_sheet(name, self.model.df.copy())
        self._mark_modified()
        self._refresh_sheet_combo()
        self.update_statusbar(tr("已添加 Sheet: {}（保存文件后生效）").format(name))

    def delete_sheets(self):
        if len(self.sheet_names) <= 1:
            QMessageBox.warning(self, tr("删除Sheet"), tr("至少需要保留一个 Sheet"))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("删除Sheet"))
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(tr("勾选要删除的 Sheet:")))
        lst = QListWidget()
        for name in self.sheet_names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            lst.addItem(item)
        layout.addWidget(lst)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        to_delete = [lst.item(i).text() for i in range(lst.count())
                     if lst.item(i).checkState() == Qt.CheckState.Checked]
        if not to_delete:
            return
        if len(to_delete) >= len(self.sheet_names):
            QMessageBox.warning(self, tr("删除Sheet"), tr("不能删除所有 Sheet"))
            return
        # 删除前确保保留的 sheet 数据都在内存里（保存时不再依赖原文件）
        for name in self.sheet_names:
            if name not in to_delete and name != self.current_sheet \
                    and name not in self._sheet_cache and self._excel_file is not None:
                self._sheet_cache[name] = file_io.read_sheet(self._excel_file, name)
        self.sheet_names = [n for n in self.sheet_names if n not in to_delete]
        for name in to_delete:
            self._sheet_cache.pop(name, None)
            self.sheet_filters.pop(name, None)
            self._sheet_formulas.pop(name, None)
        if self.current_sheet in to_delete:
            self.switch_sheet(self.sheet_names[0])
        else:
            self._refresh_sheet_combo()
        self._mark_modified()
        self.update_statusbar(tr("已删除 {} 个 Sheet（保存文件后生效）").format(len(to_delete)))

    # ================= 编辑 =================

    def undo(self):
        if self._text_editor_focused():
            QApplication.focusWidget().undo()
            return
        if self.model.undo():
            self._mark_modified()
            self.update_statusbar(tr("已撤销"))

    def redo(self):
        if self._text_editor_focused():
            QApplication.focusWidget().redo()
            return
        if self.model.redo():
            self._mark_modified()
            self.update_statusbar(tr("已重做"))

    def _text_editor_focused(self):
        from PyQt6.QtWidgets import QLineEdit, QTextEdit, QPlainTextEdit
        w = QApplication.focusWidget()
        return isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit))

    def _on_cell_edited(self, top_left, bottom_right, roles=None):
        # 仅背景色刷新（行高亮、单元格着色）不算数据修改
        if roles and Qt.ItemDataRole.DisplayRole not in roles \
                and Qt.ItemDataRole.EditRole not in roles:
            return
        # 筛选状态下把编辑同步回 original_df（视图行 0 是表头行，
        # 重命名联动由 columnRenamed 信号统一处理，这里跳过）
        if self.original_df is not None and self._filtered_idx_map:
            for view_row in range(top_left.row(), bottom_right.row() + 1):
                if view_row == 0:
                    continue
                row = view_row - HEADER_ROWS
                if row >= len(self._filtered_idx_map):
                    continue
                orig_label = self._filtered_idx_map[row]
                for col in range(top_left.column(), bottom_right.column() + 1):
                    colname = self.model.df.columns[col]
                    try:
                        self.original_df.loc[orig_label, colname] = self.model.df.iat[row, col]
                    except (ValueError, TypeError):
                        self.original_df[colname] = self.original_df[colname].astype(object)
                        self.original_df.loc[orig_label, colname] = self.model.df.iat[row, col]
                    # 编辑覆盖了挂起的公式单元格 -> 该公式作废，保留新值
                    if self._suspended_formulas:
                        self._suspended_formulas.pop((orig_label, col), None)
        self._mark_modified()

    def select_all(self):
        if self._text_editor_focused():
            QApplication.focusWidget().selectAll()
            return
        self.table.selectAll()

    # ---------- 行列操作 ----------

    def _require_no_filter(self):
        if self.active_filters:
            QMessageBox.information(self, tr("提示"), tr("筛选状态下不支持增删行/列，请先清除筛选"))
            return False
        return True

    def _current_row(self):
        """当前数据行（0 基）；视图第 0 行是表头行。"""
        idx = self.table.currentIndex()
        if not idx.isValid():
            return len(self.model.df)
        return max(0, idx.row() - HEADER_ROWS)

    def _current_col(self):
        idx = self.table.currentIndex()
        return idx.column() if idx.isValid() else len(self.model.df.columns)

    def insert_row(self, position=None):
        if not self._require_no_filter():
            return
        pos = position if position is not None else self._current_row()
        self.model.insert_row(pos)
        self._mark_modified()

    def insert_column(self, position=None):
        if not self._require_no_filter():
            return
        name, ok = QInputDialog.getText(self, tr("插入列"), tr("列名（留空自动命名）:"))
        if not ok:
            return
        pos = position if position is not None else self._current_col()
        self.model.insert_column(pos, name.strip() or None)
        self._mark_modified()

    def delete_selected_rows(self):
        if not self._require_no_filter():
            return
        # 视图行 0 是表头行，不可删除；转换为数据行坐标
        rows = sorted({i.row() - HEADER_ROWS for i in self.table.selectionModel().selectedIndexes()
                       if i.row() > 0})
        if not rows:
            return
        ret = QMessageBox.question(self, tr("删除行"), tr("确定删除选中的 {} 行？").format(len(rows)))
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.model.remove_rows(rows)
        self._mark_modified()
        self.update_statusbar()

    def delete_selected_columns(self):
        if not self._require_no_filter():
            return
        cols = sorted({i.column() for i in self.table.selectionModel().selectedIndexes()})
        if not cols:
            return
        names = [str(self.model.df.columns[c]) for c in cols]
        ret = QMessageBox.question(
            self, tr("删除列"),
            tr("确定删除列: {}？").format(
                ", ".join(names[:5]) + ("..." if len(names) > 5 else "")))
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.model.remove_columns(cols)
        self._mark_modified()
        self.update_statusbar()

    def _rename_column_at(self, col_idx):
        """进入列名编辑：列名住在视图第 1 行，字母坐标行不可编辑。

        双击字母列头 / 右键"重命名列"都跳转到第 1 行的对应单元格编辑。
        """
        index = self.model.index(0, col_idx)
        self.table.setCurrentIndex(index)
        self.table.scrollTo(index)
        self.table.edit(index)

    def _after_column_rename(self, col_idx, old, new):
        """列重命名后的联动（所有路径共用：表头行编辑/列头双击/撤销重做）。"""
        # 筛选状态下 model.df 是副本，需同步 original_df 的列名和筛选条件
        if self.original_df is not None and old in self.original_df.columns:
            self.original_df.rename(columns={old: new}, inplace=True)
            for f in self.active_filters:
                if f["col"] == old:
                    f["col"] = new
            self._rebuild_filter_bar()
        if old in self.image_columns:
            self.image_columns.discard(old)
            self.image_columns.add(new)
            self._update_image_context()
            self._save_file_config()   # 图片列名持久化在配置里，改名同步落盘
        self._mark_modified()

    # ================= 排序 =================

    def _on_header_clicked(self, col_idx):
        colname = str(self.model.df.columns[col_idx])
        ascending = not self._sort_orders.get(colname, False)
        self._sort_orders[colname] = ascending
        self._sort_by(col_idx, ascending)

    def sort_dialog(self):
        df = self.model.df
        if df.empty:
            return
        col, ok = QInputDialog.getItem(self, tr("排序"), tr("选择排序列:"),
                                       [str(c) for c in df.columns], 0, False)
        if not ok:
            return
        order, ok = QInputDialog.getItem(self, tr("排序"), tr("排序方式:"), [tr("升序"), tr("降序")], 0, False)
        if not ok:
            return
        self._sort_by(list(df.columns).index(col), order == tr("升序"))

    def _sort_by(self, col_idx, ascending):
        if self.active_filters:
            # 筛选视图下位置引用语义模糊，公式仍冻结为静态值
            self._freeze_formulas(tr("排序"))
        df = self.model.df
        colname = df.columns[col_idx]
        # 数值列按数值排，混合列按字符串排
        keys = pd.to_numeric(df[colname], errors="coerce")
        if keys.notna().sum() >= df[colname].notna().sum() and keys.notna().any():
            sort_series = keys
        else:
            sort_series = df[colname].astype(str)
        positions = sort_series.sort_values(
            ascending=ascending, kind="mergesort", na_position="last").index
        if self._filtered_idx_map:
            self._filtered_idx_map = [self._filtered_idx_map[i] for i in positions]
        # 公式单元格/引用与背景色在模型内跟随行序移动；
        # 含部分区域的公式无法安全重排，被冻结为静态值
        frozen = self.model.reorder_rows(positions)
        self._mark_modified()
        message = tr("已按 {} {}排序").format(
            colname, tr("升序") if ascending else tr("降序"))
        if frozen:
            message += tr("；{} 个含部分区域引用的公式已转为静态值").format(frozen)
        self.update_statusbar(message)

    # ================= 筛选 =================

    def _reset_filter_state(self):
        self.active_filters = []
        self.original_df = None
        self._filtered_idx_map = None
        self._orig_cell_colors = None   # 筛选期间颜色的原始行坐标底账
        self._suspended_formulas = None
        self._rebuild_filter_bar()

    def _live_formulas(self):
        """当前 sheet 的权威公式表（original_df 坐标）。

        筛选中返回挂起的公式；筛选中新输入的公式是视图坐标、语义
        不明确，不计入。
        """
        if self.active_filters:
            return self._suspended_formulas or {}
        return self.model.formulas

    def open_filter_dialog(self, preset_col=None, edit_index=None):
        base_df = self.original_df if self.original_df is not None else self.model.df
        edit_filter = self.active_filters[edit_index] if edit_index is not None else None
        dialog = FilterDialog(self, base_df, preset_col=preset_col, edit_filter=edit_filter)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result is None:
            return
        if edit_index is not None:
            self.active_filters[edit_index] = dialog.result
        else:
            self.active_filters.append(dialog.result)
        self._reapply_filters()

    def _freeze_formulas(self, reason):
        """排序/筛选会打乱行位置，公式按位置引用会失效——先转为静态值。"""
        if self.model.formulas:
            n = len(self.model.formulas)
            self.model.formulas.clear()
            self.model._dependents.clear()
            self._sheet_formulas.pop(self.current_sheet, None)
            self.update_statusbar(tr("{}后 {} 个公式已转为静态值").format(reason, n))

    def _reapply_filters(self):
        frozen_view = 0
        if self.original_df is None:
            # 首次进入筛选：挂起公式（original_df 坐标），清除筛选后恢复。
            # 单元格保留公式的计算结果静态显示。
            if self.model.formulas:
                self._suspended_formulas = dict(self.model.formulas)
        else:
            # 调整筛选条件：筛选中新输入的公式是视图坐标，语义失效，
            # 转为静态值（计数用于提示，不能无声丢弃）
            frozen_view = len(self.model.formulas)
        if self.original_df is None:
            self.original_df = self.model.df
            self._orig_cell_colors = dict(self.model.cell_colors)
        self.model.formulas.clear()
        self.model._dependents.clear()
        if not self.active_filters:
            self.clear_all_filters()
            return
        filtered, idx_map = filter_engine.apply_filters(self.original_df, self.active_filters)
        self._filtered_idx_map = idx_map
        # 背景色从原始行坐标映射到筛选后的显示行
        if self._orig_cell_colors:
            by_row = {}
            for (r, c), v in self._orig_cell_colors.items():
                by_row.setdefault(r, []).append((c, v))
            self.model.cell_colors = {
                (disp, c): v
                for disp, orig in enumerate(idx_map)
                for c, v in by_row.get(orig, ())}
        else:
            self.model.cell_colors = {}
        self.model.set_dataframe(filtered)
        self._rebuild_filter_bar()
        self._update_image_context()
        message = tr("筛选结果: {} 行（共 {} 个筛选条件）").format(
            len(filtered), len(self.active_filters))
        if self._suspended_formulas:
            message += tr("；{} 个公式已挂起，清除筛选后恢复").format(
                len(self._suspended_formulas))
        if frozen_view:
            message += tr("；筛选中输入的 {} 个公式已转为静态值").format(frozen_view)
        self.update_statusbar(message)

    def remove_filter(self, index):
        if 0 <= index < len(self.active_filters):
            self.active_filters.pop(index)
            if self.active_filters:
                self._reapply_filters()
            else:
                self.clear_all_filters()

    def clear_all_filters(self):
        # 筛选中输入的公式是视图坐标，恢复原表时转为静态值（值已同步）
        frozen_view = len(self.model.formulas) if self.original_df is not None else 0
        if self.original_df is not None:
            if self._orig_cell_colors is not None:
                self.model.cell_colors = self._orig_cell_colors
            # 恢复挂起的公式并重算（筛选期间的编辑会反映到公式结果里）
            self.model.set_dataframe(self.original_df,
                                     formulas=self._suspended_formulas)
        restored = len(self._suspended_formulas) if self._suspended_formulas else 0
        self._reset_filter_state()
        self._update_image_context()
        parts = []
        if restored:
            parts.append(tr("已恢复 {} 个公式").format(restored))
        if frozen_view:
            parts.append(tr("筛选中输入的 {} 个公式已转为静态值").format(frozen_view))
        if parts:
            self.update_statusbar(tr("；").join(parts))
        else:
            self.update_statusbar()

    def _rebuild_filter_bar(self):
        while self.filter_bar_layout.count():
            item = self.filter_bar_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self.active_filters:
            self.filter_bar.hide()
            return
        self.filter_bar_layout.addWidget(QLabel(tr("筛选:")))
        for i, f in enumerate(self.active_filters):
            chip = QPushButton(f"{filter_engine.describe_filter(f)}  ✕")
            # 浅底必须配深字：不显式指定 color 时深色主题继承白色文字，
            # 白字浅蓝底完全看不清
            chip.setStyleSheet(
                "QPushButton{background:#e3f2fd;color:#0d47a1;"
                "border:1px solid #90caf9;border-radius:9px;padding:2px 8px;}"
                "QPushButton:hover{background:#bbdefb;}")
            chip.clicked.connect(lambda checked=False, idx=i: self.remove_filter(idx))
            chip.setToolTip(tr("点击移除该筛选条件"))
            self.filter_bar_layout.addWidget(chip)
        clear_btn = QPushButton(tr("清除全部"))
        clear_btn.clicked.connect(self.clear_all_filters)
        self.filter_bar_layout.addWidget(clear_btn)
        self.filter_bar_layout.addStretch(1)
        self.filter_bar.show()

    # ================= 剪贴板 =================

    def copy_selection(self, with_headers=None):
        if self._text_editor_focused():
            w = QApplication.focusWidget()
            if hasattr(w, "copy"):
                w.copy()
            return
        indexes = self.table.selectionModel().selectedIndexes()
        if not indexes:
            return
        rows = sorted({i.row() for i in indexes})
        cols = sorted({i.column() for i in indexes})
        df = self.model.df
        matrix = []
        if with_headers is None:
            with_headers = self.copy_headers_cb.isChecked()
        selected = {(i.row(), i.column()) for i in indexes}
        # 选区含视图表头行（行 0）时它本身就是列名行：与"复制列名"选项
        # 合并，保证列名在剪贴板里最多出现一次。勾选选项输出全部列名；
        # 只选中部分表头单元格时，未选中的列留空占位
        header_selected = 0 in rows
        if header_selected:
            rows = [r for r in rows if r != 0]
        if with_headers or header_selected:
            matrix.append([
                str(df.columns[c]) if (with_headers or (0, c) in selected) else ""
                for c in cols])
        header_line = 1 if (with_headers or header_selected) else 0

        for r in rows:
            row_vals = []
            for c in cols:
                if (r, c) in selected:
                    v = df.iat[r - 1, c]
                    row_vals.append("" if pd.isna(v) else str(v))
                else:
                    row_vals.append("")
            matrix.append(row_vals)
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter="\t", quotechar='"',
                            quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerows(matrix)
        clip_text = buf.getvalue().rstrip("\n")
        QApplication.clipboard().setText(clip_text)
        # 记录选区内的公式：键为剪贴板矩阵位置（不连续选区按秩压缩，
        # 与矩阵行列一一对应），并带上源单元格视图坐标供逐格计算平移量
        row_rank = {r: i for i, r in enumerate(rows)}
        col_rank = {c: i for i, c in enumerate(cols)}
        formula_cells = {}
        for r, c in selected:
            if r == 0:
                continue
            f = self.model.formulas.get((r - HEADER_ROWS, c))
            if f:
                formula_cells[(row_rank[r] + header_line, col_rank[c])] = {
                    "text": f, "src": (r, c)}
        self._formula_clipboard = {
            "text": clip_text,
            "cells": formula_cells,
            # 复制后行列结构一旦变化（插删/排序/换表），公式引用已被
            # 重写，剪贴板里的旧公式文本作废，粘贴退回按值处理
            "version": self.model.structure_version,
        } if formula_cells else None
        self.update_statusbar(
            tr("已复制 {} 行 × {} 列").format(len(matrix), len(cols)))

    def paste_selection(self):
        if self._text_editor_focused():
            w = QApplication.focusWidget()
            if hasattr(w, "paste"):
                w.paste()
            return
        # Finder/资源管理器里 Cmd+C 复制的文件：直接作为文件打开（与拖拽一致）
        mime = QApplication.clipboard().mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile()
                if path.lower().endswith((".xlsx", ".xls", ".csv", ".tsv")):
                    self.load_file(path)
                    return
            QMessageBox.information(self, tr("粘贴打开"), tr("仅支持 Excel / CSV 文件"))
            return
        text = QApplication.clipboard().text()
        if not text:
            return
        # 公式粘贴的判定必须在扩表之前：剪贴板文本未被改写，且复制后
        # 行列结构没有变化（结构变化会重写公式引用，剪贴板旧文本作废）
        clip = getattr(self, "_formula_clipboard", None)
        use_formulas = (clip is not None and clip["text"] == text
                        and clip.get("version") == self.model.structure_version)
        # 公式粘贴需与复制矩阵逐行对齐（公式结果可能是空串产生全空行），
        # 保留空行；按值粘贴维持旧行为（跳过全空行）
        rows = self._parse_clipboard_text(text, keep_blank=use_formulas)
        if not rows:
            return

        # 空白默认表：作为整表载入
        if self._is_empty_default_table():
            self._load_rows_as_table(rows)
            return

        idx = self.table.currentIndex()
        start_row = idx.row() if idx.isValid() else 1
        start_col = idx.column() if idx.isValid() else 0
        # 视图容量 = 数据行数 + 1 行表头
        need_rows = start_row + len(rows) - (len(self.model.df) + 1)
        need_cols = start_col + max(len(r) for r in rows) - len(self.model.df.columns)
        if (need_rows > 0 or need_cols > 0) and self.active_filters:
            QMessageBox.information(self, tr("粘贴"), tr("筛选状态下粘贴内容不能超出表格范围"))
            return
        df = self.model.df
        if need_cols > 0:
            for i in range(need_cols):
                self.model.insert_column(len(df.columns),
                                         self.model._unique_col_name(_col_letter(len(df.columns))))
        if need_rows > 0:
            empty = pd.DataFrame(np.full((need_rows, len(self.model.df.columns)), np.nan),
                                 columns=self.model.df.columns)
            # 追加空行不改变既有位置，必须把公式传回去，否则会被 set_dataframe 清空
            self.model.set_dataframe(
                pd.concat([self.model.df, empty]).reset_index(drop=True),
                mark_modified=True, formulas=self.model.formulas)
        if start_row == 0 and rows:
            # 表头行粘贴预处理：目标名被"本次同样会被改名的列"占用时
            # （如互换/轮换列名），先把占名列挪到临时名，避免误判重名加后缀
            desired = {}
            for c_off, val in enumerate(rows[0]):
                col_pos = start_col + c_off
                name = str(val).strip()
                if name and col_pos < len(self.model.df.columns):
                    desired[col_pos] = name
            targets = set(desired.values())
            for col_pos in desired:
                cur = str(self.model.df.columns[col_pos])
                if cur in targets and desired[col_pos] != cur:
                    self.model.rename_column(
                        col_pos, self.model._unique_col_name("__重命名中转"))

        for r_off, row_vals in enumerate(rows):
            for c_off, val in enumerate(row_vals):
                target_row = start_row + r_off
                index = self.model.index(target_row, start_col + c_off)
                formula = clip["cells"].get((r_off, c_off)) if use_formulas else None
                if formula and target_row > 0:
                    # 逐格计算平移量（不连续选区各格偏移不同）
                    src_r, src_c = formula["src"]
                    val = self.model.shift_formula(
                        formula["text"],
                        target_row - src_r,
                        start_col + c_off - src_c)
                elif target_row == 0:
                    # 粘贴到表头行 = 批量重命名（公式也按其计算结果文本处理）；
                    # 与既有列真重名时自动唯一化，避免静默丢弃
                    name = str(val).strip()
                    col_pos = start_col + c_off
                    if (name and col_pos < len(self.model.df.columns)
                            and name != str(self.model.df.columns[col_pos])
                            and name in self.model.df.columns):
                        val = self.model._unique_col_name(name)
                self.model.setData(index, val)
        self._mark_modified()
        self.update_statusbar(tr("已粘贴 {} 行").format(len(rows)))

    @staticmethod
    def _parse_clipboard_text(text, keep_blank=False):
        """自动识别制表符/逗号分隔（与旧版 _parse_tsv_clipboard 一致）。

        keep_blank: 保留全空行。公式粘贴按行偏移对齐复制矩阵时必须保留，
        否则空行（如公式结果为空串）会让后续公式错位。
        """
        first_line = text.splitlines()[0] if text.splitlines() else ""
        delimiter = "\t" if ("\t" in first_line or "," not in first_line) else ","
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        if keep_blank:
            return list(reader)
        return [row for row in reader if any(cell.strip() for cell in row)]

    def _is_empty_default_table(self):
        df = self.model.df
        return (self.current_file is None and not self.model.modified
                and df.isna().all().all())

    def _load_rows_as_table(self, rows):
        header_likely = self._detect_header_row(rows)
        ret = QMessageBox.question(
            self, tr("粘贴"), tr("是否把第一行作为列名？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes if header_likely else QMessageBox.StandardButton.No)
        width = max(len(r) for r in rows)
        norm = [r + [""] * (width - len(r)) for r in rows]
        if ret == QMessageBox.StandardButton.Yes and len(norm) > 1:
            headers = file_io._dedupe_headers([h.strip() or _col_letter(i)
                                               for i, h in enumerate(norm[0])])
            df = pd.DataFrame(norm[1:], columns=headers)
        else:
            df = pd.DataFrame(norm, columns=[_col_letter(i) for i in range(width)])
        df = df.apply(pd.to_numeric, errors="ignore") if hasattr(df, "apply") else df
        self.model.set_dataframe(df, mark_modified=True)
        self._mark_modified()
        self.update_statusbar(tr("已从剪贴板载入 {} 行 × {} 列").format(len(df), len(df.columns)))

    @staticmethod
    def _detect_header_row(rows):
        """启发式判断第一行是否表头（简化自旧版 _detect_header_row）。"""
        if len(rows) < 2:
            return False
        def numeric_ratio(row):
            vals = [c for c in row if c.strip()]
            if not vals:
                return 0.0
            num = sum(1 for c in vals if c.replace(".", "", 1).replace("-", "", 1).isdigit())
            return num / len(vals)
        return numeric_ratio(rows[0]) < 0.3 and numeric_ratio(rows[1]) > 0.5

    # ================= 统计 =================

    def show_statistics(self):
        df = self.model.df
        if df.empty:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("描述性统计"))
        dialog.resize(720, 480)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setFont(QFont("Menlo", 12))
        try:
            text.setPlainText(df.describe(include="all").to_string())
        except Exception as e:
            text.setPlainText(tr("统计失败: {}").format(e))
        layout.addWidget(text)
        dialog.exec()

    def apply_function(self, func):
        indexes = self.table.selectionModel().selectedIndexes()
        df = self.model.df
        if indexes:
            values = pd.to_numeric(
                pd.Series([df.iat[i.row() - HEADER_ROWS, i.column()]
                           for i in indexes if i.row() > 0]),  # 跳过表头行
                errors="coerce")
        else:
            values = pd.Series(dtype=float)
        if values.notna().sum() == 0:
            QMessageBox.information(self, tr("统计"), tr("请先选中包含数值的单元格"))
            return
        result = getattr(values, func)()
        label = tr({"sum": "求和", "mean": "平均值", "max": "最大值",
                    "min": "最小值", "count": "计数"}[func])
        if func == "count":
            result = int(values.notna().sum())
        self.update_statusbar(f"{label}: {result}")
        QMessageBox.information(self, label, f"{label}: {result}")

    # ================= 查找 / 跳转 =================

    def open_find_dialog(self):
        if self._find_dialog is None:
            self._find_dialog = FindReplaceDialog(self)
        self._find_dialog.show()
        self._find_dialog.raise_()
        self._find_dialog.find_edit.setFocus()

    def jump_to_cell(self, row, col):
        """跳转到数据行 row（0 基）；视图中偏移一行表头。"""
        index = self.model.index(row + HEADER_ROWS, col)
        self.table.setCurrentIndex(index)
        self.table.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)

    # ================= 单元格内容预览 =================

    def _on_current_cell_changed(self, current, previous):
        if current.isValid():
            self._preview_loading = True
            # EditRole：公式单元格显示公式本身，可直接编辑
            text = self.model.data(current, Qt.ItemDataRole.EditRole)
            self.cell_preview_text.setPlainText(text or "")
            self._preview_loading = False
            self._preview_cell = (current.row(), current.column())
            self.model.set_highlight_row(current.row())
            # 图片面板按数据行工作（视图行 0 是表头行）
            self.image_panel.set_current_row(current.row() - HEADER_ROWS)
        else:
            self._preview_cell = None
            self.model.set_highlight_row(-1)

    def _on_preview_text_changed(self):
        if not self._preview_loading and getattr(self, "_preview_cell", None):
            self._preview_save_timer.start()

    def _save_cell_preview(self):
        if not getattr(self, "_preview_cell", None):
            return
        row, col = self._preview_cell   # 视图坐标；行 0 = 表头（编辑即重命名）
        if row > len(self.model.df) or col >= len(self.model.df.columns):
            return
        self.model.setData(self.model.index(row, col),
                           self.cell_preview_text.toPlainText())

    # ================= 图片列 / 图片预览 =================

    def _base_image_dir(self):
        return os.path.dirname(self.current_file) if self.current_file else ""

    def _sync_image_panel(self, *_):
        """把当前 model.df 同步给图片面板（排序/筛选/插删行列都会换 df 对象，
        面板持有旧引用会导致行号和图片对不上）。"""
        active_col = None
        for col in self.model.df.columns:
            if str(col) in self.image_columns:
                active_col = str(col)
                break
        self.image_panel.set_context(self.model.df, active_col, self._base_image_dir())
        return active_col

    def _update_image_context(self):
        """数据或图片列变化后刷新图片面板。"""
        if self._sync_image_panel():
            self.image_dock.show()

    def _set_image_column(self, colname):
        self.image_columns = {colname}  # 与旧版一致：同时只有一个图片列
        self._update_image_context()
        self._save_file_config()
        self.update_statusbar(tr("已把 '{}' 设为图片列").format(colname))

    def _unset_image_column(self, colname):
        self.image_columns.discard(colname)
        self._update_image_context()
        self._save_file_config()
        if not self.image_columns:
            self.image_dock.hide()

    def _on_image_row_activated(self, row):
        if 0 <= row < len(self.model.df):
            col = self.table.currentIndex().column()
            self.jump_to_cell(row, max(0, col))

    def open_image_viewer(self, path):
        try:
            from .image_viewer import ImageViewer
        except Exception as e:
            QMessageBox.critical(self, tr("图片查看"), tr("图片查看器加载失败: {}").format(e))
            return
        viewer = ImageViewer(path, parent=self)
        viewer.show()
        self._image_viewers.append(viewer)
        self._image_viewers = [v for v in self._image_viewers if v.isVisible()]

    def _open_image_queue(self, colname=None):
        """图片队列复制：把图片列的图片按行逐个复制到剪贴板。"""
        try:
            from .image_queue import FloatingImageQueue
        except Exception as e:
            QMessageBox.critical(self, tr("图片队列"), tr("图片队列加载失败: {}").format(e))
            return
        if colname is None:
            colname = next(iter(self.image_columns), None)
        if colname is None or colname not in self.model.df.columns:
            QMessageBox.information(self, tr("图片队列"), tr("请先把某一列设为图片列"))
            return
        # 从选中行（或全部行）收集图片路径（视图行 -> 数据行，跳过表头行）
        rows = sorted({i.row() - HEADER_ROWS for i in self.table.selectionModel().selectedIndexes()
                       if i.row() > 0})
        if len(rows) <= 1:
            rows = range(len(self.model.df))
        col_idx = list(self.model.df.columns).index(colname)
        base = self._base_image_dir()
        paths = []
        for r in rows:
            raw = str(self.model.df.iat[r, col_idx]).strip()
            if not raw or raw.lower() in ("nan", "none"):
                continue
            p = raw if os.path.isabs(raw) else os.path.normpath(os.path.join(base, raw))
            if os.path.exists(p):
                paths.append(p)
        if not paths:
            QMessageBox.information(self, tr("图片队列"), tr("没有找到可用的图片路径"))
            return
        self._image_queue_win = FloatingImageQueue(paths, parent=self)
        self._image_queue_win.show()

    # ---------- 图片列配置随文件记忆 ----------

    def _load_file_config(self):
        if not self.current_file:
            return
        entry = _file_config_entry(self.current_file)
        self.image_columns = set(entry.get("image_columns", []))

    def _save_file_config(self):
        import json
        if not self.current_file:
            return
        try:
            cfg = {}
            if os.path.exists(FILE_CONFIG_PATH):
                with open(FILE_CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg[os.path.abspath(self.current_file)] = {
                "image_columns": sorted(self.image_columns),
                "last_sheet": self.current_sheet}
            os.makedirs(os.path.dirname(FILE_CONFIG_PATH), exist_ok=True)
            with open(FILE_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except (OSError, ValueError) as e:
            print(f"保存文件配置失败: {e}")

    # ================= 背景颜色 =================

    def _set_selection_color(self, color_hex):
        indexes = [i for i in self.table.selectionModel().selectedIndexes()
                   if i.row() > 0]  # 视图行 0 是表头行，不设背景色
        for i in indexes:
            row = i.row() - HEADER_ROWS   # 数据行
            self.model.set_cell_color(row, i.column(), color_hex)
            # 筛选状态下同步到原始行坐标底账，清除筛选后颜色仍在正确的行上
            if self._filtered_idx_map is not None and self._orig_cell_colors is not None \
                    and row < len(self._filtered_idx_map):
                key = (self._filtered_idx_map[row], i.column())
                if color_hex:
                    self._orig_cell_colors[key] = color_hex
                else:
                    self._orig_cell_colors.pop(key, None)
        if indexes:
            self._mark_modified()

    # ================= 右键菜单 =================

    def _show_cell_menu(self, pos):
        index = self.table.indexAt(pos)
        menu = QMenu(self)
        if index.isValid():
            # 视图行 -> 数据行（-1）；表头行上"向上插入"落到数据行 0
            data_row = max(0, index.row() - HEADER_ROWS)
            menu.addAction(tr("向上插入一行"), lambda: self.insert_row(data_row))
            menu.addAction(tr("向下插入一行"), lambda: self.insert_row(
                data_row + (1 if index.row() > 0 else 0)))
            menu.addAction(tr("删除选中行"), self.delete_selected_rows)
            if index.row() > 0:
                menu.addAction(tr("将此行设为表头"),
                               lambda: self.promote_row_to_header(index.row() - 1))
            menu.addSeparator()
            menu.addAction(tr("向左插入一列"), lambda: self._insert_col_at(index.column()))
            menu.addAction(tr("向右插入一列"), lambda: self._insert_col_at(index.column() + 1))
            menu.addAction(tr("重命名列"), lambda: self._rename_column_at(index.column()))
            menu.addAction(tr("删除选中列"), self.delete_selected_columns)
            menu.addSeparator()
            colname = str(self.model.df.columns[index.column()])
            menu.addAction(tr("筛选此列 ({})...").format(colname),
                           lambda: self.open_filter_dialog(preset_col=colname))
            menu.addAction(tr("升序排序"), lambda: self._sort_by(index.column(), True))
            menu.addAction(tr("降序排序"), lambda: self._sort_by(index.column(), False))
            menu.addSeparator()
        else:
            # 空白区域：支持直接在末尾追加行/列
            menu.addAction(tr("在末尾新增一列"),
                           lambda: self._insert_col_at(len(self.model.df.columns)))
            menu.addAction(tr("在末尾新增一行"),
                           lambda: self.insert_row(len(self.model.df)))
            menu.addSeparator()
        menu.addAction(tr("复制"), self.copy_selection)
        menu.addAction(tr("不带列名复制"), lambda: self.copy_selection(with_headers=False))
        menu.addAction(tr("复制后转置"), self.copy_selection_transposed)
        menu.addAction(tr("粘贴"), self.paste_selection)
        if index.isValid():
            colname = str(self.model.df.columns[index.column()])
            menu.addSeparator()
            if colname in self.image_columns:
                menu.addAction(tr("查看图片"), lambda: self._view_cell_image(index))
                menu.addAction(tr("取消图片列"), lambda: self._unset_image_column(colname))
            else:
                menu.addAction(tr("设为图片列"), lambda: self._set_image_column(colname))
            menu.addAction(tr("图片队列复制..."), lambda: self._open_image_queue())
            menu.addSeparator()
            color_menu = menu.addMenu(tr("设置背景颜色"))
            for label, color_hex in CELL_COLORS:
                color_menu.addAction(
                    tr(label), lambda checked=False, c=color_hex: self._set_selection_color(c))
            color_menu.addSeparator()
            color_menu.addAction(tr("清除颜色"), lambda: self._set_selection_color(None))
        if self.current_file:
            menu.addSeparator()
            menu.addAction(tr("打开所在文件夹"), self._open_file_folder)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _view_cell_image(self, index):
        if index.row() == 0:
            return  # 表头行没有图片
        raw = str(self.model.df.iat[index.row() - HEADER_ROWS, index.column()]).strip()
        if not raw or raw.lower() in ("nan", "none"):
            return
        path = raw if os.path.isabs(raw) else os.path.normpath(
            os.path.join(self._base_image_dir(), raw))
        if os.path.exists(path):
            self.open_image_viewer(path)
        else:
            QMessageBox.warning(self, tr("查看图片"), tr("图片不存在:\n{}").format(path))

    def copy_selection_transposed(self):
        indexes = self.table.selectionModel().selectedIndexes()
        if not indexes:
            return
        # 视图行 -> 数据行；表头行由列名行独立提供，选中它不再重复
        rows = sorted({i.row() - HEADER_ROWS for i in indexes if i.row() > 0})
        cols = sorted({i.column() for i in indexes})
        df = self.model.df
        matrix = [[str(df.columns[c]) for c in cols]]
        for r in rows:
            matrix.append(["" if pd.isna(df.iat[r, c]) else str(df.iat[r, c])
                           for c in cols])
        transposed = list(zip(*matrix))
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter="\t", quotechar='"',
                            quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerows(transposed)
        QApplication.clipboard().setText(buf.getvalue().rstrip("\n"))
        # 转置后行列互换，公式偏移模型不适用，粘贴按值处理
        self._formula_clipboard = None
        self.update_statusbar(tr("已转置复制 {} 行 × {} 列").format(len(rows), len(cols)))

    def _insert_col_at(self, pos):
        if not self._require_no_filter():
            return
        self.model.insert_column(pos)
        self._mark_modified()

    def _show_col_menu(self, pos):
        col = self.table.horizontalHeader().logicalIndexAt(pos)
        if col < 0:
            # 列头右侧空白区域：支持在末尾追加列
            menu = QMenu(self)
            menu.addAction(tr("在末尾新增一列"),
                           lambda: self._insert_col_at(len(self.model.df.columns)))
            menu.exec(self.table.horizontalHeader().mapToGlobal(pos))
            return
        colname = str(self.model.df.columns[col])
        menu = QMenu(self)
        menu.addAction(tr("筛选此列 ({})...").format(colname),
                       lambda: self.open_filter_dialog(preset_col=colname))
        menu.addAction(tr("升序排序"), lambda: self._sort_by(col, True))
        menu.addAction(tr("降序排序"), lambda: self._sort_by(col, False))
        menu.addSeparator()
        menu.addAction(tr("重命名列"), lambda: self._rename_column_at(col))
        menu.addAction(tr("向左插入一列"), lambda: self._insert_col_at(col))
        menu.addAction(tr("向右插入一列"), lambda: self._insert_col_at(col + 1))
        menu.addAction(tr("删除此列"), lambda: self._delete_col_at(col))
        menu.addSeparator()
        if colname in self.image_columns:
            menu.addAction(tr("取消图片列"), lambda: self._unset_image_column(colname))
        else:
            menu.addAction(tr("设为图片列"), lambda: self._set_image_column(colname))
        menu.addAction(tr("图片队列复制..."), lambda: self._open_image_queue(
            colname if colname in self.image_columns else None))
        menu.exec(self.table.horizontalHeader().mapToGlobal(pos))

    def _delete_col_at(self, col):
        if not self._require_no_filter():
            return
        colname = str(self.model.df.columns[col])
        ret = QMessageBox.question(self, tr("删除列"), tr("确定删除列 '{}'？").format(colname))
        if ret == QMessageBox.StandardButton.Yes:
            self.model.remove_columns([col])
            self._mark_modified()

    def _show_row_menu(self, pos):
        row = self.table.verticalHeader().logicalIndexAt(pos)
        if row < 0:
            return
        # 视图行 -> 数据行；表头行（视图 0）上两个动作都落到数据行 0
        data_row = max(0, row - 1)
        below = data_row + (1 if row > 0 else 0)
        menu = QMenu(self)
        menu.addAction(tr("向上插入一行"), lambda: self.insert_row(data_row))
        menu.addAction(tr("向下插入一行"), lambda: self.insert_row(below))
        menu.addAction(tr("删除选中行"), self.delete_selected_rows)
        if row > 0:
            menu.addSeparator()
            menu.addAction(tr("将此行设为表头"),
                           lambda: self.promote_row_to_header(row - 1))
        menu.exec(self.table.verticalHeader().mapToGlobal(pos))

    def promote_row_to_header(self, data_row):
        """把数据行提升为表头（真实表头不在首行的文件，如带元数据前言的导出）。"""
        if not self._require_no_filter():
            return
        if data_row > 0:
            ret = QMessageBox.question(
                self, tr("设为表头"),
                tr("将第 {} 行设为表头？其上方的 {} 行将被删除。").format(
                    data_row + 2, data_row))
            if ret != QMessageBox.StandardButton.Yes:
                return
        if self.model.promote_row_to_header(data_row):
            self._mark_modified()
            self.update_statusbar(tr("已将该行设为表头"))

    def _open_file_folder(self):
        if self.current_file:
            import subprocess
            subprocess.Popen(["open", "-R", self.current_file])

    # ================= 缩放 =================

    def zoom_in(self):
        self._zoom = min(2.0, self._zoom + 0.1)
        self._apply_zoom()

    def zoom_out(self):
        self._zoom = max(0.5, self._zoom - 0.1)
        self._apply_zoom()

    def zoom_reset(self):
        self._zoom = 1.0
        self._apply_zoom()

    def _apply_zoom(self):
        font = self.table.font()
        font.setPointSizeF(self._base_font_size * self._zoom)
        self.table.setFont(font)
        self._cell_delegate.clear_cache()  # 字号变化后截断缓存全部失效
        self.table.verticalHeader().setDefaultSectionSize(
            int(self._base_row_height * self._zoom))
        self.update_statusbar(tr("缩放: {}%").format(int(self._zoom * 100)))

    def refit_columns(self):
        self.table.resizeColumnsToContents()
        header = self.table.horizontalHeader()
        for i in range(header.count()):
            if header.sectionSize(i) > 400:
                header.resizeSection(i, 400)

    # ================= 拖拽 =================

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith((".xlsx", ".xls", ".csv", ".tsv")):
                self.load_file(path)
                return
        QMessageBox.information(self, tr("拖拽打开"), tr("仅支持 Excel / CSV 文件"))

    # ================= 关闭 =================

    def closeEvent(self, event):
        if self._check_save_before_discard():
            self._save_recent()
            self._save_file_config()   # 记住最后停留的 sheet / 图片列
            self._settings.setValue("window/geometry", self.saveGeometry())
            self._settings.setValue("window/state", self.saveState())
            event.accept()
        else:
            event.ignore()
