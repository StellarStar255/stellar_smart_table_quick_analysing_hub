# -*- coding: utf-8 -*-
"""
Python 数据分析窗口 - 对应 Tkinter 版 ui/python_analysis.py 的核心功能：
代码编辑器（语法高亮）、预设片段、后台运行代码、结果 DataFrame 预览/保存为 Sheet。
不包含 AI 代码生成与语音输入。
"""

import builtins
import io
import json
import keyword
import os
import traceback
import contextlib
from datetime import datetime

import pandas as pd
import numpy as np

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRegularExpression
from PyQt6.QtGui import (
    QAction, QColor, QFont, QKeySequence, QSyntaxHighlighter,
    QTextCharFormat, QFontDatabase,
)
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QPlainTextEdit, QSplitter, QToolBar, QMessageBox,
    QInputDialog, QDialog, QTableView, QDialogButtonBox,
)

from .pandas_model import PandasTableModel

PRESETS_FILE = os.path.join(os.path.expanduser("~"), ".smart_table_hub", "qt_python_presets.json")

DEFAULT_PRESETS = {
    "描述统计": "print(df.describe(include='all'))",
    "缺失值统计": "print(df.isnull().sum())",
    "数据类型": "print(df.dtypes)",
    "去重": "result = df.drop_duplicates()\nsave_as_sheet(result, '去重结果')",
}


# ---------------------------------------------------------------------------
# 语法高亮
# ---------------------------------------------------------------------------

