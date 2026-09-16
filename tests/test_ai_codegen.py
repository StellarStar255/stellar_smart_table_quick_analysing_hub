"""AI 生成代码：提示词只带表结构、代码块提取、后台调用与对话框流程（不联网）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui import ai_codegen
from qtui.ai_codegen import (
    build_user_message, describe_frame, extract_code, CodeGenWorker,
    AiGenerateDialog, SYSTEM_PROMPT,
)


def _df():
    return pd.DataFrame({'ground_truth': ['中年女', '中年男', '中年女'],
                         '预测年龄': [44, 32, 36],
                         'secret note': ['a' * 100, 'b', None]})


class TestPrompt:
    def test_describe_lists_columns_types_and_samples_only(self):
        text = describe_frame(_df())
        assert "3 行 × 3 列" in text or "3 rows × 3 cols" in text
        assert "'ground_truth'" in text and "int64" in text and "'中年女'" in text
        assert "'secret note'" in text
        # 长值截断，整表不进提示词
        assert "a" * 100 not in text and "aaaa…" in text

    def test_user_message_includes_optional_parts(self):
        msg = build_user_message("按 ground_truth 分组计数", _df(),
                                 current_code="x = 1", error_text="KeyError: 'zz'",
                                 sheet_names=['Sheet1', '汇总'])
        assert "x = 1" in msg and "KeyError: 'zz'" in msg
        assert "'Sheet1', '汇总'" in msg
        assert msg.rstrip().endswith("按 ground_truth 分组计数")
        plain = build_user_message("q", _df())
        assert "```" not in plain

    def test_system_prompt_mentions_runtime_contract(self):
        for token in ("save_as_sheet", "show_figure", "# ===== 参数", "_COL", "```python"):
            assert token in SYSTEM_PROMPT


class TestExtractCode:
    def test_fenced_block(self):
        assert extract_code("说明\n```python\nresult = df.head()\n```\n完") == "result = df.head()"
        assert extract_code("```py\nx = 1\n```") == "x = 1"
        assert extract_code("```\nx = 2\n```") == "x = 2"

    def test_no_fence_returns_text(self):
        assert extract_code("  result = df  ") == "result = df"
        assert extract_code("") == ""


class TestWorker:
    def _run(self, monkeypatch, reply=None, error=None):
        def fake(settings, system, user):
            if error:
                raise RuntimeError(error)
            return reply
        monkeypatch.setattr(CodeGenWorker, "_call_api", staticmethod(fake))
        w = CodeGenWorker({"model": "claude-opus-5"}, "sys", "user")
        got = {}
        w.finished_ok.connect(lambda c: got.__setitem__("code", c))
        w.failed.connect(lambda m: got.__setitem__("err", m))
        w.run()
        return got

    def test_success_extracts_code(self, monkeypatch):
        got = self._run(monkeypatch, reply="```python\nresult = df\n```")
        assert got == {"code": "result = df"}

    def test_failure_reported(self, monkeypatch):
        got = self._run(monkeypatch, error="boom")
        assert got == {"err": "boom"}

    def test_empty_reply_is_failure(self, monkeypatch):
        got = self._run(monkeypatch, reply="```python\n\n```")
        assert "err" in got


class TestDialog:
    def test_generate_puts_code_on_dialog(self, monkeypatch):
        captured = {}

        def fake(settings, system, user):
            captured["user"] = user
            return "```python\nresult = df.head(2)\n```"
        monkeypatch.setattr(CodeGenWorker, "_call_api", staticmethod(fake))
        dlg = AiGenerateDialog(_df(), current_code="old = 1", sheet_names=['S'])
        dlg.request_edit.setPlainText("前两行")
        dlg.modify_cb.setChecked(True)
        dlg._generate()
        dlg._worker.wait(5000)
        _app.processEvents()
        assert dlg.code == "result = df.head(2)"
        assert "old = 1" in captured["user"] and "前两行" in captured["user"]

    def test_fix_mode_prefills_request_and_sends_error(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(CodeGenWorker, "_call_api",
                            staticmethod(lambda s, sy, u: captured.setdefault("user", u) and "```python\nok = 1\n```"))
        dlg = AiGenerateDialog(_df(), current_code="bad = df['zz']", error_text="KeyError: 'zz'")
        assert dlg.request_edit.toPlainText().strip()
        assert dlg.modify_cb.isChecked() and dlg.fix_cb.isChecked()
        dlg._generate()
        dlg._worker.wait(5000)
        _app.processEvents()
        assert "KeyError: 'zz'" in captured["user"] and "bad = df['zz']" in captured["user"]
        assert dlg.code == "ok = 1"

    def test_empty_request_does_not_call_api(self, monkeypatch):
        called = []
        monkeypatch.setattr(CodeGenWorker, "_call_api", staticmethod(lambda *a: called.append(1) or "x"))
        monkeypatch.setattr(ai_codegen.QMessageBox, "information", staticmethod(lambda *a, **k: None))
        dlg = AiGenerateDialog(_df())
        dlg._generate()
        assert called == [] and dlg._worker is None


class TestWindowIntegration:
    def test_apply_generated_code_syncs_form_and_marks_clean(self, monkeypatch, tmp_path):
        from types import SimpleNamespace
        from qtui.python_analysis import PythonAnalysisWindow
        host = SimpleNamespace(model=SimpleNamespace(df=_df()), current_file=None,
                               update_statusbar=lambda m: None)
        win = PythonAnalysisWindow(host)
        try:
            code = "# ===== 参数 =====\nGROUP_COL = 'ground_truth'  # 分组\n# =====\nresult = df.groupby(GROUP_COL).size()"
            win._apply_generated_code(code)
            assert win.code_edit.toPlainText() == code
            assert win._editor_is_clean()
            assert [p.name for p in win.param_panel.params()] == ['GROUP_COL']
            assert not win.ai_fix_action.isEnabled()
            win.code_edit.setPlainText("df['nope']")
            win.run_code(); win._worker.run(); _app.processEvents()
            assert win.ai_fix_action.isEnabled() and win._last_error[0] == "df['nope']"
        finally:
            win.close()


# ---------------------------------------------------------------------------
# 新增：Key 存储、客户端配置、生成中取消
# ---------------------------------------------------------------------------

class _FakeKeyring:
    def __init__(self):
        self.store = {}
        self.fail = False

    def get_password(self, service, user):
        if self.fail:
            raise RuntimeError("no backend")
        return self.store.get((service, user))

    def set_password(self, service, user, password):
        if self.fail:
            raise RuntimeError("no backend")
        self.store[(service, user)] = password

    def delete_password(self, service, user):
        if (service, user) not in self.store:
            raise RuntimeError("missing")
        del self.store[(service, user)]


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    """设置走临时 ini 文件，钥匙串用假的：测试不碰真实 QSettings，也不弹系统钥匙串。"""
    from PyQt6.QtCore import QSettings
    path = str(tmp_path / "settings.ini")
    monkeypatch.setattr(ai_codegen, "_settings",
                        lambda: QSettings(path, QSettings.Format.IniFormat))
    fake = _FakeKeyring()
    monkeypatch.setattr(ai_codegen, "keyring", fake)
    return fake


_KR = (ai_codegen._KEYRING_SERVICE, ai_codegen._KEYRING_USER)


class TestKeyStorage:
    def test_key_saved_to_keyring_not_settings(self, isolated_settings):
        ai_codegen.save_settings({"api_key": "sk-1", "model": "m", "base_url": ""})
        assert isolated_settings.store[_KR] == "sk-1"
        assert not ai_codegen._settings().value("ai/api_key")
        assert ai_codegen.load_settings()["api_key"] == "sk-1"

    def test_plaintext_key_migrated_on_first_read(self, isolated_settings):
        s = ai_codegen._settings()
        s.setValue("ai/api_key", "legacy")
        s.sync()
        assert ai_codegen.load_settings()["api_key"] == "legacy"
        assert isolated_settings.store[_KR] == "legacy"
        assert not ai_codegen._settings().value("ai/api_key")     # 明文已抹掉

    def test_falls_back_to_settings_when_keyring_unusable(self, isolated_settings):
        isolated_settings.fail = True
        ai_codegen.save_settings({"api_key": "sk-2", "model": "m", "base_url": ""})
        assert ai_codegen._settings().value("ai/api_key") == "sk-2"
        assert ai_codegen.load_settings()["api_key"] == "sk-2"

    def test_missing_keyring_module_is_fine(self, monkeypatch):
        monkeypatch.setattr(ai_codegen, "keyring", None)
        ai_codegen.save_settings({"api_key": "sk-3", "model": "m", "base_url": ""})
        assert ai_codegen.load_settings()["api_key"] == "sk-3"

    def test_empty_key_removes_keyring_entry(self, isolated_settings):
        ai_codegen.save_settings({"api_key": "sk-1", "model": "m", "base_url": ""})
        ai_codegen.save_settings({"api_key": "", "model": "m", "base_url": ""})
        assert _KR not in isolated_settings.store
        assert ai_codegen.load_settings()["api_key"] == ""


def _install_fake_anthropic(monkeypatch, has_fallbacks=True):
    """假 anthropic 模块：记录客户端参数和走了哪条 create 路径。"""
    import sys
    import types
    log = {"client": None, "path": []}
    response = types.SimpleNamespace(
        stop_reason="end_turn",
        content=[types.SimpleNamespace(type="text", text="```python\nx = 1\n```")])

    class PlainMessages:
        def create(self, **kw):
            log["path"].append("plain")
            return response

    class BetaMessagesWithFallbacks:
        def create(self, *, model, max_tokens, system, messages, betas=None, fallbacks=None):
            log["path"].append("beta")
            return response

    class BetaMessagesOld:
        def create(self, *, model, max_tokens, system, messages):
            log["path"].append("beta")
            return response

    class Client:
        def __init__(self, **kwargs):
            log["client"] = kwargs
            self.messages = PlainMessages()
            beta_cls = BetaMessagesWithFallbacks if has_fallbacks else BetaMessagesOld
            self.beta = types.SimpleNamespace(messages=beta_cls())

    fake = types.ModuleType("anthropic")
    fake.Anthropic = Client
    for name in ("AuthenticationError", "NotFoundError", "RateLimitError",
                 "APIConnectionError", "APIStatusError"):
        setattr(fake, name, type(name, (Exception,), {}))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return log


class TestClientConfig:
    def test_client_gets_short_timeout_and_single_retry(self, monkeypatch):
        log = _install_fake_anthropic(monkeypatch)
        CodeGenWorker._call_api({"model": "claude-opus-5", "api_key": "k"}, "s", "u")
        assert log["client"]["timeout"] == ai_codegen.API_TIMEOUT_SECONDS == 60.0
        assert log["client"]["max_retries"] == ai_codegen.API_MAX_RETRIES == 1
        assert log["client"]["api_key"] == "k"

    def test_beta_fallback_only_for_official_endpoint_and_supported_sdk(self, monkeypatch):
        log = _install_fake_anthropic(monkeypatch)
        CodeGenWorker._call_api({"model": "claude-opus-5"}, "s", "u")
        CodeGenWorker._call_api({"model": "claude-opus-5", "base_url": "https://gw.example"}, "s", "u")
        CodeGenWorker._call_api({"model": "claude-sonnet-5"}, "s", "u")
        assert log["path"] == ["beta", "plain", "plain"]
        # 旧 SDK：create 没有 fallbacks 参数 → 直接走普通路径，不靠捕获 TypeError
        log = _install_fake_anthropic(monkeypatch, has_fallbacks=False)
        CodeGenWorker._call_api({"model": "claude-opus-5"}, "s", "u")
        assert log["path"] == ["plain"]


class TestDescribeFrame:
    def test_duplicate_column_names_do_not_break(self):
        df = pd.DataFrame([[1, 'a'], [None, 'b']], columns=['x', 'x'])
        text = describe_frame(df)
        assert text.count("'x'") == 2 and "| 1 |" in text and "| 2 |" in text


class TestCancelWhileGenerating:
    def test_reject_detaches_running_worker(self, monkeypatch):
        import threading
        gate = threading.Event()
        monkeypatch.setattr(CodeGenWorker, "_call_api", staticmethod(
            lambda s, sy, u: gate.wait(10) and "```python\nlate = 1\n```"))
        dlg = AiGenerateDialog(_df())
        dlg.request_edit.setPlainText("慢")
        dlg._generate()
        worker = dlg._worker
        assert worker.isRunning()
        assert worker.parent() is not dlg                 # 对话框销毁不会连带销毁运行中的线程
        assert "取消" in dlg.status.text() or "Cancel" in dlg.status.text()
        dlg.reject()                                      # 立刻返回，不等线程
        assert dlg._worker is None and worker.isRunning()
        gate.set()
        assert worker.wait(5000)
        _app.processEvents()
        assert dlg.code is None                           # 迟到的结果不再回到对话框
