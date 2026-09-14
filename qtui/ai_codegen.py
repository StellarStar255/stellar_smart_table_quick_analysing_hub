# -*- coding: utf-8 -*-
"""Python 分析窗口的「AI 生成代码」：用自然语言描述需求，由 Claude 生成 pandas 代码。

- 提示词只带表的结构（列名、类型、少量样例值），不上传整张表
- 调用在后台线程，结果回到编辑器；API Key / 模型存在 QSettings（明文，本机）
- 没配 Key 时走 anthropic SDK 的默认凭据（ANTHROPIC_API_KEY 环境变量或 ant auth login）
"""

import re

import pandas as pd
from PyQt6.QtCore import QThread, pyqtSignal, QSettings, Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QPlainTextEdit, QCheckBox, QPushButton, QDialogButtonBox,
    QMessageBox,
)

from qtui.i18n import tr

DEFAULT_MODEL = "claude-opus-5"
MODEL_CHOICES = ["claude-opus-5", "claude-sonnet-5", "claude-fable-5-1", "claude-haiku-4-5"]
MAX_TOKENS = 8000
SAMPLE_VALUES = 5
SAMPLE_ROWS = 200        # 取样例值时只看前这么多行，大表也很快
MAX_COLUMNS_IN_PROMPT = 80


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------

def _settings():
    return QSettings("SmartTableHub", "SmartTableHubQt")


