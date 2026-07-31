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
import time
import traceback
import contextlib
from datetime import datetime

import pandas as pd
import numpy as np

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRegularExpression, QRect, QSize
from PyQt6.QtGui import (
    QAction, QColor, QFont, QKeySequence, QSyntaxHighlighter,
    QTextCharFormat, QFontDatabase, QPainter, QPalette, QTextFormat,
)
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QPlainTextEdit, QSplitter, QToolBar, QMessageBox,
    QInputDialog, QDialog, QTableView, QDialogButtonBox, QTextEdit,
)

from .pandas_model import PandasTableModel
from qtui.i18n import tr

PRESETS_FILE = os.path.join(os.path.expanduser("~"), ".smart_table_hub", "qt_python_presets.json")

# 默认预设版本：新增默认预设时 +1，老用户的预设文件会做一次性合并
# （用户同名预设优先；只补比文件版本更新的批次，删除过的旧默认预设不会复活）
DEFAULTS_VERSION = 3
_DEFAULTS_VERSION_KEY = "__defaults_version__"

DEFAULT_PRESETS = {
    "描述统计": "print(df.describe(include='all'))",
    "缺失值统计": "print(df.isnull().sum())",
    "数据类型": "print(df.dtypes)",
    "去重": "result = df.drop_duplicates()\nsave_as_sheet(result, '去重结果')",
    "分组聚合": (
        "# 把列名改成你的：按一列分组，对数值列做多种聚合\n"
        "result = df.groupby('分组列').agg({'数值列': ['sum', 'mean', 'count']})\n"
        "print(result)\n"
        "# save_as_sheet(result.reset_index(), '分组聚合')"
    ),
    "透视表": (
        "# 把行/列/值改成你的列名\n"
        "result = pd.pivot_table(df, index='行维度', columns='列维度',\n"
        "                        values='数值列', aggfunc='sum', fill_value=0)\n"
        "print(result)\n"
        "# save_as_sheet(result.reset_index(), '透视表')"
    ),
    "相关性矩阵": (
        "result = df.select_dtypes('number').corr().round(3)\n"
        "print(result)\n"
        "# save_as_sheet(result.reset_index(), '相关性')"
    ),
    "TopN 排序": (
        "# 按某数值列取最大的 10 行\n"
        "result = df.nlargest(10, '数值列')\n"
        "print(result)\n"
        "# save_as_sheet(result, 'Top10')"
    ),
    "缺失值清洗": (
        "# 丢弃全空行，数值列缺失填 0\n"
        "result = df.dropna(how='all')\n"
        "result = result.fillna({c: 0 for c in result.select_dtypes('number').columns})\n"
        "print(result.isnull().sum())\n"
        "# save_as_sheet(result, '清洗结果')"
    ),
    "直方图": (
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(figsize=(8, 5))\n"
        "df['数值列'].hist(ax=ax, bins=30)\n"
        "save_figure(fig, '直方图.png')"
    ),
    # ---- v3 新增 ----
    "数据概览": (
        "print(f'行数: {len(df)}, 列数: {len(df.columns)}')\n"
        "print()\n"
        "print('列清单:')\n"
        "for c in df.columns:\n"
        "    print(f'  {c}  ({df[c].dtype})  非空 {df[c].notna().sum()}')\n"
        "print()\n"
        "print('前 5 行:')\n"
        "print(df.head())"
    ),
    "频次统计": (
        "# 统计某一列每个值出现的次数与占比，改成你的列名\n"
        "counts = df['分类列'].value_counts()\n"
        "result = counts.to_frame('次数')\n"
        "result['占比%'] = (counts / len(df) * 100).round(2)\n"
        "print(result)\n"
        "# save_as_sheet(result.reset_index(), '频次统计')"
    ),
    "条件筛选导出": (
        "# 按条件筛选行并另存为 Sheet（& 与、| 或，注意每个条件加括号）\n"
        "result = df[(df['数值列'] > 100) & (df['分类列'] == '目标值')]\n"
        "print(f'筛出 {len(result)} 行')\n"
        "save_as_sheet(result, '筛选结果')"
    ),
    "新增计算列": (
        "import numpy as np\n"
        "result = df.copy()\n"
        "# 算术：两列相乘\n"
        "result['金额'] = result['数量'] * result['单价']\n"
        "# 条件分档\n"
        "result['等级'] = np.where(result['金额'] >= 10000, '高', '普通')\n"
        "print(result.head())\n"
        "# save_as_sheet(result, '含计算列')"
    ),
    "文本清洗": (
        "result = df.copy()\n"
        "col = '文本列'  # 改成你的列名\n"
        "result[col] = (result[col].astype(str)\n"
        "               .str.strip()          # 去首尾空格\n"
        "               .str.upper()          # 转大写（不需要就删掉）\n"
        "               .str.replace('旧', '新', regex=False))\n"
        "# 按分隔符拆出新列：\n"
        "# result[['前段', '后段']] = result[col].str.split('-', n=1, expand=True)\n"
        "print(result.head())\n"
        "# save_as_sheet(result, '清洗结果')"
    ),
    "日期处理": (
        "result = df.copy()\n"
        "col = '日期列'  # 改成你的列名\n"
        "result[col] = pd.to_datetime(result[col], errors='coerce')\n"
        "result['年'] = result[col].dt.year\n"
        "result['月'] = result[col].dt.to_period('M').astype(str)\n"
        "# 按月聚合：\n"
        "monthly = result.groupby('月')['数值列'].sum()\n"
        "print(monthly)\n"
        "# save_as_sheet(monthly.reset_index(), '按月汇总')"
    ),
    "异常值检测": (
        "# IQR 法：找出超出 [Q1-1.5IQR, Q3+1.5IQR] 的行\n"
        "col = '数值列'  # 改成你的列名\n"
        "q1, q3 = df[col].quantile(0.25), df[col].quantile(0.75)\n"
        "iqr = q3 - q1\n"
        "lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr\n"
        "result = df[(df[col] < lo) | (df[col] > hi)]\n"
        "print(f'正常范围 [{lo:.2f}, {hi:.2f}]，异常 {len(result)} 行')\n"
        "print(result.head(20))\n"
        "# save_as_sheet(result, '异常值')"
    ),
    "重复值检查": (
        "# 查看重复的行（keep=False 把每组重复全部列出）；只想去重用\"去重\"预设\n"
        "# 按整行判断：subset=None；按某几列判断：subset=['列1', '列2']\n"
        "result = df[df.duplicated(subset=None, keep=False)]\n"
        "print(f'重复行数: {len(result)}')\n"
        "print(result.head(20))\n"
        "# save_as_sheet(result, '重复行')"
    ),
    "条形图": (
        "import matplotlib.pyplot as plt\n"
        "counts = df['分类列'].value_counts().head(15)\n"
        "fig, ax = plt.subplots(figsize=(9, 5))\n"
        "counts.plot.barh(ax=ax)\n"
        "ax.invert_yaxis()\n"
        "fig.tight_layout()\n"
        "save_figure(fig, '条形图.png')"
    ),
    "散点图": (
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(figsize=(7, 6))\n"
        "df.plot.scatter(x='数值列X', y='数值列Y', alpha=0.5, ax=ax)\n"
        "save_figure(fig, '散点图.png')"
    ),
    "箱线图": (
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(figsize=(8, 5))\n"
        "df.boxplot(column='数值列', by='分类列', ax=ax)\n"
        "fig.suptitle('')\n"
        "fig.tight_layout()\n"
        "save_figure(fig, '箱线图.png')"
    ),
}

