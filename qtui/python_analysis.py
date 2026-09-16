# -*- coding: utf-8 -*-
"""
Python 数据分析窗口：
代码编辑器（语法高亮、列名补全）、带参数表单的预设片段、后台运行代码、
结果 DataFrame / 图表内嵌展示、保存为 Sheet。
"""

import ast
import builtins
import io
import json
import keyword
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime

import pandas as pd
import numpy as np

from PyQt6.QtCore import (
    Qt, QThread, QObject, QMetaObject, Q_ARG, pyqtSignal, pyqtSlot,
    QRegularExpression, QRect, QSize, QTimer, QStringListModel,
)
from PyQt6.QtGui import (
    QAction, QColor, QFont, QKeySequence, QSyntaxHighlighter,
    QTextCharFormat, QPainter, QPalette, QTextFormat,
    QTextCursor, QPixmap,
)
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QPlainTextEdit, QSplitter, QToolBar, QMessageBox,
    QInputDialog, QDialog, QTableView, QDialogButtonBox, QTextEdit,
    QTabWidget, QScrollArea, QFormLayout, QLineEdit, QCheckBox, QCompleter,
    QMenu, QToolButton, QFileDialog, QGroupBox, QListWidget, QListWidgetItem,
    QApplication, QSizePolicy,
)

from .pandas_model import PandasTableModel
from . import analysis_params
from .analysis_params import (
    KIND_COLUMN, KIND_COLUMNS, KIND_NUMBER, KIND_BOOL, KIND_TEXT,
)
from qtui.i18n import tr
from qtui.paths import CONFIG_DIR

_CONFIG_DIR = CONFIG_DIR
PRESETS_FILE = os.path.join(_CONFIG_DIR, "qt_python_presets.json")
LAST_CODE_FILE = os.path.join(_CONFIG_DIR, "qt_python_last_code.py")   # 关窗时的编辑器内容
HISTORY_FILE = os.path.join(_CONFIG_DIR, "qt_python_history.json")    # 最近运行过的代码
HISTORY_LIMIT = 30

# 带菜单的工具栏按钮：Qt 默认把下拉小箭头画在文字上，改成文字自带 ▾
_MENU_BTN_STYLE = "QToolButton::menu-indicator { image: none; width: 0px; }"

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
        "# ===== 参数：把引号里的列名改成你的 =====\n"
        "GROUP_COL = '分组列'   # 按哪一列分组\n"
        "VALUE_COL = '数值列'   # 对哪一列聚合\n"
        "# =====================================\n"
        "result = df.groupby(GROUP_COL).agg({VALUE_COL: ['sum', 'mean', 'count']})\n"
        "print(result)\n"
        "# save_as_sheet(result.reset_index(), '分组聚合')"
    ),
    "透视表": (
        "# ===== 参数：把引号里的列名改成你的 =====\n"
        "ROW_DIM = '行维度'     # 透视表的行\n"
        "COL_DIM = '列维度'     # 透视表的列\n"
        "VALUE_COL = '数值列'   # 汇总的值\n"
        "# =====================================\n"
        "result = pd.pivot_table(df, index=ROW_DIM, columns=COL_DIM,\n"
        "                        values=VALUE_COL, aggfunc='sum', fill_value=0)\n"
        "print(result)\n"
        "# save_as_sheet(result.reset_index(), '透视表')"
    ),
    "相关性矩阵": (
        "result = df.select_dtypes('number').corr().round(3)\n"
        "print(result)\n"
        "# save_as_sheet(result.reset_index(), '相关性')"
    ),
    "TopN 排序": (
        "# ===== 参数 =====\n"
        "VALUE_COL = '数值列'   # 按哪一列排\n"
        "TOP_N = 10             # 取前几行\n"
        "# ===============\n"
        "result = df.nlargest(TOP_N, VALUE_COL)\n"
        "print(result)\n"
        "# save_as_sheet(result, 'TopN')"
    ),
    "缺失值清洗": (
        "# ===== 参数 =====\n"
        "FILL_VALUE = 0   # 数值列缺失的填充值\n"
        "# ===============\n"
        "result = df.dropna(how='all')  # 丢弃全空行\n"
        "result = result.fillna({c: FILL_VALUE\n"
        "                        for c in result.select_dtypes('number').columns})\n"
        "print(result.isnull().sum())\n"
        "# save_as_sheet(result, '清洗结果')"
    ),
    "直方图": (
        "# ===== 参数 =====\n"
        "VALUE_COL = '数值列'   # 画哪一列\n"
        "BINS = 30              # 分箱数\n"
        "# ===============\n"
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(figsize=(8, 5))\n"
        "df[VALUE_COL].hist(ax=ax, bins=BINS)\n"
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
        "# ===== 参数 =====\n"
        "CATEGORY_COL = '分类列'   # 统计哪一列\n"
        "# ===============\n"
        "counts = df[CATEGORY_COL].value_counts()\n"
        "result = counts.to_frame('次数')\n"
        "result['占比%'] = (counts / len(df) * 100).round(2)\n"
        "print(result)\n"
        "# save_as_sheet(result.reset_index(), '频次统计')"
    ),
    "条件筛选导出": (
        "# ===== 参数 =====\n"
        "VALUE_COL = '数值列'      # 数值条件的列\n"
        "THRESHOLD = 100           # 数值阈值\n"
        "CATEGORY_COL = '分类列'   # 分类条件的列\n"
        "TARGET = '目标值'         # 分类要等于什么\n"
        "# ===============\n"
        "# & 与、| 或，注意每个条件加括号\n"
        "result = df[(df[VALUE_COL] > THRESHOLD) & (df[CATEGORY_COL] == TARGET)]\n"
        "print(f'筛出 {len(result)} 行')\n"
        "save_as_sheet(result, '筛选结果')"
    ),
    "新增计算列": (
        "# ===== 参数 =====\n"
        "QTY_COL = '数量'            # 数量列\n"
        "PRICE_COL = '单价'          # 单价列\n"
        "AMOUNT_THRESHOLD = 10000    # 金额分档阈值\n"
        "# ===============\n"
        "import numpy as np\n"
        "result = df.copy()\n"
        "result['金额'] = result[QTY_COL] * result[PRICE_COL]\n"
        "result['等级'] = np.where(result['金额'] >= AMOUNT_THRESHOLD, '高', '普通')\n"
        "print(result.head())\n"
        "# save_as_sheet(result, '含计算列')"
    ),
    "文本清洗": (
        "# ===== 参数 =====\n"
        "TEXT_COL = '文本列'   # 清洗哪一列\n"
        "# ===============\n"
        "result = df.copy()\n"
        "result[TEXT_COL] = (result[TEXT_COL].astype(str)\n"
        "                    .str.strip()          # 去首尾空格\n"
        "                    .str.upper()          # 转大写（不需要就删掉）\n"
        "                    .str.replace('旧', '新', regex=False))\n"
        "# 按分隔符拆出新列：\n"
        "# result[['前段', '后段']] = result[TEXT_COL].str.split('-', n=1, expand=True)\n"
        "print(result.head())\n"
        "# save_as_sheet(result, '清洗结果')"
    ),
    "日期处理": (
        "# ===== 参数 =====\n"
        "DATE_COL = '日期列'    # 日期列\n"
        "VALUE_COL = '数值列'   # 按月汇总的数值列\n"
        "# ===============\n"
        "result = df.copy()\n"
        "result[DATE_COL] = pd.to_datetime(result[DATE_COL], errors='coerce')\n"
        "result['年'] = result[DATE_COL].dt.year\n"
        "result['月'] = result[DATE_COL].dt.to_period('M').astype(str)\n"
        "monthly = result.groupby('月')[VALUE_COL].sum()\n"
        "print(monthly)\n"
        "# save_as_sheet(monthly.reset_index(), '按月汇总')"
    ),
    "异常值检测": (
        "# ===== 参数 =====\n"
        "VALUE_COL = '数值列'   # 检测哪一列\n"
        "IQR_FACTOR = 1.5       # IQR 倍数，越大越宽松\n"
        "# ===============\n"
        "q1, q3 = df[VALUE_COL].quantile(0.25), df[VALUE_COL].quantile(0.75)\n"
        "iqr = q3 - q1\n"
        "lo, hi = q1 - IQR_FACTOR * iqr, q3 + IQR_FACTOR * iqr\n"
        "result = df[(df[VALUE_COL] < lo) | (df[VALUE_COL] > hi)]\n"
        "print(f'正常范围 [{lo:.2f}, {hi:.2f}]，异常 {len(result)} 行')\n"
        "print(result.head(20))\n"
        "# save_as_sheet(result, '异常值')"
    ),
    "重复值检查": (
        "# ===== 参数：整行判断用 None，按某几列判断填 ['列1', '列2'] =====\n"
        "SUBSET_COLS = None\n"
        "# =========================================================\n"
        "# keep=False 把每组重复全部列出；只想去重用\"去重\"预设\n"
        "result = df[df.duplicated(subset=SUBSET_COLS, keep=False)]\n"
        "print(f'重复行数: {len(result)}')\n"
        "print(result.head(20))\n"
        "# save_as_sheet(result, '重复行')"
    ),
    "条形图": (
        "# ===== 参数 =====\n"
        "CATEGORY_COL = '分类列'   # 统计哪一列\n"
        "TOP_N = 15                # 显示前几名\n"
        "# ===============\n"
        "import matplotlib.pyplot as plt\n"
        "counts = df[CATEGORY_COL].value_counts().head(TOP_N)\n"
        "fig, ax = plt.subplots(figsize=(9, 5))\n"
        "counts.plot.barh(ax=ax)\n"
        "ax.invert_yaxis()\n"
        "fig.tight_layout()\n"
        "save_figure(fig, '条形图.png')"
    ),
    "散点图": (
        "# ===== 参数 =====\n"
        "X_COL = '数值列X'\n"
        "Y_COL = '数值列Y'\n"
        "# ===============\n"
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(figsize=(7, 6))\n"
        "df.plot.scatter(x=X_COL, y=Y_COL, alpha=0.5, ax=ax)\n"
        "save_figure(fig, '散点图.png')"
    ),
    "箱线图": (
        "# ===== 参数 =====\n"
        "VALUE_COL = '数值列'   # 数值列\n"
        "GROUP_COL = '分类列'   # 按哪一列分组对比\n"
        "# ===============\n"
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(figsize=(8, 5))\n"
        "df.boxplot(column=VALUE_COL, by=GROUP_COL, ax=ax)\n"
        "fig.suptitle('')\n"
        "fig.tight_layout()\n"
        "save_figure(fig, '箱线图.png')"
    ),
}