class PythonHighlighter(QSyntaxHighlighter):
    """Python 语法高亮，颜色在浅色/深色主题下均可读。"""

    def __init__(self, document):
        super().__init__(document)

        def fmt(color, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            if italic:
                f.setFontItalic(True)
            return f

        self._rules = []

        # 关键字
        kw_fmt = fmt("#c678dd", bold=True)
        for kw in keyword.kwlist:
            self._rules.append((QRegularExpression(rf"\b{kw}\b"), kw_fmt))

        # 内置函数
        builtin_fmt = fmt("#2a9d8f")
        builtin_names = [n for n in dir(builtins) if not n.startswith("_")]
        self._rules.append((
            QRegularExpression(r"\b(" + "|".join(builtin_names) + r")\b(?=\s*\()"),
            builtin_fmt,
        ))

        # df / pd / np 特殊名字
        self._rules.append((QRegularExpression(r"\b(df|pd|np)\b"), fmt("#e07b39", bold=True)))

        # 数字
        self._rules.append((
            QRegularExpression(r"\b\d+(\.\d+)?([eE][+-]?\d+)?j?\b"),
            fmt("#d19a66"),
        ))

        # 装饰器
        self._rules.append((QRegularExpression(r"@\w+(\.\w+)*"), fmt("#4a90d9")))

        # 单行字符串（单/双引号）
        str_fmt = fmt("#7cae52")
        self._rules.append((QRegularExpression(r"'[^'\\]*(\\.[^'\\]*)*'"), str_fmt))
        self._rules.append((QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"'), str_fmt))

        # 注释（放最后，覆盖前面的规则）
        self._rules.append((QRegularExpression(r"#[^\n]*"), fmt("#8a919c", italic=True)))

        # 三引号字符串（跨行，用 block state 处理）
        self._tri_fmt = str_fmt
        self._tri_single = QRegularExpression(r"'''")
        self._tri_double = QRegularExpression('"""')

    def highlightBlock(self, text):
        for pattern, char_fmt in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), char_fmt)

        # 三引号多行字符串：state 1 = '''，state 2 = """
        self.setCurrentBlockState(0)
        self._match_multiline(text, self._tri_single, 1)
        if self.currentBlockState() == 0:
            self._match_multiline(text, self._tri_double, 2)

    def _match_multiline(self, text, delimiter, state):
        if self.previousBlockState() == state:
            start = 0
            add = 0
        else:
            m = delimiter.match(text)
            start = m.capturedStart() if m.hasMatch() else -1
            add = m.capturedLength() if m.hasMatch() else 0

        while start >= 0:
            m = delimiter.match(text, start + add)
            if m.hasMatch():
                end = m.capturedStart() + m.capturedLength()
                self.setFormat(start, end - start, self._tri_fmt)
                nm = delimiter.match(text, end)
                start = nm.capturedStart() if nm.hasMatch() else -1
                add = nm.capturedLength() if nm.hasMatch() else 0
            else:
                self.setCurrentBlockState(state)
                self.setFormat(start, len(text) - start, self._tri_fmt)
                return


# ---------------------------------------------------------------------------
# 代码编辑器
# ---------------------------------------------------------------------------

class CodeEditor(QPlainTextEdit):
    """Tab 键插入 4 个空格。"""

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Tab and not event.modifiers():
            self.insertPlainText("    ")
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# 后台执行线程
# ---------------------------------------------------------------------------

class CodeRunWorker(QThread):
    """在后台线程执行用户代码，结果通过信号回传主线程。"""

    done = pyqtSignal(str, dict, list, list)  # 输出文本, DataFrame变量, sheet保存请求, 图片保存记录

    def __init__(self, code, df, current_file, parent=None):
        super().__init__(parent)
        self._code = code
        self._df = df
        self._current_file = current_file

    def run(self):
        buf = io.StringIO()
        sheet_requests = []   # [(DataFrame, sheet_name)]
        figure_files = []
        result_dfs = {}

        # 强制 matplotlib 使用 Agg，避免后台线程弹 GUI
        try:
            import matplotlib
            matplotlib.use("Agg", force=True)
        except Exception:
            pass

        def save_as_sheet(result_df, sheet_name=None):
            if not isinstance(result_df, pd.DataFrame):
                raise TypeError("结果必须是 pandas DataFrame")
            if sheet_name is None:
                sheet_name = f"分析结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            sheet_requests.append((result_df.copy(), str(sheet_name)))
            print(f"✓ 已加入保存队列: {sheet_name}")

        def save_figure(fig, filename=None):
            if filename is None:
                filename = f"分析图表_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            if not os.path.dirname(filename):
                if self._current_file:
                    base_dir = os.path.dirname(self._current_file)
                else:
                    base_dir = os.path.join(os.path.expanduser("~"), "Desktop")
                filename = os.path.join(base_dir, filename)
            fig.savefig(filename, dpi=150, bbox_inches="tight")
            figure_files.append(filename)
            print(f"✓ 图表已保存: {filename}")

        namespace = {
            "df": self._df,
            "pd": pd,
            "np": np,
            "save_as_sheet": save_as_sheet,
            "save_figure": save_figure,
            "__builtins__": builtins,
        }

        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                exec(self._code, namespace)
            except Exception:
                buf.write("\n[执行错误]\n")
                buf.write(traceback.format_exc())

        # 扫描命名空间中的 DataFrame 变量
        for name, value in namespace.items():
            if name.startswith("__"):
                continue
            if isinstance(value, pd.DataFrame):
                result_dfs[name] = value

        self.done.emit(buf.getvalue(), result_dfs, sheet_requests, figure_files)


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class PythonAnalysisWindow(QMainWindow):
    """Python 数据分析窗口（非模态）。host 为主窗口，提供 model.df 等接口。"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self._worker = None
        self._result_dfs = {}

        self.setWindowTitle("Python 数据分析")
        self.resize(1000, 700)

        self.presets = self._load_presets()

        self._build_ui()
        self._update_preset_combo()

    # ---------------- UI ----------------

    def _build_ui(self):
        toolbar = QToolBar("工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addWidget(QLabel(" 预设: "))
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(180)
        toolbar.addWidget(self.preset_combo)

        apply_btn = QPushButton("应用预设")
        apply_btn.clicked.connect(lambda: self._apply_preset(append=False))
        toolbar.addWidget(apply_btn)

        append_btn = QPushButton("追加")
        append_btn.clicked.connect(lambda: self._apply_preset(append=True))
        toolbar.addWidget(append_btn)

        save_preset_btn = QPushButton("保存为预设")
        save_preset_btn.clicked.connect(self._save_current_as_preset)
        toolbar.addWidget(save_preset_btn)

        del_preset_btn = QPushButton("删除预设")
        del_preset_btn.clicked.connect(self._delete_preset)
        toolbar.addWidget(del_preset_btn)

        toolbar.addSeparator()

        self.run_action = QAction("▶ 运行", self)
        self.run_action.setShortcut(QKeySequence(Qt.Key.Key_F5))
        self.run_action.triggered.connect(self.run_code)
        toolbar.addAction(self.run_action)

        clear_action = QAction("清空输出", self)
        clear_action.triggered.connect(lambda: self.output_edit.clear())
        toolbar.addAction(clear_action)

        # 主区域：上代码编辑器、下输出
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)

        mono = QFont("Menlo", 13)
        mono.setStyleHint(QFont.StyleHint.Monospace)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter, 1)

        self.code_edit = CodeEditor()
        self.code_edit.setFont(mono)
        self.code_edit.setPlaceholderText(
            "# 可用变量: df (当前数据副本), pd, np\n"
            "# 可用函数: save_as_sheet(df, '名称'), save_figure(fig, '文件名.png')"
        )
        self._highlighter = PythonHighlighter(self.code_edit.document())
        splitter.addWidget(self.code_edit)

        self.output_edit = QPlainTextEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setFont(mono)
        splitter.addWidget(self.output_edit)

        splitter.setSizes([420, 220])

        # 结果 DataFrame 行
        result_row = QHBoxLayout()
        result_row.addWidget(QLabel("结果 DataFrame:"))
        self.result_combo = QComboBox()
        self.result_combo.setMinimumWidth(160)
        result_row.addWidget(self.result_combo)

        preview_btn = QPushButton("预览")
        preview_btn.clicked.connect(self._preview_result)
        result_row.addWidget(preview_btn)

        save_sheet_btn = QPushButton("保存为Sheet")
        save_sheet_btn.clicked.connect(self._save_result_as_sheet)
        result_row.addWidget(save_sheet_btn)

        result_row.addStretch(1)
        layout.addLayout(result_row)

    # ---------------- 预设 ----------------

    def _load_presets(self):
        try:
            if os.path.exists(PRESETS_FILE):
                with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            print(f"加载预设失败: {e}")
        return dict(DEFAULT_PRESETS)

    def _save_presets(self):
        try:
            os.makedirs(os.path.dirname(PRESETS_FILE), exist_ok=True)
            with open(PRESETS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.presets, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存预设失败:\n{e}")

    def _update_preset_combo(self, select_name=None):
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItems(list(self.presets.keys()))
        if select_name and select_name in self.presets:
            self.preset_combo.setCurrentText(select_name)
        self.preset_combo.blockSignals(False)

    def _apply_preset(self, append=False):
        name = self.preset_combo.currentText()
        if not name or name not in self.presets:
            return
        code = self.presets[name]
        if append:
            existing = self.code_edit.toPlainText()
            self.code_edit.setPlainText((existing.rstrip() + "\n\n" + code) if existing.strip() else code)
        else:
            self.code_edit.setPlainText(code)

    def _save_current_as_preset(self):
        code = self.code_edit.toPlainText().strip()
        if not code:
            QMessageBox.warning(self, "提示", "编辑器内容为空，无法保存为预设")
            return
        name, ok = QInputDialog.getText(self, "保存为预设", "预设名称:",
                                        text=self.preset_combo.currentText())
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self.presets:
            ans = QMessageBox.question(self, "确认", f"预设 “{name}” 已存在，是否覆盖?")
            if ans != QMessageBox.StandardButton.Yes:
                return
        self.presets[name] = code
        self._save_presets()
        self._update_preset_combo(select_name=name)

    def _delete_preset(self):
        name = self.preset_combo.currentText()
        if not name or name not in self.presets:
            return
        ans = QMessageBox.question(self, "确认", f"确定删除预设 “{name}”?")
        if ans != QMessageBox.StandardButton.Yes:
            return
        del self.presets[name]
        self._save_presets()
        self._update_preset_combo()

    # ---------------- 运行 ----------------

    def run_code(self):
        if self._worker is not None and self._worker.isRunning():
            return
        code = self.code_edit.toPlainText().strip()
        if not code:
            QMessageBox.warning(self, "提示", "请输入要运行的代码")
            return

        df = getattr(self.host.model, "df", None)
        df = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()

        self.output_edit.clear()
        self.output_edit.setPlainText("正在运行...\n")
        self.run_action.setEnabled(False)

        self._worker = CodeRunWorker(code, df, getattr(self.host, "current_file", None), self)
        self._worker.done.connect(self._on_run_done)
        self._worker.start()

    def _on_run_done(self, output, result_dfs, sheet_requests, figure_files):
        self.run_action.setEnabled(True)
        self.output_edit.setPlainText(output if output else "✓ 代码执行完成（无输出）\n")
        sb = self.output_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

        # 结果 DataFrame 下拉框
        self._result_dfs = result_dfs
        self.result_combo.clear()
        for name, rdf in result_dfs.items():
            self.result_combo.addItem(f"{name} ({len(rdf)}行×{len(rdf.columns)}列)", name)

        # save_as_sheet 队列在主线程统一执行（线程安全）
        for rdf, sheet_name in sheet_requests:
            try:
                self.host.add_sheet_from_df(rdf, sheet_name)
            except Exception as e:
                self.output_edit.appendPlainText(f"[保存Sheet失败] {sheet_name}: {e}")

        if sheet_requests:
            names = ", ".join(n for _, n in sheet_requests)
            self._status(f"已保存 {len(sheet_requests)} 个Sheet: {names}")
        else:
            self._status("代码执行完成")

        worker = self._worker
        self._worker = None
        worker.deleteLater()

    def _status(self, msg):
        try:
            self.host.update_statusbar(msg)
        except Exception:
            pass

    # ---------------- 结果 DataFrame ----------------

    def _current_result_df(self):
        name = self.result_combo.currentData()
        if not name or name not in self._result_dfs:
            QMessageBox.information(self, "提示", "没有可用的结果 DataFrame，请先运行代码")
            return None, None
        return name, self._result_dfs[name]

    def _preview_result(self):
        name, rdf = self._current_result_df()
        if rdf is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"预览 - {name} ({len(rdf)}行×{len(rdf.columns)}列)")
        dlg.resize(800, 500)
        layout = QVBoxLayout(dlg)
        view = QTableView()
        view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        model = PandasTableModel(rdf.copy())
        view.setModel(model)
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.clicked.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.exec()

    def _save_result_as_sheet(self):
        name, rdf = self._current_result_df()
        if rdf is None:
            return
        default = f"{name}_{datetime.now().strftime('%H%M%S')}" if name == "df" else name
        sheet_name, ok = QInputDialog.getText(self, "保存为Sheet", "Sheet 名称:", text=default)
        if not ok or not sheet_name.strip():
            return
        try:
            self.host.add_sheet_from_df(rdf.copy(), sheet_name.strip())
            self._status(f"已保存Sheet: {sheet_name.strip()}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存Sheet失败:\n{e}")

    # ---------------- 关闭 ----------------

    def closeEvent(self, event):
        # 等待后台线程结束，避免崩溃
        if self._worker is not None and self._worker.isRunning():
            self._worker.done.disconnect()
            self._worker.wait(3000)
        super().closeEvent(event)