# 每个版本批次新增的默认预设键：合并时只补比文件版本更新的批次，
# 用户删除过的旧批次默认预设不会复活
DEFAULT_PRESET_VERSIONS = {
    2: ["描述统计", "缺失值统计", "数据类型", "去重", "分组聚合", "透视表",
        "相关性矩阵", "TopN 排序", "缺失值清洗", "直方图"],
    3: ["数据概览", "频次统计", "条件筛选导出", "新增计算列", "文本清洗",
        "日期处理", "异常值检测", "重复值检查", "条形图", "散点图", "箱线图"],
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

class _LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):
        return QSize(self._editor.line_number_width(), 0)

    def paintEvent(self, event):
        self._editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    """带行号栏、当前行高亮、自动缩进的代码编辑器；Tab 插入 4 空格。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._line_area = _LineNumberArea(self)
        self.blockCountChanged.connect(lambda _: self._update_margin())
        self.updateRequest.connect(self._on_update_request)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_margin()
        self._highlight_current_line()

    # ---- 行号栏 ----

    def line_number_width(self):
        digits = max(2, len(str(self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance('9') * digits

    def _update_margin(self):
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def _on_update_request(self, rect, dy):
        if dy:
            self._line_area.scroll(0, dy)
        else:
            self._line_area.update(0, rect.y(), self._line_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margin()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_area.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_width(), cr.height()))

    def paint_line_numbers(self, event):
        painter = QPainter(self._line_area)
        palette = self.palette()
        painter.fillRect(event.rect(), palette.color(QPalette.ColorRole.Window))
        painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
        painter.setFont(self.font())

        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block)
                    .translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        number = block.blockNumber() + 1
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(0, top, self._line_area.width() - 6, height,
                                 Qt.AlignmentFlag.AlignRight, str(number))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            number += 1

    # ---- 当前行高亮 ----

    def _highlight_current_line(self):
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(
            self.palette().color(QPalette.ColorRole.AlternateBase))
        selection.format.setProperty(
            QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])

    # ---- 编辑行为 ----

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Tab and not event.modifiers():
            self.insertPlainText("    ")
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and not event.modifiers():
            # 自动缩进：继承上一行缩进；行尾是冒号再加一级
            cursor = self.textCursor()
            line = cursor.block().text()[:cursor.positionInBlock()]
            indent = line[:len(line) - len(line.lstrip())]
            if line.rstrip().endswith(':'):
                indent += '    '
            super().keyPressEvent(event)
            if indent:
                self.insertPlainText(indent)
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
                raise TypeError(tr("结果必须是 pandas DataFrame"))
            if sheet_name is None:
                sheet_name = tr("分析结果_{}").format(datetime.now().strftime('%Y%m%d_%H%M%S'))
            sheet_requests.append((result_df.copy(), str(sheet_name)))
            print(tr("✓ 已加入保存队列: {}").format(sheet_name))

        def save_figure(fig, filename=None):
            if filename is None:
                filename = tr("分析图表_{}.png").format(datetime.now().strftime('%Y%m%d_%H%M%S'))
            if not os.path.dirname(filename):
                if self._current_file:
                    base_dir = os.path.dirname(self._current_file)
                else:
                    base_dir = os.path.join(os.path.expanduser("~"), "Desktop")
                filename = os.path.join(base_dir, filename)
            fig.savefig(filename, dpi=150, bbox_inches="tight")
            figure_files.append(filename)
            print(tr("✓ 图表已保存: {}").format(filename))

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
                buf.write("\n" + tr("[执行错误]") + "\n")
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

        self.setWindowTitle(tr("Python 数据分析"))
        self.resize(1000, 700)

        self.presets = self._load_presets()

        self._build_ui()
        self._update_preset_combo()

    # ---------------- UI ----------------

    def _build_ui(self):
        toolbar = QToolBar(tr("工具栏"))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addWidget(QLabel(tr(" 预设: ")))
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(180)
        # 选中即自动应用（编辑器有未保存的自定义内容时不覆盖）
        self.preset_combo.currentTextChanged.connect(self._on_preset_selected)
        toolbar.addWidget(self.preset_combo)

        apply_btn = QPushButton(tr("应用预设"))
        apply_btn.clicked.connect(lambda: self._apply_preset(append=False))
        toolbar.addWidget(apply_btn)

        append_btn = QPushButton(tr("追加"))
        append_btn.clicked.connect(lambda: self._apply_preset(append=True))
        toolbar.addWidget(append_btn)

        save_preset_btn = QPushButton(tr("保存为预设"))
        save_preset_btn.clicked.connect(self._save_current_as_preset)
        toolbar.addWidget(save_preset_btn)

        del_preset_btn = QPushButton(tr("删除预设"))
        del_preset_btn.clicked.connect(self._delete_preset)
        toolbar.addWidget(del_preset_btn)

        toolbar.addSeparator()

        self.run_action = QAction(tr("▶ 运行"), self)
        # F5 与 Cmd/Ctrl+Enter 都能运行（后者是笔记本用户的肌肉记忆）
        self.run_action.setShortcuts([
            QKeySequence(Qt.Key.Key_F5), QKeySequence("Ctrl+Return")])
        self.run_action.setToolTip(tr("运行代码（F5 或 Ctrl+Enter）"))
        self.run_action.triggered.connect(self.run_code)
        toolbar.addAction(self.run_action)

        clear_action = QAction(tr("清空输出"), self)
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
        self.code_edit.setPlaceholderText(tr(
            "# 可用变量: df (当前数据副本), pd, np\n"
            "# 可用函数: save_as_sheet(df, '名称'), save_figure(fig, '文件名.png')"
        ))
        self._highlighter = PythonHighlighter(self.code_edit.document())
        splitter.addWidget(self.code_edit)

        self.output_edit = QPlainTextEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setFont(mono)
        self.output_edit.setPlaceholderText(tr(
            "运行结果显示在这里（print 输出与错误信息）。\n"
            "运行后代码里的 DataFrame 变量会出现在下方下拉框，可预览或保存为 Sheet。"))
        splitter.addWidget(self.output_edit)

        splitter.setSizes([420, 220])

        # 结果 DataFrame 行
        result_row = QHBoxLayout()
        result_row.addWidget(QLabel(tr("结果 DataFrame:")))
        self.result_combo = QComboBox()
        self.result_combo.setMinimumWidth(160)
        result_row.addWidget(self.result_combo)

        preview_btn = QPushButton(tr("预览"))
        preview_btn.clicked.connect(self._preview_result)
        result_row.addWidget(preview_btn)

        save_sheet_btn = QPushButton(tr("保存为Sheet"))
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
                    version = data.pop(_DEFAULTS_VERSION_KEY, 1)
                    if version < DEFAULTS_VERSION:
                        # 只补比文件版本更新的批次（用户同名/已删除的不动），
                        # 立即写回版本号
                        for v in range(version + 1, DEFAULTS_VERSION + 1):
                            for key in DEFAULT_PRESET_VERSIONS.get(v, []):
                                data.setdefault(key, DEFAULT_PRESETS[key])
                        self._write_presets_file(data)
                    return data
        except Exception as e:
            print(f"加载预设失败: {e}")
        return dict(DEFAULT_PRESETS)

    @staticmethod
    def _write_presets_file(presets):
        os.makedirs(os.path.dirname(PRESETS_FILE), exist_ok=True)
        payload = {_DEFAULTS_VERSION_KEY: DEFAULTS_VERSION, **presets}
        with open(PRESETS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _save_presets(self):
        try:
            self._write_presets_file(self.presets)
        except Exception as e:
            QMessageBox.critical(self, tr("错误"), tr("保存预设失败:\n{}").format(e))

    def _update_preset_combo(self, select_name=None):
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItems(list(self.presets.keys()))
        if select_name and select_name in self.presets:
            self.preset_combo.setCurrentText(select_name)
        self.preset_combo.blockSignals(False)

    def _on_preset_selected(self, name):
        """下拉选中即自动应用；编辑器有自定义未保存内容时不覆盖。

        判定"干净"：为空，或与任一预设完全一致（说明是上次应用后未改动）。
        """
        if not name or name not in self.presets:
            return
        current = self.code_edit.toPlainText().strip()
        if current and current not in {c.strip() for c in self.presets.values()}:
            self._status(tr("编辑器有未保存内容，未自动应用；可点\"应用预设\"覆盖"))
            return
        self._apply_preset(append=False)

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
            QMessageBox.warning(self, tr("提示"), tr("编辑器内容为空，无法保存为预设"))
            return
        name, ok = QInputDialog.getText(self, tr("保存为预设"), tr("预设名称:"),
                                        text=self.preset_combo.currentText())
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self.presets:
            ans = QMessageBox.question(self, tr("确认"), tr("预设 “{}” 已存在，是否覆盖?").format(name))
            if ans != QMessageBox.StandardButton.Yes:
                return
        self.presets[name] = code
        self._save_presets()
        self._update_preset_combo(select_name=name)

    def _delete_preset(self):
        name = self.preset_combo.currentText()
        if not name or name not in self.presets:
            return
        ans = QMessageBox.question(self, tr("确认"), tr("确定删除预设 “{}”?").format(name))
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
            QMessageBox.warning(self, tr("提示"), tr("请输入要运行的代码"))
            return

        df = getattr(self.host.model, "df", None)
        df = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()

        self.output_edit.clear()
        self.output_edit.setPlainText(tr("正在运行...\n"))
        self.run_action.setEnabled(False)
        self._run_started = time.perf_counter()

        self._worker = CodeRunWorker(code, df, getattr(self.host, "current_file", None), self)
        self._worker.done.connect(self._on_run_done)
        self._worker.start()

    def _on_run_done(self, output, result_dfs, sheet_requests, figure_files):
        self.run_action.setEnabled(True)
        self.output_edit.setPlainText(output if output else tr("✓ 代码执行完成（无输出）\n"))
        sb = self.output_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

        # 结果 DataFrame 下拉框
        self._result_dfs = result_dfs
        self.result_combo.clear()
        for name, rdf in result_dfs.items():
            self.result_combo.addItem(
                tr("{} ({}行×{}列)").format(name, len(rdf), len(rdf.columns)), name)

        # save_as_sheet 队列在主线程统一执行（线程安全）
        for rdf, sheet_name in sheet_requests:
            try:
                self.host.add_sheet_from_df(rdf, sheet_name)
            except Exception as e:
                self.output_edit.appendPlainText(
                    tr("[保存Sheet失败] {}: {}").format(sheet_name, e))

        elapsed = time.perf_counter() - getattr(self, "_run_started", time.perf_counter())
        if sheet_requests:
            names = ", ".join(n for _, n in sheet_requests)
            self._status(tr("已保存 {} 个Sheet: {}（耗时 {:.2f} 秒）").format(
                len(sheet_requests), names, elapsed))
        else:
            self._status(tr("代码执行完成（耗时 {:.2f} 秒）").format(elapsed))

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
            QMessageBox.information(self, tr("提示"), tr("没有可用的结果 DataFrame，请先运行代码"))
            return None, None
        return name, self._result_dfs[name]

    def _preview_result(self):
        name, rdf = self._current_result_df()
        if rdf is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("预览 - {} ({}行×{}列)").format(name, len(rdf), len(rdf.columns)))
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
        sheet_name, ok = QInputDialog.getText(self, tr("保存为Sheet"), tr("Sheet 名称:"), text=default)
        if not ok or not sheet_name.strip():
            return
        try:
            self.host.add_sheet_from_df(rdf.copy(), sheet_name.strip())
            self._status(tr("已保存Sheet: {}").format(sheet_name.strip()))
        except Exception as e:
            QMessageBox.critical(self, tr("错误"), tr("保存Sheet失败:\n{}").format(e))

    # ---------------- 关闭 ----------------

    def closeEvent(self, event):
        # 等待后台线程结束，避免崩溃
        if self._worker is not None and self._worker.isRunning():
            self._worker.done.disconnect()
            self._worker.wait(3000)
        super().closeEvent(event)