# 每个版本批次新增的默认预设键（仅用于旧格式预设文件的迁移）
DEFAULT_PRESET_VERSIONS = {
    2: ["描述统计", "缺失值统计", "数据类型", "去重", "分组聚合", "透视表",
        "相关性矩阵", "TopN 排序", "缺失值清洗", "直方图"],
    3: ["数据概览", "频次统计", "条件筛选导出", "新增计算列", "文本清洗",
        "日期处理", "异常值检测", "重复值检查", "条形图", "散点图", "箱线图"],
}

# 历史版本的默认预设文本：旧格式文件迁移时与之完全一致的条目
# 视为"未改动的旧默认"，直接采用内置的最新版本（用户改过的绝不动）
_SUPERSEDED_DEFAULT_TEXTS = {
    "分组聚合": [
        "# 把列名改成你的：按一列分组，对数值列做多种聚合\nresult = df.groupby('分组列').agg({'数值列': ['sum', 'mean', 'count']})\nprint(result)\n# save_as_sheet(result.reset_index(), '分组聚合')"
    ],
    "透视表": [
        "# 把行/列/值改成你的列名\nresult = pd.pivot_table(df, index='行维度', columns='列维度',\n                        values='数值列', aggfunc='sum', fill_value=0)\nprint(result)\n# save_as_sheet(result.reset_index(), '透视表')"
    ],
    "TopN 排序": [
        "# 按某数值列取最大的 10 行\nresult = df.nlargest(10, '数值列')\nprint(result)\n# save_as_sheet(result, 'Top10')"
    ],
    "缺失值清洗": [
        "# 丢弃全空行，数值列缺失填 0\nresult = df.dropna(how='all')\nresult = result.fillna({c: 0 for c in result.select_dtypes('number').columns})\nprint(result.isnull().sum())\n# save_as_sheet(result, '清洗结果')"
    ],
    "直方图": [
        "import matplotlib.pyplot as plt\nfig, ax = plt.subplots(figsize=(8, 5))\ndf['数值列'].hist(ax=ax, bins=30)\nsave_figure(fig, '直方图.png')"
    ],
    "频次统计": [
        "# 统计某一列每个值出现的次数与占比，改成你的列名\ncounts = df['分类列'].value_counts()\nresult = counts.to_frame('次数')\nresult['占比%'] = (counts / len(df) * 100).round(2)\nprint(result)\n# save_as_sheet(result.reset_index(), '频次统计')"
    ],
    "条件筛选导出": [
        "# 按条件筛选行并另存为 Sheet（& 与、| 或，注意每个条件加括号）\nresult = df[(df['数值列'] > 100) & (df['分类列'] == '目标值')]\nprint(f'筛出 {len(result)} 行')\nsave_as_sheet(result, '筛选结果')"
    ],
    "新增计算列": [
        "import numpy as np\nresult = df.copy()\n# 算术：两列相乘\nresult['金额'] = result['数量'] * result['单价']\n# 条件分档\nresult['等级'] = np.where(result['金额'] >= 10000, '高', '普通')\nprint(result.head())\n# save_as_sheet(result, '含计算列')"
    ],
    "文本清洗": [
        "result = df.copy()\ncol = '文本列'  # 改成你的列名\nresult[col] = (result[col].astype(str)\n               .str.strip()          # 去首尾空格\n               .str.upper()          # 转大写（不需要就删掉）\n               .str.replace('旧', '新', regex=False))\n# 按分隔符拆出新列：\n# result[['前段', '后段']] = result[col].str.split('-', n=1, expand=True)\nprint(result.head())\n# save_as_sheet(result, '清洗结果')"
    ],
    "日期处理": [
        "result = df.copy()\ncol = '日期列'  # 改成你的列名\nresult[col] = pd.to_datetime(result[col], errors='coerce')\nresult['年'] = result[col].dt.year\nresult['月'] = result[col].dt.to_period('M').astype(str)\n# 按月聚合：\nmonthly = result.groupby('月')['数值列'].sum()\nprint(monthly)\n# save_as_sheet(monthly.reset_index(), '按月汇总')"
    ],
    "异常值检测": [
        "# IQR 法：找出超出 [Q1-1.5IQR, Q3+1.5IQR] 的行\ncol = '数值列'  # 改成你的列名\nq1, q3 = df[col].quantile(0.25), df[col].quantile(0.75)\niqr = q3 - q1\nlo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr\nresult = df[(df[col] < lo) | (df[col] > hi)]\nprint(f'正常范围 [{lo:.2f}, {hi:.2f}]，异常 {len(result)} 行')\nprint(result.head(20))\n# save_as_sheet(result, '异常值')"
    ],
    "重复值检查": [
        "# 查看重复的行（keep=False 把每组重复全部列出）；只想去重用\"去重\"预设\n# 按整行判断：subset=None；按某几列判断：subset=['列1', '列2']\nresult = df[df.duplicated(subset=None, keep=False)]\nprint(f'重复行数: {len(result)}')\nprint(result.head(20))\n# save_as_sheet(result, '重复行')"
    ],
    "条形图": [
        "import matplotlib.pyplot as plt\ncounts = df['分类列'].value_counts().head(15)\nfig, ax = plt.subplots(figsize=(9, 5))\ncounts.plot.barh(ax=ax)\nax.invert_yaxis()\nfig.tight_layout()\nsave_figure(fig, '条形图.png')"
    ],
    "散点图": [
        "import matplotlib.pyplot as plt\nfig, ax = plt.subplots(figsize=(7, 6))\ndf.plot.scatter(x='数值列X', y='数值列Y', alpha=0.5, ax=ax)\nsave_figure(fig, '散点图.png')"
    ],
    "箱线图": [
        "import matplotlib.pyplot as plt\nfig, ax = plt.subplots(figsize=(8, 5))\ndf.boxplot(column='数值列', by='分类列', ax=ax)\nfig.suptitle('')\nfig.tight_layout()\nsave_figure(fig, '箱线图.png')"
    ]
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
    """带行号栏、当前行高亮、自动缩进的代码编辑器；Tab 插入 4 空格。

    在字符串字面量里打字时弹出列名补全（Ctrl+Space 可强制弹出）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._line_area = _LineNumberArea(self)
        self.blockCountChanged.connect(lambda _: self._update_margin())
        self.updateRequest.connect(self._on_update_request)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_margin()
        self._highlight_current_line()

        self._completion_model = QStringListModel(self)
        self._completer = QCompleter(self._completion_model, self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.activated.connect(self._insert_completion)

    # ---- 列名补全 ----

    def set_completion_words(self, words):
        self._completion_model.setStringList([str(w) for w in words])

    def string_prefix(self):
        """光标若在未闭合的字符串里，返回 (引号, 引号后到光标的文本)；否则 None。"""
        cursor = self.textCursor()
        text = cursor.block().text()[:cursor.positionInBlock()]
        quote, start = None, -1
        i = 0
        while i < len(text):
            ch = text[i]
            if quote:
                if ch == "\\":
                    i += 1
                elif ch == quote:
                    quote = None
            elif ch in ("'", '"'):
                quote, start = ch, i
            elif ch == "#":
                return None
            i += 1
        if quote is None:
            return None
        return quote, text[start + 1:]

    def _maybe_complete(self, force=False):
        popup = self._completer.popup()
        if self._completion_model.rowCount() == 0:
            popup.hide()
            return
        ctx = self.string_prefix()
        if ctx is None or (not force and not ctx[1]):
            popup.hide()
            return
        prefix = ctx[1]
        self._completer.setCompletionPrefix(prefix)
        count = self._completer.completionCount()
        if count == 0 or (count == 1 and self._completer.currentCompletion() == prefix):
            popup.hide()
            return
        popup.setCurrentIndex(self._completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(max(220, popup.sizeHintForColumn(0)
                          + popup.verticalScrollBar().sizeHint().width() + 8))
        self._completer.complete(rect)

    def _insert_completion(self, text):
        ctx = self.string_prefix()
        if ctx is None:
            return
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Left,
                            QTextCursor.MoveMode.KeepAnchor, len(ctx[1]))
        cursor.insertText(text)
        self.setTextCursor(cursor)

    def insert_column_name(self, name):
        """在光标处插入列名：已在字符串里就只插名字，否则连引号一起插。"""
        ctx = self.string_prefix()
        if ctx is not None:
            self.insertPlainText(name)
        else:
            quote = '"' if "'" in name else "'"
            self.insertPlainText(f"{quote}{name}{quote}")
        self.setFocus()

    def replace_line(self, line_no, text):
        """只替换某一行的文本（不重置光标/撤销栈）。"""
        block = self.document().findBlockByNumber(line_no)
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        cursor.select(QTextCursor.SelectionType.LineUnderCursor)
        cursor.insertText(text)

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
        key = event.key()
        if self._completer.popup().isVisible() and key in (
                Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape,
                Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            event.ignore()   # 交给补全弹层处理
            return
        if key == Qt.Key.Key_Space and \
                event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._maybe_complete(force=True)
            return
        if key == Qt.Key.Key_Tab and not event.modifiers():
            self.insertPlainText("    ")
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
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
        # 只在打字（有可见字符或退格）时刷新补全，方向键等不动它
        if event.text() or key == Qt.Key.Key_Backspace:
            self._maybe_complete()


# ---------------------------------------------------------------------------
# 后台执行线程
# ---------------------------------------------------------------------------

class _Cancelled(BaseException):
    """用户点击"停止"后在工作线程内抛出，中断用户代码。"""


class _StreamRouter:
    """按线程分流 stdout/stderr：工作线程的输出进缓冲区，其它线程照常输出。

    contextlib.redirect_stdout 是进程全局替换，会把主线程（例如加载/保存
    线程的报错）在分析运行期间打印的内容一并吞进分析输出里。
    """

    _lock = threading.Lock()

    def __init__(self, original):
        self._original = original
        self._targets = {}

    def _target(self):
        return self._targets.get(threading.get_ident(), self._original)

    def write(self, text):
        target = self._target()
        if target is not None:
            return target.write(text)
        return len(text)

    def flush(self):
        target = self._target()
        if target is not None and hasattr(target, "flush"):
            target.flush()

    def __getattr__(self, name):
        return getattr(self._original, name)

    @classmethod
    def capture(cls, attr, buf):
        """把当前线程的 sys.<attr> 输出接到 buf；返回撤销函数。"""
        ident = threading.get_ident()
        with cls._lock:
            stream = getattr(sys, attr)
            if not isinstance(stream, cls):
                stream = cls(stream)
                setattr(sys, attr, stream)
            stream._targets[ident] = buf

        def restore():
            with cls._lock:
                stream._targets.pop(ident, None)
                # 没有其它线程在用时还原原始流
                if not stream._targets and getattr(sys, attr) is stream:
                    setattr(sys, attr, stream._original)
        return restore


# 用户代码编译时的文件名：追踪器只跟踪这个文件里的帧，报错定位也按它找行号
_USER_CODE_FILENAME = "<string>"


class CodeRunWorker(QThread):
    """在后台线程执行用户代码，结果通过信号回传主线程。"""

    # 输出文本, DataFrame变量, sheet保存请求, 图片保存记录, 图表 PNG [(标题, bytes)]
    done = pyqtSignal(str, dict, list, list, list)

    def __init__(self, code, df, current_file, parent=None, extra=None):
        super().__init__(parent)
        self._code = code
        self._df = df
        self._current_file = current_file
        self._extra = dict(extra or {})   # 额外注入命名空间的变量（sheets、selection 等）
        self._cancel = threading.Event()

    def cancel(self):
        """请求停止：用户代码下一次执行到任何一行时抛出 _Cancelled。"""
        self._cancel.set()

    def cancel_requested(self):
        return self._cancel.is_set()

    def _tracer(self, frame, event, arg):
        # 只在工作线程生效（sys.settrace 是线程局部的）。
        # 只给用户代码自己的帧装行级追踪：pandas/numpy 内部的帧返回 None，
        # 否则用户的循环/apply 里每一行库代码都要回调一次 Python，慢好几倍。
        if frame.f_code.co_filename != _USER_CODE_FILENAME:
            return None
        if self._cancel.is_set():
            raise _Cancelled()
        return self._tracer

    def run(self):
        buf = io.StringIO()
        sheet_requests = []   # [(DataFrame, sheet_name)]
        figure_files = []
        figure_images = []    # [(标题, PNG bytes)]，窗口内嵌展示
        result_dfs = {}

        # 强制 matplotlib 使用 Agg，避免后台线程弹 GUI；顺带配好中文字体
        try:
            import matplotlib
            matplotlib.use("Agg", force=True)
            _configure_matplotlib_fonts(matplotlib)
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
            figure_images.append((os.path.basename(filename), _fig_to_png(fig)))
            print(tr("✓ 图表已保存: {}").format(filename))
            # 保存即关闭，避免每次运行都在 pyplot 里累积图形（内存持续增长）
            _close_figure(fig)

        def show_figure(fig, title=None):
            """只在窗口里显示，不落盘。"""
            figure_images.append((str(title or tr("图 {}").format(len(figure_images) + 1)),
                                  _fig_to_png(fig)))
            _close_figure(fig)

        def _exit(code=None):
            raise SystemExit(code)

        namespace = {
            "df": self._df,
            "pd": pd,
            "np": np,
            "save_as_sheet": save_as_sheet,
            "save_figure": save_figure,
            "show_figure": show_figure,
            "exit": _exit,
            "quit": _exit,
            "__builtins__": builtins,
        }
        namespace.update(self._extra)

        restore_out = _StreamRouter.capture("stdout", buf)
        restore_err = _StreamRouter.capture("stderr", buf)
        sys.settrace(self._tracer)
        try:
            exec(compile(self._code, _USER_CODE_FILENAME, "exec"), namespace)
        except _Cancelled:
            buf.write("\n" + tr("[已停止] 用户中断了代码执行") + "\n")
        except KeyboardInterrupt:
            buf.write("\n" + tr("[已停止] 代码被 KeyboardInterrupt 中断") + "\n")
        except SystemExit as e:
            # 用户脚本里常见的 exit()/sys.exit()：只结束本次运行，绝不能退出整个程序
            code = e.code
            suffix = "" if code in (None, 0) else tr("（退出码 {}）").format(code)
            buf.write("\n" + tr("[执行结束] 代码调用了 exit()/sys.exit()") + suffix + "\n")
        except BaseException as e:
            buf.write("\n" + tr("[执行错误]") + "\n")
            buf.write(traceback.format_exc())
            where = _user_code_location(e, self._code)
            if where:
                buf.write("\n" + where + "\n")
            try:
                hint = analysis_params.column_hint(e, list(self._df.columns), tr)
            except Exception:
                hint = None
            if hint:
                buf.write("\n💡 " + hint + "\n")
        finally:
            sys.settrace(None)
            restore_out()
            restore_err()
            # 用户直接 df.plot() 没调 save_figure 的图也抓下来显示
            figure_images.extend(_capture_open_figures(len(figure_images)))
            _close_all_figures()

        # 扫描命名空间中的 DataFrame 变量
        for name, value in namespace.items():
            if name.startswith("__") or name in self._extra:
                continue
            if isinstance(value, pd.DataFrame):
                result_dfs[name] = value

        # 线程对象本身在 finished→deleteLater 时整个销毁（见 run_code），
        # 这一轮的 df 副本随之释放，不必在这里提前清空
        self.done.emit(buf.getvalue(), result_dfs, sheet_requests,
                       figure_files, figure_images)


def _user_code_location(exc, code):
    """从异常回溯里找到用户代码（exec 的 <string>）最后一帧，给出行号和那行内容。"""
    try:
        frames = [f for f in traceback.extract_tb(exc.__traceback__)
                  if f.filename == _USER_CODE_FILENAME]
    except Exception:
        return None
    if not frames:
        return None
    lineno = frames[-1].lineno
    lines = code.split("\n")
    text = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
    return tr("⚠ 出错位置：你的代码第 {} 行：{}").format(lineno, text)


def _is_default_index(idx):
    return (isinstance(idx, pd.RangeIndex) and idx.start == 0
            and idx.step == 1 and idx.name is None)


def _flatten_columns(rdf):
    """多级列名（agg 多个函数）压成一层，用 "_" 连接；没有多级列时原样返回。"""
    if not isinstance(rdf.columns, pd.MultiIndex):
        return rdf
    out = rdf.copy()
    out.columns = ["_".join(str(x) for x in tup if str(x) != "")
                   for tup in out.columns.to_flat_index()]
    return out


def flatten_frame(rdf):
    """把分析结果整理成能放进表格/Sheet 的二维表。

    透视表、groupby 的结果把分组值放在索引里，直接显示会丢掉行标签；
    多级列名（agg 多个函数）也压成一层，用 "_" 连接。原对象不改——
    不需要整理时直接返回原对象（调用方都是只读展示/复制进模型，不会改它），
    省掉一次整表复制。
    """
    out = _flatten_columns(rdf)
    if not _is_default_index(out.index):
        try:
            out = out.reset_index()
        except ValueError:
            # 索引名和列名撞了：先改成不撞的名字
            out = out.copy()
            names = [n if n is not None else f"index{i}" for i, n in enumerate(out.index.names)]
            names = [f"{n}_index" if n in out.columns else n for n in names]
            out.index = out.index.set_names(names)
            out = out.reset_index()
    return out


def align_result_rows(rdf, target_index):
    """把要「追加为新列」的结果按行对齐到当前表。

    返回 (对齐后的表, None)，或 (None, 原因)：原因为 "rows"（行数不同）
    或 "index"（行标签对不上）。

    - 默认索引（0..n-1）或与当前表索引完全一致：按位置追加；
    - 索引是当前表行标签的一个排列（例如 sort_values 后的结果）：先按当前表
      的顺序 reindex，再按位置追加——不这样做会错行，还会多出一列 index；
    - 其他情况（groupby 的分组标签、set_index 后的键等）：不猜，交给调用方提示用户。
    """
    out = _flatten_columns(rdf)
    if len(out) != len(target_index):
        return None, "rows"
    idx = out.index
    if _is_default_index(idx) or idx.equals(target_index):
        return out.reset_index(drop=True), None
    try:
        same_labels = (idx.is_unique and target_index.is_unique
                       and len(idx.difference(target_index)) == 0)
    except Exception:
        same_labels = False
    if same_labels:
        return out.reindex(target_index).reset_index(drop=True), None
    return None, "index"


_FONTS_CONFIGURED = False
# 各平台常见的中文字体，按优先级；找不到就维持默认（只影响图里的中文显示）
_CJK_FONT_CANDIDATES = [
    "PingFang SC", "Hiragino Sans GB", "Heiti SC", "STHeiti", "Arial Unicode MS",   # macOS
    "Microsoft YaHei", "SimHei", "SimSun",                                          # Windows
    "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", "WenQuanYi Micro Hei",  # Linux
]


def _configure_matplotlib_fonts(matplotlib):
    """让图表里的中文不再显示成方块；只做一次。"""
    global _FONTS_CONFIGURED
    if _FONTS_CONFIGURED:
        return
    _FONTS_CONFIGURED = True
    try:
        from matplotlib import font_manager
        available = {f.name for f in font_manager.fontManager.ttflist}
        chosen = [f for f in _CJK_FONT_CANDIDATES if f in available]
        if chosen:
            matplotlib.rcParams["font.sans-serif"] = chosen + list(
                matplotlib.rcParams.get("font.sans-serif", []))
            matplotlib.rcParams["font.family"] = "sans-serif"
        matplotlib.rcParams["axes.unicode_minus"] = False
    except Exception:
        pass


def _fig_to_png(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    return buf.getvalue()


def _capture_open_figures(start_index=0):
    """把 pyplot 里仍打开的图渲染成 PNG（用户没显式调用 save_figure 的情况）。"""
    plt = sys.modules.get("matplotlib.pyplot")
    images = []
    if plt is None:
        return images
    try:
        nums = list(plt.get_fignums())
    except Exception:
        return images
    for i, num in enumerate(nums):
        try:
            fig = plt.figure(num)
            if not fig.get_axes():
                continue
            images.append((tr("图 {}").format(start_index + i + 1), _fig_to_png(fig)))
        except Exception:
            continue
    return images


def _close_figure(fig):
    plt = sys.modules.get("matplotlib.pyplot")
    if plt is not None:
        try:
            plt.close(fig)
        except Exception:
            pass


def _close_all_figures():
    plt = sys.modules.get("matplotlib.pyplot")
    if plt is not None:
        try:
            plt.close("all")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 参数表单：预设开头的参数块渲染成控件，改控件即改代码
# ---------------------------------------------------------------------------

# 预设下拉按类别分组；不在这里的默认预设归入"其他"，用户自定义归入"我的预设"
PRESET_CATEGORIES = [
    ("概览与检查", ["数据概览", "描述统计", "数据类型", "缺失值统计",
                   "重复值检查", "异常值检测"]),
    ("清洗与加工", ["去重", "缺失值清洗", "文本清洗", "日期处理",
                   "新增计算列", "条件筛选导出"]),
    ("统计分析", ["频次统计", "分组聚合", "透视表", "TopN 排序", "相关性矩阵"]),
    ("图表", ["直方图", "条形图", "散点图", "箱线图"]),
]


class _ColumnsPicker(QWidget):
    """列名列表参数：文本框（逗号分隔，留空为 None）+ 勾选对话框。"""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._columns = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(tr("留空 = None；多个列名用逗号分隔"))
        self.edit.editingFinished.connect(self.changed)
        lay.addWidget(self.edit, 1)
        btn = QPushButton(tr("选择…"))
        btn.clicked.connect(self._pick)
        lay.addWidget(btn)

    def set_columns(self, columns):
        self._columns = list(columns)

    def value(self):
        text = self.edit.text().strip()
        if not text:
            return None
        return [c.strip() for c in text.split(",") if c.strip()]

    def set_value(self, value):
        self.edit.setText(", ".join(map(str, value)) if isinstance(value, (list, tuple)) else "")

    def _pick(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("选择列"))
        lay = QVBoxLayout(dlg)
        lst = QListWidget()
        chosen = set(self.value() or [])
        for c in self._columns:
            item = QListWidgetItem(c)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if c in chosen else Qt.CheckState.Unchecked)
            lst.addItem(item)
        lay.addWidget(lst)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        dlg.resize(320, 400)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        picked = [lst.item(i).text() for i in range(lst.count())
                  if lst.item(i).checkState() == Qt.CheckState.Checked]
        self.set_value(picked)
        self.changed.emit()


class ParamPanel(QWidget):
    """参数块表单。列名参数下拉选列；数字/文本直接输入；布尔为勾选框。"""

    value_changed = pyqtSignal(str, object)   # (参数名, 新值)

    _BAD_STYLE = "QLineEdit { color: #c0392b; }"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._columns = []
        self._params = []
        self._widgets = {}      # name -> (kind, widget)
        self._loading = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._box = QGroupBox(tr("参数（改这里即改代码）"))
        self._form = QFormLayout(self._box)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._form.setContentsMargins(8, 4, 8, 6)
        self._form.setVerticalSpacing(4)
        outer.addWidget(self._box)
        self.hide()

    # ---- 对外 ----

    def set_columns(self, columns):
        self._columns = [str(c) for c in columns]
        self._loading = True
        try:
            for kind, w in self._widgets.values():
                if kind == KIND_COLUMN:
                    self._fill_column_combo(w, w.currentText())
                elif kind == KIND_COLUMNS:
                    w.set_columns(self._columns)
        finally:
            self._loading = False

    def load(self, code):
        """按代码重建/刷新表单；返回是否有参数。"""
        params = analysis_params.parse_params(code)
        signature = [(p.name, p.kind) for p in params]
        self._loading = True
        try:
            if signature != [(p.name, p.kind) for p in self._params]:
                self._rebuild(params)
            else:
                for p in params:
                    self._set_widget_value(p)
        finally:
            self._loading = False
        self._params = params
        self.setVisible(bool(params))
        return bool(params)

    def params(self):
        return list(self._params)

    def widget_for(self, name):
        return self._widgets.get(name, (None, None))[1]

    # ---- 内部 ----

    def _rebuild(self, params):
        while self._form.rowCount():
            self._form.removeRow(0)
        self._widgets = {}
        for p in params:
            w = self._make_widget(p)
            self._widgets[p.name] = (p.kind, w)
            label = QLabel(p.comment or p.name)
            label.setToolTip(p.name)
            w.setToolTip(p.name)
            self._form.addRow(label, w)
            self._set_widget_value(p)

    def _fill_column_combo(self, combo, current):
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(self._columns)
        combo.setCurrentText(current)
        combo.blockSignals(False)
        self._mark_column_combo(combo)

    def _mark_column_combo(self, combo):
        # 值不在当前列里（多半还是占位符）→ 标红提醒
        ok = combo.currentText() in self._columns or not self._columns
        combo.lineEdit().setStyleSheet("" if ok else self._BAD_STYLE)

    def _make_widget(self, p):
        if p.kind == KIND_COLUMN:
            w = QComboBox()
            w.setEditable(True)
            w.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._fill_column_combo(w, str(p.value))
            w.currentTextChanged.connect(
                lambda text, n=p.name, c=w: self._emit_column(n, c, text))
            return w
        if p.kind == KIND_COLUMNS:
            w = _ColumnsPicker()
            w.set_columns(self._columns)
            w.changed.connect(lambda n=p.name, c=w: self._emit(n, c.value()))
            return w
        if p.kind == KIND_BOOL:
            w = QCheckBox()
            w.toggled.connect(lambda v, n=p.name: self._emit(n, bool(v)))
            return w
        w = QLineEdit()
        if p.kind == KIND_NUMBER:
            w.editingFinished.connect(lambda n=p.name, c=w: self._emit_number(n, c))
        elif p.kind == KIND_TEXT:
            w.editingFinished.connect(lambda n=p.name, c=w: self._emit(n, c.text()))
        else:
            w.editingFinished.connect(lambda n=p.name, c=w: self._emit_literal(n, c))
        return w

    def _set_widget_value(self, p):
        kind, w = self._widgets.get(p.name, (None, None))
        if w is None:
            return
        w.blockSignals(True)
        try:
            if kind == KIND_COLUMN:
                w.setCurrentText(str(p.value))
                self._mark_column_combo(w)
            elif kind == KIND_COLUMNS:
                w.set_value(p.value)
            elif kind == KIND_BOOL:
                w.setChecked(bool(p.value))
            elif kind == KIND_TEXT:
                w.setText(str(p.value))
            else:
                w.setText(repr(p.value))
        finally:
            w.blockSignals(False)

    def _emit(self, name, value):
        if not self._loading:
            self.value_changed.emit(name, value)

    def _emit_column(self, name, combo, text):
        self._mark_column_combo(combo)
        self._emit(name, text)

    def _emit_number(self, name, edit):
        text = edit.text().strip()
        try:
            value = int(text) if re.fullmatch(r"[+-]?\d+", text) else float(text)
        except ValueError:
            edit.setStyleSheet(self._BAD_STYLE)
            return
        edit.setStyleSheet("")
        self._emit(name, value)

    def _emit_literal(self, name, edit):
        try:
            value = ast.literal_eval(edit.text().strip())
        except (ValueError, SyntaxError):
            edit.setStyleSheet(self._BAD_STYLE)
            return
        edit.setStyleSheet("")
        self._emit(name, value)


# ---------------------------------------------------------------------------
# 图表画廊：运行产生的图直接显示在窗口里
# ---------------------------------------------------------------------------

class _MainThreadSheetFetcher(QObject):
    """替工作线程在主线程里执行 host.get_sheet_df。

    sheets['名字'] 是在工作线程里被调用的，而 host.get_sheet_df 会读主线程
    正在用的 model.df / sheet 缓存 / 共享的 ExcelFile——主线程此时可能正在
    编辑单元格或切 sheet，直接读会撞上。这里用 BlockingQueuedConnection 把
    读取（含复制）投递到主线程排队执行，工作线程阻塞等结果；每次运行前
    把所有 sheet 都预先复制一份的做法则太贵（多 sheet 大文件每次运行都要
    全量读盘）。对象本身必须在主线程创建（在 _extra_namespace 里）。
    """

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._result = None
        self._error = None
        self._owner_ident = threading.get_ident()   # 创建它的线程（主线程）

    @pyqtSlot(str)
    def _fetch(self, name):
        try:
            self._result = self._host.get_sheet_df(name)
            self._error = None
        except BaseException as exc:      # noqa: BLE001 —— 原样带回工作线程重新抛出
            self._result, self._error = None, exc

    def get(self, name):
        if threading.get_ident() == self._owner_ident:
            # 已在主线程（同步测试直接调 worker.run()）：直接调用。
            # 这里不能用 QThread.currentThread() is self.thread() 判断——同一个
            # C++ 线程可能对应两个 Python 包装对象，误判后 BlockingQueuedConnection
            # 会等自己，整个进程卡死
            self._fetch(name)
        else:
            QMetaObject.invokeMethod(
                self, "_fetch", Qt.ConnectionType.BlockingQueuedConnection,
                Q_ARG(str, name))
        result, error = self._result, self._error
        self._result = self._error = None
        if error is not None:
            raise error
        return result


class _SheetAccessor:
    """用户代码里的 sheets 对象：sheets['名字'] 按需读取某个 sheet 的完整数据。

    读取本身经 _MainThreadSheetFetcher 在主线程完成（见其说明）。
    """

    def __init__(self, host, names, fetcher=None):
        self._host = host
        self._names = list(names)
        self._fetcher = fetcher

    def keys(self):
        return list(self._names)

    def __iter__(self):
        return iter(self._names)

    def __len__(self):
        return len(self._names)

    def __contains__(self, name):
        return name in self._names

    def __getitem__(self, name):
        if isinstance(name, int):
            name = self._names[name]
        if getattr(self._host, "get_sheet_df", None) is None:
            raise KeyError(name)
        try:
            if self._fetcher is not None:
                return self._fetcher.get(str(name))
            return self._host.get_sheet_df(name)
        except KeyError:
            raise KeyError(tr("没有名为 {} 的 Sheet，可用: {}").format(
                repr(name), ", ".join(repr(n) for n in self._names))) from None

    def get(self, name, default=None):
        try:
            return self[name]
        except KeyError:
            return default

    def __repr__(self):
        return f"sheets{self._names!r}"


class FigureGallery(QScrollArea):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(self._container)
        self._images = []
        self._empty = QLabel(tr("运行画图代码后，图表会显示在这里。\n"
                                "预设里的 save_figure(fig, '文件名.png') 会同时保存文件；"
                                "只想看不想存用 show_figure(fig)。"))
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet("color: gray;")
        self._layout.addWidget(self._empty)

    def clear(self):
        self._images = []
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None and w is not self._empty:
                w.deleteLater()
        self._layout.addWidget(self._empty)
        self._empty.show()

    def set_images(self, images):
        self.clear()
        self._images = list(images)
        if not images:
            return
        self._empty.hide()
        for title, png in images:
            self._layout.addWidget(self._make_card(title, png))

    def count(self):
        return len(self._images)

    def _make_card(self, title, png):
        card = QWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(4, 4, 4, 12)
        head = QHBoxLayout()
        head.addWidget(QLabel(f"<b>{title}</b>"))
        head.addStretch(1)
        copy_btn = QPushButton(tr("复制"))
        copy_btn.clicked.connect(lambda: self._copy(png))
        head.addWidget(copy_btn)
        save_btn = QPushButton(tr("保存图片…"))
        save_btn.clicked.connect(lambda: self._save(title, png))
        head.addWidget(save_btn)
        lay.addLayout(head)
        pix = QPixmap()
        pix.loadFromData(png, "PNG")
        img = QLabel()
        img.setPixmap(pix)
        img.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(img)
        return card

    def _copy(self, png):
        pix = QPixmap()
        pix.loadFromData(png, "PNG")
        QApplication.clipboard().setPixmap(pix)

    def _save(self, title, png):
        name = title if title.lower().endswith(".png") else f"{title}.png"
        path, _ = QFileDialog.getSaveFileName(self, tr("保存图片"), name, "PNG (*.png)")
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(png)
        except OSError as e:
            QMessageBox.critical(self, tr("错误"), tr("保存图片失败:\n{}").format(e))


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class PythonAnalysisWindow(QMainWindow):
    """Python 数据分析窗口（非模态）。host 为主窗口，提供 model.df 等接口。"""

    TAB_OUTPUT, TAB_RESULT, TAB_FIGURES = 0, 1, 2

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self._worker = None
        self._result_dfs = {}
        self._clean_code = ""      # 最近一次"应用预设/表单改参数"后的代码，用来判断编辑器是否被手改
        self._sheet_fetcher = None  # 工作线程读其他 sheet 时的主线程代办（懒建，见 _MainThreadSheetFetcher）
        # 点了「停止」但用户代码卡在一次 C 层调用里（settrace 插不进去）：
        # 超过这个时间还没停下来就提示用户只能等
        self._stop_pending_timer = QTimer(self)
        self._stop_pending_timer.setSingleShot(True)
        self._stop_pending_timer.setInterval(3000)
        self._stop_pending_timer.timeout.connect(self._on_stop_pending)

        self.setWindowTitle(tr("Python 数据分析"))
        self.resize(1100, 760)

        self.presets = self._load_presets()

        self._build_ui()
        self._update_preset_combo()
        self.refresh_columns()
        self._history = self._load_history()
        self._restore_last_code()

    # ---------------- UI ----------------

    def _build_ui(self):
        toolbar = QToolBar(tr("工具栏"))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addWidget(QLabel(tr(" 预设: ")))
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(200)
        self.preset_combo.setPlaceholderText(tr("选择预设…"))
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

        # 插入列名：菜单列出当前表的列，点一下就插到光标处
        self.insert_col_btn = QToolButton()
        self.insert_col_btn.setText(tr("插入列名") + " ▾")
        self.insert_col_btn.setStyleSheet(_MENU_BTN_STYLE)
        self.insert_col_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._col_menu = QMenu(self.insert_col_btn)
        self._col_menu.aboutToShow.connect(self._fill_column_menu)
        self.insert_col_btn.setMenu(self._col_menu)
        toolbar.addWidget(self.insert_col_btn)

        # 历史：最近运行过的代码
        self.history_btn = QToolButton()
        self.history_btn.setText(tr("历史") + " ▾")
        self.history_btn.setStyleSheet(_MENU_BTN_STYLE)
        self.history_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._history_menu = QMenu(self.history_btn)
        self._history_menu.aboutToShow.connect(self._fill_history_menu)
        self.history_btn.setMenu(self._history_menu)
        toolbar.addWidget(self.history_btn)

        # AI：自然语言生成代码 / 修复报错 / 设置
        self.ai_btn = QToolButton()
        self.ai_btn.setText(tr("AI") + " ▾")
        self.ai_btn.setStyleSheet(_MENU_BTN_STYLE)
        self.ai_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        ai_menu = QMenu(self.ai_btn)
        ai_menu.addAction(tr("生成代码…"), lambda: self._ai_generate(fix=False))
        self.ai_fix_action = ai_menu.addAction(tr("修复上次报错…"), lambda: self._ai_generate(fix=True))
        self.ai_fix_action.setEnabled(False)
        ai_menu.addSeparator()
        ai_menu.addAction(tr("AI 设置…"), self._ai_settings)
        self.ai_btn.setMenu(ai_menu)
        toolbar.addWidget(self.ai_btn)

        toolbar.addSeparator()

        self.run_action = QAction(tr("▶ 运行"), self)
        # F5 与 Cmd/Ctrl+Enter 都能运行（后者是笔记本用户的肌肉记忆）
        self.run_action.setShortcuts([
            QKeySequence(Qt.Key.Key_F5), QKeySequence("Ctrl+Return")])
        self.run_action.setToolTip(tr("运行代码（F5 或 Ctrl+Enter）"))
        self.run_action.triggered.connect(self.run_code)
        toolbar.addAction(self.run_action)

        self.stop_action = QAction(tr("■ 停止"), self)
        self.stop_action.setToolTip(tr("中断正在运行的代码"))
        self.stop_action.setEnabled(False)
        self.stop_action.triggered.connect(self.stop_code)
        toolbar.addAction(self.stop_action)

        clear_action = QAction(tr("清空输出"), self)
        clear_action.triggered.connect(self._clear_outputs)
        toolbar.addAction(clear_action)

        # 主区域：上 = 参数表单 + 代码编辑器；下 = 输出 / 结果表 / 图表 三个标签页
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)

        mono = QFont("Menlo", 13)
        mono.setStyleHint(QFont.StyleHint.Monospace)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter, 1)

        top = QWidget()
        top_lay = QVBoxLayout(top)
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(4)

        self.param_panel = ParamPanel()
        self.param_panel.value_changed.connect(self._on_param_changed)
        top_lay.addWidget(self.param_panel)

        self.code_edit = CodeEditor()
        self.code_edit.setFont(mono)
        self.code_edit.setPlaceholderText(tr(
            "# 可用变量: df (当前数据副本), df_full (筛选前全表), selection (当前选区),\n"
            "#          sheets['名字'] (其他 Sheet), sheet_names, selected_columns, pd, np\n"
            "# 可用函数: save_as_sheet(df, '名称'), save_figure(fig, '文件名.png'), show_figure(fig)\n"
            "# 在引号里打字可补全列名（Ctrl+Space 强制弹出）"
        ))
        self._highlighter = PythonHighlighter(self.code_edit.document())
        # 手改代码后延迟刷新表单（避免每敲一个字就重解析）
        self._param_sync_timer = QTimer(self)
        self._param_sync_timer.setSingleShot(True)
        self._param_sync_timer.setInterval(250)
        self._param_sync_timer.timeout.connect(self._sync_params_from_code)
        self.code_edit.textChanged.connect(self._param_sync_timer.start)
        top_lay.addWidget(self.code_edit, 1)
        splitter.addWidget(top)

        self.tabs = QTabWidget()
        splitter.addWidget(self.tabs)

        # 输出
        self.output_edit = QPlainTextEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setFont(mono)
        self.output_edit.setPlaceholderText(tr(
            "运行结果显示在这里（print 输出与错误信息）。\n"
            "运行后代码里的 DataFrame 变量会出现在「结果表」页，图表出现在「图表」页。"))
        self.tabs.addTab(self.output_edit, tr("输出"))

        # 结果表
        result_page = QWidget()
        result_lay = QVBoxLayout(result_page)
        result_lay.setContentsMargins(0, 4, 0, 0)
        result_row = QHBoxLayout()
        result_row.addWidget(QLabel(tr("结果 DataFrame:")))
        self.result_combo = QComboBox()
        self.result_combo.setMinimumWidth(200)
        self.result_combo.currentIndexChanged.connect(self._show_selected_result)
        result_row.addWidget(self.result_combo)
        save_sheet_btn = QPushButton(tr("保存为Sheet"))
        save_sheet_btn.clicked.connect(self._save_result_as_sheet)
        result_row.addWidget(save_sheet_btn)
        replace_btn = QPushButton(tr("替换当前Sheet"))
        replace_btn.setToolTip(tr("用这个结果整表替换当前 Sheet（会先确认，可撤销）"))
        replace_btn.clicked.connect(self._replace_current_sheet)
        result_row.addWidget(replace_btn)
        append_btn = QPushButton(tr("追加为新列"))
        append_btn.setToolTip(tr("把结果的各列按行位置追加到当前 Sheet 末尾（会先确认，可撤销）"))
        append_btn.clicked.connect(self._append_result_columns)
        result_row.addWidget(append_btn)
        result_row.addStretch(1)
        result_lay.addLayout(result_row)
        self.result_view = QTableView()
        self.result_view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.result_view.setAlternatingRowColors(True)
        result_lay.addWidget(self.result_view, 1)
        self.tabs.addTab(result_page, tr("结果表"))

        # 图表
        self.figure_gallery = FigureGallery()
        self.tabs.addTab(self.figure_gallery, tr("图表"))

        splitter.setSizes([440, 300])

    # ---------------- 列名（补全 / 表单 / 插入菜单） ----------------

    def host_columns(self):
        df = getattr(getattr(self.host, "model", None), "df", None)
        if isinstance(df, pd.DataFrame):
            return [str(c) for c in df.columns]
        return []

    def refresh_columns(self):
        cols = self.host_columns()
        self.code_edit.set_completion_words(cols)
        self.param_panel.set_columns(cols)

    def _fill_column_menu(self):
        self._col_menu.clear()
        cols = self.host_columns()
        if not cols:
            self._col_menu.addAction(tr("（当前没有数据）")).setEnabled(False)
            return
        limit = 60
        for c in cols[:limit]:
            self._col_menu.addAction(c, lambda name=c: self.code_edit.insert_column_name(name))
        if len(cols) > limit:
            self._col_menu.addAction(
                tr("…还有 {} 列，请在引号里打字补全").format(len(cols) - limit)).setEnabled(False)

    def changeEvent(self, event):
        # 切回本窗口时刷新列名：用户可能在主窗口改了表头
        if event.type() == event.Type.ActivationChange and self.isActiveWindow():
            self.refresh_columns()
        super().changeEvent(event)

    # ---------------- 参数表单 <-> 代码 ----------------

    def _sync_params_from_code(self):
        self.param_panel.load(self.code_edit.toPlainText())

    def _on_param_changed(self, name, value):
        code = self.code_edit.toPlainText()
        new_code = analysis_params.set_param(code, name, value)
        if new_code == code:
            return
        # 只替换那一行，编辑器的光标和撤销栈不受影响
        for p in analysis_params.parse_params(code):
            if p.name == name:
                self.code_edit.replace_line(p.line_no, new_code.split("\n")[p.line_no])
                break
        self._clean_code = self.code_edit.toPlainText()

    def _clear_outputs(self):
        self.output_edit.clear()
        self.result_combo.clear()
        self.result_view.setModel(None)
        self._result_dfs = {}
        self.figure_gallery.clear()

    # ---------------- 预设 ----------------

    # 预设存储格式 4：默认预设住在代码里（应用升级模板自动更新），
    # 用户文件只存自定义/改动过的预设与被删除的默认预设名
    _FORMAT_KEY = "__format__"
    _DELETED_KEY = "__deleted_defaults__"
    PRESETS_FORMAT = 4

    def _load_presets(self):
        self._user_presets = {}
        self._deleted_defaults = set()
        try:
            if os.path.exists(PRESETS_FILE):
                with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if data.get(self._FORMAT_KEY) == self.PRESETS_FORMAT:
                        self._deleted_defaults = set(
                            data.get(self._DELETED_KEY, []))
                        self._user_presets = {
                            k: v for k, v in data.items()
                            if not str(k).startswith("__")}
                    else:
                        self._migrate_legacy_presets(data)
        except Exception as e:
            print(f"加载预设失败: {e}")
        return self._compose_presets()

    def _compose_presets(self):
        """显示用预设表 = 内置默认（去掉用户删除的）+ 用户预设（同名覆盖）。"""
        presets = {k: v for k, v in DEFAULT_PRESETS.items()
                   if k not in self._deleted_defaults}
        presets.update(self._user_presets)
        return presets

    def _migrate_legacy_presets(self, data):
        """旧格式（全量快照）一次性迁移。

        与当前或历史默认文本完全一致的条目视为未改动（改用内置最新版），
        其余保留为用户预设；按批次版本推断用户删除过的默认预设。
        用户改过的内容绝不丢弃。
        """
        version = data.pop(_DEFAULTS_VERSION_KEY, 1)
        for key, code in data.items():
            if str(key).startswith("__"):
                continue
            if key in DEFAULT_PRESETS and (
                    code == DEFAULT_PRESETS[key]
                    or code in _SUPERSEDED_DEFAULT_TEXTS.get(key, ())):
                continue
            self._user_presets[key] = code
        for v, keys in DEFAULT_PRESET_VERSIONS.items():
            if v <= version:
                for k in keys:
                    if k not in data:
                        self._deleted_defaults.add(k)
        try:
            self._write_presets_file()
        except OSError:
            pass

    def _write_presets_file(self):
        os.makedirs(os.path.dirname(PRESETS_FILE), exist_ok=True)
        payload = {self._FORMAT_KEY: self.PRESETS_FORMAT,
                   self._DELETED_KEY: sorted(self._deleted_defaults),
                   **self._user_presets}
        with open(PRESETS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _save_presets(self):
        try:
            self._write_presets_file()
        except Exception as e:
            QMessageBox.critical(self, tr("错误"), tr("保存预设失败:\n{}").format(e))

    def _update_preset_combo(self, select_name=None):
        """按类别分组填充下拉框：分组标题为不可选的灰项。"""
        combo = self.preset_combo
        combo.blockSignals(True)
        combo.clear()

        def header(text):
            combo.addItem(f"── {text} ──")
            item = combo.model().item(combo.count() - 1)
            item.setEnabled(False)

        categorized = set()
        for cat, names in PRESET_CATEGORIES:
            present = [n for n in names if n in self.presets]
            if not present:
                continue
            header(tr(cat))
            for n in present:
                combo.addItem(n)
            categorized.update(present)
        others = [n for n in self.presets if n not in categorized]
        defaults_left = [n for n in others if n in DEFAULT_PRESETS]
        user_only = [n for n in others if n not in DEFAULT_PRESETS]
        if defaults_left:
            header(tr("其他"))
            combo.addItems(defaults_left)
        if user_only:
            header(tr("我的预设"))
            combo.addItems(user_only)

        if select_name and select_name in self.presets:
            combo.setCurrentText(select_name)
        else:
            combo.setCurrentIndex(-1)
        combo.blockSignals(False)

    def _editor_is_clean(self):
        """编辑器为空、或与最近一次应用预设/表单改参数后的内容一致、或等于某个预设原文。"""
        current = self.code_edit.toPlainText().strip()
        if not current or current == self._clean_code.strip():
            return True
        return current in {c.strip() for c in self.presets.values()}

    def _on_preset_selected(self, name):
        """下拉选中即自动应用；编辑器有自定义未保存内容时不覆盖。"""
        if not name or name not in self.presets:
            return
        if not self._editor_is_clean():
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
        self._clean_code = self.code_edit.toPlainText()
        self.refresh_columns()
        self._param_sync_timer.stop()
        self._sync_params_from_code()
        # 列名参数还是占位符时，把焦点放到第一个参数上，提示用户先选列
        for p in self.param_panel.params():
            if p.kind == KIND_COLUMN and str(p.value) not in self.host_columns():
                w = self.param_panel.widget_for(p.name)
                if w is not None:
                    w.setFocus()
                break

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
        self._user_presets[name] = code
        self.presets = self._compose_presets()
        self._save_presets()
        self._update_preset_combo(select_name=name)

    def _delete_preset(self):
        name = self.preset_combo.currentText()
        if not name or name not in self.presets:
            return
        ans = QMessageBox.question(self, tr("确认"), tr("确定删除预设 “{}”?").format(name))
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._user_presets.pop(name, None)
        if name in DEFAULT_PRESETS:
            self._deleted_defaults.add(name)   # 删除的默认预设不再复活
        self.presets = self._compose_presets()
        self._save_presets()
        self._update_preset_combo()

    # ---------------- 运行 ----------------

    def run_code(self):
        if self._worker is not None and self._worker.isRunning():
            # 运行按钮已禁用，但 F5 / Ctrl+Enter 仍能触发：别静默吞掉
            self._status(tr("代码正在运行，请等待完成或点击「停止」"))
            return
        # 表单与代码先对齐（延迟同步的定时器可能还没到点）
        self._param_sync_timer.stop()
        self._sync_params_from_code()
        code = self.code_edit.toPlainText().strip()
        if not code:
            QMessageBox.warning(self, tr("提示"), tr("请输入要运行的代码"))
            return

        # 上一轮的结果表（含那一轮的 df 副本）先放掉，再复制这一轮的数据
        self._result_dfs = {}
        self._show_selected_result()      # 卸下旧结果模型（它还引用着上一轮的 df）
        df = getattr(self.host.model, "df", None)
        df = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
        self.refresh_columns()

        self.output_edit.clear()
        self.output_edit.setPlainText(tr("正在运行...\n"))
        self.tabs.setCurrentIndex(self.TAB_OUTPUT)
        self.run_action.setEnabled(False)
        self.stop_action.setEnabled(True)
        self._run_started = time.perf_counter()
        self._run_code_text = code

        worker = CodeRunWorker(code, df, getattr(self.host, "current_file", None),
                               self, extra=self._extra_namespace(df))
        worker.done.connect(self._on_run_done)
        # 线程真正退出后再销毁：done 发出时 run() 可能还没返回，
        # 在 done 的槽里 deleteLater 有机会销毁一个仍在跑的 QThread
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _extra_namespace(self, df_copy=None):
        """除 df 之外注入用户代码的变量：

        - sheets['名字']：任意 sheet 的完整数据（按需读取，读取在主线程完成）；
          sheet_names 为名字列表
        - df_full：当前 sheet 筛选前的全表；没在筛选时就是 df 本身（同一个副本，
          不再多复制一份整表）
        - selection：当前选区的数据块（没选就是 None）；selected_columns：选中列名
        """
        host = self.host
        extra = {}
        names = list(getattr(host, "sheet_names", None) or [])
        extra["sheet_names"] = names
        if getattr(host, "get_sheet_df", None) is not None:
            if self._sheet_fetcher is None:
                self._sheet_fetcher = _MainThreadSheetFetcher(host, self)
            extra["sheets"] = _SheetAccessor(host, names, self._sheet_fetcher)
        else:
            extra["sheets"] = _SheetAccessor(host, names)
        if getattr(host, "original_df", None) is not None:
            extra["df_full"] = host.original_df.copy()
        elif df_copy is not None:
            extra["df_full"] = df_copy
        elif isinstance(getattr(getattr(host, "model", None), "df", None), pd.DataFrame):
            extra["df_full"] = host.model.df.copy()
        else:
            extra["df_full"] = pd.DataFrame()
        sel, sel_cols = None, []
        if hasattr(host, "selection_frame"):
            try:
                sel, sel_cols = host.selection_frame()
            except Exception:
                sel, sel_cols = None, []
        extra["selection"] = sel
        extra["selected_columns"] = list(sel_cols)
        return extra

    def stop_code(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._status(tr("正在停止..."))
            self._stop_pending_timer.start()

    def _on_stop_pending(self):
        # settrace 只能在用户代码的下一行插入中断；一次长时间的 C 层调用
        # （大表 merge、读文件、time.sleep）中间停不下来，只能等它返回
        if self._worker is not None and self._worker.isRunning() \
                and self._worker.cancel_requested():
            self._status(tr("当前操作无法中断，请等待完成"))

    def _on_run_done(self, output, result_dfs, sheet_requests, figure_files, figure_images):
        self._stop_pending_timer.stop()
        self.run_action.setEnabled(True)
        self.stop_action.setEnabled(False)
        had_error = tr("[执行错误]") in (output or "")
        code = getattr(self, "_run_code_text", "")
        self._record_run_history(code, output, had_error)
        preferred = self._update_run_ui(output, result_dfs, figure_files, figure_images)
        self._apply_sheet_requests(sheet_requests)

        # 自动切到最有用的页：报错看输出，有图看图，有结果看表
        if had_error:
            self.tabs.setCurrentIndex(self.TAB_OUTPUT)
        elif figure_images:
            self.tabs.setCurrentIndex(self.TAB_FIGURES)
        elif preferred is not None:
            self.tabs.setCurrentIndex(self.TAB_RESULT)
        else:
            self.tabs.setCurrentIndex(self.TAB_OUTPUT)

        elapsed = time.perf_counter() - getattr(self, "_run_started", time.perf_counter())
        if sheet_requests:
            names = ", ".join(n for _, n in sheet_requests)
            self._status(tr("已保存 {} 个Sheet: {}（耗时 {:.2f} 秒）").format(
                len(sheet_requests), names, elapsed))
        else:
            self._status(tr("代码执行完成（耗时 {:.2f} 秒）").format(elapsed))
        # 线程对象由 finished→deleteLater 自行销毁（见 run_code），这里只放开引用
        self._worker = None

    def _record_run_history(self, code, output, had_error):
        """运行历史 + 给「AI 修复上次报错」记住出错的代码和 traceback。"""
        self._record_history(code, ok=not had_error)
        self._last_error = (code, output) if had_error else None
        self.ai_fix_action.setEnabled(had_error)

    def _update_run_ui(self, output, result_dfs, figure_files, figure_images):
        """输出页、结果表下拉框/表格、图表页。返回默认选中的结果名（可能为 None）。"""
        if figure_files:
            output = (output or "") + "\n" + tr("已保存图表:") + "\n" + "\n".join(
                "  " + f for f in figure_files) + "\n"
        self.output_edit.setPlainText(output if output else tr("✓ 代码执行完成（无输出）\n"))
        sb = self.output_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

        self._result_dfs = result_dfs
        preferred = self._preferred_result(result_dfs)
        self.result_combo.blockSignals(True)
        self.result_combo.clear()
        for name, rdf in result_dfs.items():
            self.result_combo.addItem(
                tr("{} ({}行×{}列)").format(name, len(rdf), len(rdf.columns)), name)
        if preferred is not None:
            self.result_combo.setCurrentIndex(self.result_combo.findData(preferred))
        self.result_combo.blockSignals(False)
        self._show_selected_result()
        self.tabs.setTabText(self.TAB_RESULT, tr("结果表") + (
            f" ({len(result_dfs)})" if result_dfs else ""))

        self.figure_gallery.set_images(figure_images)
        self.tabs.setTabText(self.TAB_FIGURES, tr("图表") + (
            f" ({len(figure_images)})" if figure_images else ""))
        return preferred

    def _apply_sheet_requests(self, sheet_requests):
        """save_as_sheet 队列在主线程统一执行（线程安全）。

        与「保存为Sheet」按钮走同一套整理：groupby/透视表的行标签展开成列，
        多级列名压平——否则 groupby(...).sum().to_frame() 存出来会丢掉分组列。
        """
        for rdf, sheet_name in sheet_requests:
            try:
                self.host.add_sheet_from_df(flatten_frame(rdf), sheet_name)
            except Exception as e:
                self.output_edit.appendPlainText(
                    tr("[保存Sheet失败] {}: {}").format(sheet_name, e))

    # 超过这么多单元格就不再逐值比较 df 是否被改过（整表 .equals 在大表上要好几秒）
    _EQUALS_MAX_CELLS = 2_000_000

    def _preferred_result(self, result_dfs):
        """默认展示哪个结果：最后一个非 df 的 DataFrame；没有就看 df 是否被改过。

        先比形状和列名（几乎免费），都一样才做逐值比较；大表跳过逐值比较，
        形状列名一致就当没改——用户仍可从下拉框手动选 df 查看。
        """
        names = [n for n in result_dfs if n != "df"]
        if names:
            return names[-1]
        if "df" not in result_dfs:
            return None
        host_df = getattr(getattr(self.host, "model", None), "df", None)
        if not isinstance(host_df, pd.DataFrame):
            return "df"
        rdf = result_dfs["df"]
        try:
            same = rdf.shape == host_df.shape and rdf.columns.equals(host_df.columns)
            if same and rdf.size <= self._EQUALS_MAX_CELLS:
                same = rdf.equals(host_df)
        except Exception:
            same = False
        return None if same else "df"

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

    def _show_selected_result(self):
        name = self.result_combo.currentData()
        rdf = self._result_dfs.get(name) if name else None
        old = self.result_view.model()
        if rdf is None:
            self.result_view.setModel(None)
        else:
            self.result_view.setModel(PandasTableModel(flatten_frame(rdf)))
        if old is not None:
            old.deleteLater()

    def _save_result_as_sheet(self):
        name, rdf = self._current_result_df()
        if rdf is None:
            return
        default = f"{name}_{datetime.now().strftime('%H%M%S')}" if name == "df" else name
        sheet_name, ok = QInputDialog.getText(self, tr("保存为Sheet"), tr("Sheet 名称:"), text=default)
        if not ok or not sheet_name.strip():
            return
        try:
            self.host.add_sheet_from_df(flatten_frame(rdf), sheet_name.strip())
            self._status(tr("已保存Sheet: {}").format(sheet_name.strip()))
        except Exception as e:
            QMessageBox.critical(self, tr("错误"), tr("保存Sheet失败:\n{}").format(e))

    # ---------------- 结果回写当前 Sheet（都先弹窗确认，都可撤销） ----------------

    def _replace_current_sheet(self):
        name, rdf = self._current_result_df()
        if rdf is None:
            return
        if not hasattr(self.host, "replace_current_sheet_df"):
            return
        flat = flatten_frame(rdf)
        cur = getattr(getattr(self.host, "model", None), "df", None)
        cur_desc = tr("{} 行 × {} 列").format(len(cur), len(cur.columns)) \
            if isinstance(cur, pd.DataFrame) else "?"
        ret = QMessageBox.question(
            self, tr("替换当前Sheet"),
            tr("用结果「{}」（{} 行 × {} 列）整表替换当前 Sheet（现有 {}）？\n\n"
               "当前 Sheet 的公式和单元格颜色会一并清除。此操作可用撤销（Ctrl+Z）恢复。")
            .format(name, len(flat), len(flat.columns), cur_desc),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            self.host.replace_current_sheet_df(flat)
        except Exception as e:
            QMessageBox.warning(self, tr("替换当前Sheet"), str(e))

    def _append_result_columns(self):
        name, rdf = self._current_result_df()
        if rdf is None:
            return
        if not hasattr(self.host, "append_columns_to_current"):
            return
        cur = getattr(getattr(self.host, "model", None), "df", None)
        if isinstance(cur, pd.DataFrame):
            flat, why = align_result_rows(rdf, cur.index)
            if why == "rows":
                QMessageBox.warning(
                    self, tr("追加为新列"),
                    tr("结果有 {} 行，当前 Sheet 有 {} 行，行数不一致无法按位置追加。\n"
                       "提示：用 df.merge(...) 或 df.assign(...) 把结果对齐到 df 后再追加。")
                    .format(len(rdf), len(cur)))
                return
            if why == "index":
                QMessageBox.warning(
                    self, tr("追加为新列"),
                    tr("结果的行索引与当前 Sheet 的行对不上，无法按行对齐追加。\n"
                       "结果若是从 df 筛选/排序得来的可以直接追加；分组汇总等结果请先"
                       "用 df.merge(...) 对齐到 df，或 reset_index() 后再追加。"))
                return
        else:
            flat = flatten_frame(rdf)
        cols = [str(c) for c in flat.columns]
        ret = QMessageBox.question(
            self, tr("追加为新列"),
            tr("把结果「{}」的 {} 列追加到当前 Sheet 末尾？\n{}\n\n"
               "按行位置对齐（第 1 行对第 1 行）。此操作可用撤销（Ctrl+Z）恢复。")
            .format(name, len(cols), ", ".join(cols[:8]) + ("..." if len(cols) > 8 else "")),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            self.host.append_columns_to_current(flat)
        except Exception as e:
            QMessageBox.warning(self, tr("追加为新列"), str(e))

    # ---------------- 代码持久化与运行历史 ----------------

    def _restore_last_code(self):
        if self.code_edit.toPlainText().strip():
            return
        try:
            if os.path.exists(LAST_CODE_FILE):
                with open(LAST_CODE_FILE, "r", encoding="utf-8") as f:
                    code = f.read()
                if code.strip():
                    self.code_edit.setPlainText(code)
                    self._clean_code = code
                    self._param_sync_timer.stop()
                    self._sync_params_from_code()
        except OSError:
            pass

    def _save_last_code(self):
        try:
            os.makedirs(_CONFIG_DIR, exist_ok=True)
            with open(LAST_CODE_FILE, "w", encoding="utf-8") as f:
                f.write(self.code_edit.toPlainText())
        except OSError:
            pass

    def _load_history(self):
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return [d for d in data if isinstance(d, dict) and d.get("code")]
        except (OSError, ValueError):
            pass
        return []

    def _record_history(self, code, ok=True):
        code = (code or "").strip()
        if not code:
            return
        # 同样的代码只留最新一条
        self._history = [h for h in self._history if h.get("code") != code]
        self._history.insert(0, {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "code": code, "ok": bool(ok)})
        del self._history[HISTORY_LIMIT:]
        try:
            os.makedirs(_CONFIG_DIR, exist_ok=True)
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self._history, f, ensure_ascii=False, indent=1)
        except OSError:
            pass
        self._save_last_code()

    @staticmethod
    def _history_label(entry):
        first = ""
        for line in entry.get("code", "").split("\n"):
            s = line.strip()
            if s and not s.startswith("#"):
                first = s
                break
        if len(first) > 48:
            first = first[:48] + "…"
        mark = "" if entry.get("ok", True) else " ✗"
        return f"{entry.get('time', '')}{mark}   {first}"

    def _fill_history_menu(self):
        self._history_menu.clear()
        if not self._history:
            self._history_menu.addAction(tr("（还没有运行记录）")).setEnabled(False)
            return
        for entry in self._history:
            act = self._history_menu.addAction(self._history_label(entry))
            act.setToolTip(entry.get("code", ""))
            act.triggered.connect(lambda _=False, e=entry: self._load_history_entry(e))
        self._history_menu.addSeparator()
        self._history_menu.addAction(tr("清空历史"), self._clear_history)

    def _load_history_entry(self, entry):
        code = entry.get("code", "")
        if not self._editor_is_clean() and self.code_edit.toPlainText().strip() != code.strip():
            ret = QMessageBox.question(
                self, tr("历史"), tr("编辑器里有未保存的改动，用这条历史记录覆盖？"))
            if ret != QMessageBox.StandardButton.Yes:
                return
        self.code_edit.setPlainText(code)
        self._clean_code = code
        self._param_sync_timer.stop()
        self._sync_params_from_code()

    def _clear_history(self):
        self._history = []
        try:
            if os.path.exists(HISTORY_FILE):
                os.remove(HISTORY_FILE)
        except OSError:
            pass

    # ---------------- AI 生成代码 ----------------

    def _ai_settings(self):
        from .ai_codegen import AiSettingsDialog
        AiSettingsDialog(self).exec()

    def _ai_generate(self, fix=False):
        from .ai_codegen import AiGenerateDialog
        df = getattr(getattr(self.host, "model", None), "df", None)
        if not isinstance(df, pd.DataFrame):
            df = pd.DataFrame()
        current = self.code_edit.toPlainText()
        error_text = ""
        if fix and getattr(self, "_last_error", None):
            current, error_text = self._last_error
        dlg = AiGenerateDialog(df, current_code=current, error_text=error_text,
                               sheet_names=list(getattr(self.host, "sheet_names", None) or []),
                               parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.code:
            return
        self._apply_generated_code(dlg.code, modify=dlg.modify_cb.isChecked())

    def _apply_generated_code(self, code, modify=False):
        """把生成的代码放进编辑器；会覆盖手改内容时先确认。"""
        if not modify and not self._editor_is_clean():
            ret = QMessageBox.question(
                self, tr("AI 生成代码"), tr("编辑器里有未保存的改动，用生成的代码覆盖？"))
            if ret != QMessageBox.StandardButton.Yes:
                return
        self.code_edit.setPlainText(code)
        self._clean_code = code
        self._param_sync_timer.stop()
        self._sync_params_from_code()
        self._status(tr("已插入 AI 生成的代码，检查参数后按 F5 运行"))

    # ---------------- 关闭 ----------------

    def closeEvent(self, event):
        """用户点关闭、或主窗口退出时调用 self._analysis_win.close() 都走这里。

        - 没有代码在跑：保存编辑器内容到 LAST_CODE_FILE，接受事件（close() 返回 True）。
        - 有代码在跑：先请求停止并最多等 3 秒；停下来了同上；仍停不下来
          （卡在 C 层调用里）则弹一次提示、event.ignore()，窗口不关（close() 返回
          False）——挂在主窗口下的 QThread 若在运行中被销毁会直接崩溃整个程序。
        """
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
            if self._worker.isRunning():
                QMessageBox.warning(
                    self, tr("提示"),
                    tr("代码仍在运行，无法关闭窗口。\n请等待其结束，或点击「停止」后再试。"))
                event.ignore()
                return
        self._stop_pending_timer.stop()
        self._save_last_code()
        super().closeEvent(event)