def load_settings():
    s = _settings()
    return {
        "api_key": (s.value("ai/api_key") or "").strip(),
        "model": (s.value("ai/model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "base_url": (s.value("ai/base_url") or "").strip(),
    }


def save_settings(values):
    s = _settings()
    s.setValue("ai/api_key", values.get("api_key", ""))
    s.setValue("ai/model", values.get("model", DEFAULT_MODEL))
    s.setValue("ai/base_url", values.get("base_url", ""))


# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是一个嵌在表格工具里的 pandas 数据分析助手。用户在「Python 数据分析」窗口里用自然语言描述需求，你生成能直接运行的 Python 代码。

运行环境（已注入，不要再 import pandas/numpy）：
- df：当前表的 pandas DataFrame 副本（第一行数据是第 1 行，列名就是表头）
- df_full：筛选前的全表；selection：用户当前选区的数据块（可能为 None）；selected_columns：选中的列名
- sheets['名字']：其他 Sheet 的数据；sheet_names：所有 Sheet 名
- pd、np 已可用
- save_as_sheet(df, '名称')：把结果存为新 Sheet
- save_figure(fig, '文件名.png')：保存并显示图；show_figure(fig)：只在窗口里显示图（画图用 matplotlib，需要 import matplotlib.pyplot as plt）

规则：
1. 只输出一个 ```python 代码块，代码块外不要写解释。
2. 把最终结果赋给变量 result（DataFrame 或 Series），并 print(result)。需要存 Sheet 时在末尾加一行注释 # save_as_sheet(result, '名称')。
3. 需要用户调整的参数放在代码开头的参数块里，格式严格如下（大写常量 = 字面量  # 说明）；列名参数以 _COL 结尾，列名列表以 _COLS 结尾：
   # ===== 参数 =====
   VALUE_COL = '列名'   # 说明
   TOP_N = 10           # 说明
   # ===============
4. 只使用下面列出的真实列名；列名含空格或特殊字符时照原样写在引号里。
5. 不要读写文件、不要访问网络、不要用 input()、不要 exit()。
6. 注释和输出文字使用与用户请求相同的语言。
7. 如果用户给了现有代码或报错，在其基础上修改，保留用户意图。
"""


def _sample_values(series, n=SAMPLE_VALUES):
    vals = series.dropna().head(SAMPLE_ROWS).astype(str).unique()[:n]
    out = []
    for v in vals:
        v = v.replace("\n", " ")
        out.append(v[:40] + "…" if len(v) > 40 else v)
    return out


def describe_frame(df):
    """给模型看的表结构摘要（不含整表数据）。"""
    if not isinstance(df, pd.DataFrame) or df.empty and len(df.columns) == 0:
        return tr("（当前没有数据）")
    lines = [tr("表：{} 行 × {} 列").format(len(df), len(df.columns)), tr("列（名称 | 类型 | 非空数 | 样例值）:")]
    cols = list(df.columns)
    for c in cols[:MAX_COLUMNS_IN_PROMPT]:
        s = df[c]
        samples = ", ".join(repr(v) for v in _sample_values(s))
        lines.append(f"- {c!r} | {s.dtype} | {int(s.notna().sum())} | {samples}")
    if len(cols) > MAX_COLUMNS_IN_PROMPT:
        lines.append(tr("…还有 {} 列未列出").format(len(cols) - MAX_COLUMNS_IN_PROMPT))
    return "\n".join(lines)


def build_user_message(request, df, current_code=None, error_text=None, sheet_names=None):
    parts = [describe_frame(df)]
    if sheet_names:
        parts.append(tr("所有 Sheet: {}").format(", ".join(repr(n) for n in sheet_names)))
    if current_code and current_code.strip():
        parts.append(tr("现有代码:") + "\n```python\n" + current_code.strip() + "\n```")
    if error_text and error_text.strip():
        parts.append(tr("运行报错:") + "\n```\n" + error_text.strip()[-3000:] + "\n```")
    parts.append(tr("需求:") + "\n" + request.strip())
    return "\n\n".join(parts)


_FENCE_RE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_code(text):
    """取回复里的第一个 python 代码块；没有围栏就整段当代码。"""
    m = _FENCE_RE.search(text or "")
    if m:
        return m.group(1).strip("\n")
    return (text or "").strip()


# ---------------------------------------------------------------------------
# 调用
# ---------------------------------------------------------------------------

class CodeGenWorker(QThread):
    finished_ok = pyqtSignal(str)      # 生成的代码
    failed = pyqtSignal(str)           # 错误说明

    def __init__(self, settings, system, user, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._system = system
        self._user = user

    def run(self):
        try:
            text = self._call_api(self._settings, self._system, self._user)
        except Exception as e:
            self.failed.emit(str(e))
            return
        code = extract_code(text)
        if not code.strip():
            self.failed.emit(tr("模型没有返回代码"))
            return
        self.finished_ok.emit(code)

    @staticmethod
    def _call_api(settings, system, user):
        try:
            import anthropic
        except ImportError:
            raise RuntimeError(tr("未安装 anthropic SDK，请先执行: pip install anthropic"))

        kwargs = {}
        if settings.get("api_key"):
            kwargs["api_key"] = settings["api_key"]
        if settings.get("base_url"):
            kwargs["base_url"] = settings["base_url"]
        client = anthropic.Anthropic(**kwargs)
        model = settings.get("model") or DEFAULT_MODEL
        messages = [{"role": "user", "content": user}]

        try:
            if model.startswith(("claude-opus-5", "claude-fable")):
                # 安全分类器拒答时由服务端自动换模型重跑，不用自己维护列表
                try:
                    response = client.beta.messages.create(
                        model=model, max_tokens=MAX_TOKENS,
                        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                        system=system, messages=messages)
                except TypeError:   # 旧版 SDK 不认识 fallbacks 参数
                    response = client.messages.create(
                        model=model, max_tokens=MAX_TOKENS, system=system, messages=messages)
            else:
                response = client.messages.create(
                    model=model, max_tokens=MAX_TOKENS, system=system, messages=messages)
        except anthropic.AuthenticationError:
            raise RuntimeError(tr("API Key 无效或未配置（菜单 AI → 设置…）"))
        except anthropic.NotFoundError:
            raise RuntimeError(tr("模型 {} 不存在或无权访问").format(model))
        except anthropic.RateLimitError:
            raise RuntimeError(tr("请求过于频繁，稍后再试"))
        except anthropic.APIConnectionError as e:
            raise RuntimeError(tr("网络连接失败: {}").format(e))
        except anthropic.APIStatusError as e:
            raise RuntimeError(tr("API 错误 {}: {}").format(e.status_code, e.message))

        if response.stop_reason == "refusal":
            detail = ""
            sd = getattr(response, "stop_details", None)
            if sd is not None and getattr(sd, "explanation", None):
                detail = f"：{sd.explanation}"
            raise RuntimeError(tr("模型拒绝了这个请求{}").format(detail))
        if response.stop_reason == "max_tokens":
            raise RuntimeError(tr("生成的代码太长被截断，请把需求拆小一点"))
        return "".join(b.text for b in response.content if getattr(b, "type", "") == "text")


# ---------------------------------------------------------------------------
# 对话框
# ---------------------------------------------------------------------------

class AiSettingsDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("AI 设置"))
        self.setMinimumWidth(460)
        cur = load_settings()
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.key_edit = QLineEdit(cur["api_key"])
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText(tr("留空则使用环境变量 ANTHROPIC_API_KEY"))
        form.addRow(tr("Anthropic API Key:"), self.key_edit)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(MODEL_CHOICES)
        self.model_combo.setCurrentText(cur["model"])
        form.addRow(tr("模型:"), self.model_combo)
        self.url_edit = QLineEdit(cur["base_url"])
        self.url_edit.setPlaceholderText(tr("可选，默认官方地址"))
        form.addRow(tr("Base URL:"), self.url_edit)
        lay.addLayout(form)
        note = QLabel(tr("Key 明文保存在本机应用设置里，只用于本功能。生成时只发送列名、类型和少量样例值，不上传整张表。"))
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        lay.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def values(self):
        return {"api_key": self.key_edit.text().strip(),
                "model": self.model_combo.currentText().strip() or DEFAULT_MODEL,
                "base_url": self.url_edit.text().strip()}

    def accept(self):
        save_settings(self.values())
        super().accept()


class AiGenerateDialog(QDialog):
    """输入需求 → 后台生成 → 结果放在 self.code。"""

    def __init__(self, df, current_code="", error_text="", sheet_names=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("AI 生成代码"))
        self.resize(560, 360)
        self._df = df
        self._current_code = current_code or ""
        self._error_text = error_text or ""
        self._sheet_names = list(sheet_names or [])
        self._worker = None
        self.code = None

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(tr("用一句话描述你要做的分析（例如：按 预测年龄 分组统计 ground_truth 的人数和占比）：")))
        self.request_edit = QPlainTextEdit()
        self.request_edit.setPlaceholderText(tr("要做什么？"))
        lay.addWidget(self.request_edit, 1)
        self.modify_cb = QCheckBox(tr("在编辑器里的现有代码基础上修改"))
        self.modify_cb.setChecked(bool(self._current_code.strip()) and bool(self._error_text))
        self.modify_cb.setEnabled(bool(self._current_code.strip()))
        lay.addWidget(self.modify_cb)
        if self._error_text:
            self.fix_cb = QCheckBox(tr("把上次运行的报错一起发给模型（修复错误）"))
            self.fix_cb.setChecked(True)
            lay.addWidget(self.fix_cb)
            if not self.request_edit.toPlainText().strip():
                self.request_edit.setPlainText(tr("修复这个报错，保持原来的分析目的。"))
        else:
            self.fix_cb = None
        self.status = QLabel("")
        self.status.setStyleSheet("color: gray;")
        lay.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch(1)
        self.gen_btn = QPushButton(tr("生成"))
        self.gen_btn.setDefault(True)
        self.gen_btn.clicked.connect(self._generate)
        row.addWidget(self.gen_btn)
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _generate(self):
        request = self.request_edit.toPlainText().strip()
        if not request:
            QMessageBox.information(self, tr("提示"), tr("先写一下要做什么"))
            return
        current = self._current_code if self.modify_cb.isChecked() else None
        error = self._error_text if (self.fix_cb is not None and self.fix_cb.isChecked()) else None
        user = build_user_message(request, self._df, current, error, self._sheet_names)
        settings = load_settings()
        self.gen_btn.setEnabled(False)
        self.status.setText(tr("正在生成（{}）…").format(settings["model"]))
        self._worker = CodeGenWorker(settings, SYSTEM_PROMPT, user, self)
        self._worker.finished_ok.connect(self._on_ok)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _on_ok(self, code):
        self.code = code
        self._cleanup()
        self.accept()

    def _on_fail(self, msg):
        self._cleanup()
        self.gen_btn.setEnabled(True)
        self.status.setText("")
        QMessageBox.warning(self, tr("AI 生成代码"), msg)

    def _cleanup(self):
        w, self._worker = self._worker, None
        if w is not None:
            w.deleteLater()

    def reject(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(2000)
        super().reject()

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(2000)
        super().closeEvent(event)
