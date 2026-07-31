"""Python 数据分析窗口测试：代码执行、预设合并、编辑器行为"""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qtui import python_analysis
from qtui.python_analysis import (
    CodeEditor, CodeRunWorker, PythonAnalysisWindow,
    DEFAULT_PRESETS, DEFAULT_PRESET_VERSIONS,
    DEFAULTS_VERSION, _DEFAULTS_VERSION_KEY,
)


def run_worker(code, df):
    worker = CodeRunWorker(code, df, None)
    results = []
    worker.done.connect(lambda *args: results.append(args))
    worker.run()  # 直接同步执行，不开线程
    return results[0]  # (output, result_dfs, sheet_requests, figure_files)


class TestCodeRunWorker:
    def test_print_output_and_df_scan(self):
        df = pd.DataFrame({'A': [1, 2, 3]})
        output, result_dfs, sheets, figures = run_worker(
            "result = df[df['A'] > 1]\nprint(len(result))", df)
        assert '2' in output
        assert 'result' in result_dfs and len(result_dfs['result']) == 2
        assert sheets == [] and figures == []

    def test_save_as_sheet_queued(self):
        df = pd.DataFrame({'A': [1, 2]})
        output, _, sheets, _ = run_worker(
            "save_as_sheet(df.head(1), '测试')", df)
        assert len(sheets) == 1
        assert sheets[0][1] == '测试'
        assert len(sheets[0][0]) == 1

    def test_error_is_captured_not_raised(self):
        output, _, sheets, _ = run_worker("1/0", pd.DataFrame())
        assert 'ZeroDivisionError' in output
        assert sheets == []


class TestPresetMerge:
    def _make_window(self, monkeypatch, tmp_path, file_content=None):
        path = str(tmp_path / "presets.json")
        if file_content is not None:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(file_content, f, ensure_ascii=False)
        monkeypatch.setattr(python_analysis, "PRESETS_FILE", path)
        win = PythonAnalysisWindow(host=SimpleNamespace(model=None))
        return win, path

    def test_fresh_install_gets_defaults(self, monkeypatch, tmp_path):
        win, _ = self._make_window(monkeypatch, tmp_path)
        assert set(DEFAULT_PRESETS) <= set(win.presets)
        win.close()

    def test_v1_file_merges_new_defaults_keeping_user_override(
            self, monkeypatch, tmp_path):
        # 老版本文件：用户自定义 + 覆盖过的同名默认预设
        win, path = self._make_window(monkeypatch, tmp_path, {
            "我的分析": "print(1)",
            "描述统计": "print('用户改过的')",
        })
        assert win.presets["我的分析"] == "print(1)"
        assert win.presets["描述统计"] == "print('用户改过的')"  # 用户优先
        assert "透视表" in win.presets  # 新默认预设并入
        # 合并后写回了版本号
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        assert saved[_DEFAULTS_VERSION_KEY] == DEFAULTS_VERSION
        win.close()

    def test_deleted_default_stays_deleted_after_merge(
            self, monkeypatch, tmp_path):
        # 已带版本号的文件里没有"透视表"（用户删过），不应复活
        content = {_DEFAULTS_VERSION_KEY: DEFAULTS_VERSION, "我的": "print(1)"}
        win, _ = self._make_window(monkeypatch, tmp_path, content)
        assert "透视表" not in win.presets
        assert win.presets == {"我的": "print(1)"}
        win.close()


class TestAutoApplyPreset:
    def _make_window(self, monkeypatch, tmp_path):
        monkeypatch.setattr(python_analysis, "PRESETS_FILE",
                            str(tmp_path / "p.json"))
        return PythonAnalysisWindow(host=SimpleNamespace(
            model=None, update_statusbar=lambda *a: None))

    def test_selecting_preset_applies_when_editor_clean(
            self, monkeypatch, tmp_path):
        win = self._make_window(monkeypatch, tmp_path)
        win.preset_combo.setCurrentText('数据类型')
        assert win.code_edit.toPlainText() == DEFAULT_PRESETS['数据类型']
        # 未改动时继续切换，直接替换
        win.preset_combo.setCurrentText('缺失值统计')
        assert win.code_edit.toPlainText() == DEFAULT_PRESETS['缺失值统计']
        win.close()

    def test_selecting_preset_keeps_dirty_editor(self, monkeypatch, tmp_path):
        win = self._make_window(monkeypatch, tmp_path)
        win.code_edit.setPlainText('my_custom = 1')
        win.preset_combo.setCurrentText('数据类型')
        assert win.code_edit.toPlainText() == 'my_custom = 1'  # 不覆盖
        win.close()


class TestDefaultPresetBatches:
    def test_version_batches_cover_all_presets(self):
        batched = [k for keys in DEFAULT_PRESET_VERSIONS.values() for k in keys]
        assert sorted(batched) == sorted(DEFAULT_PRESETS)
        assert len(batched) == len(set(batched))  # 无重复

    def test_v2_file_gets_v3_batch_but_deleted_v2_stays_deleted(
            self, monkeypatch, tmp_path):
        path = str(tmp_path / "presets.json")
        # v2 文件：用户删掉了"透视表"
        content = {_DEFAULTS_VERSION_KEY: 2,
                   "描述统计": DEFAULT_PRESETS["描述统计"]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False)
        monkeypatch.setattr(python_analysis, "PRESETS_FILE", path)
        win = PythonAnalysisWindow(host=SimpleNamespace(model=None))
        assert "透视表" not in win.presets      # 旧批次删除不复活
        assert "频次统计" in win.presets        # 新批次并入
        assert "箱线图" in win.presets
        win.close()


class TestAllDefaultPresetsExecutable:
    """每个默认预设都能在占位列名的样例数据上跑通（不报执行错误）"""

    def _sample_df(self):
        return pd.DataFrame({
            '分类列': ['A', 'B', 'A', 'C', 'B', 'A'],
            '分组列': ['G1', 'G1', 'G2', 'G2', 'G1', 'G2'],
            '行维度': ['R1', 'R2', 'R1', 'R2', 'R1', 'R2'],
            '列维度': ['C1', 'C1', 'C2', 'C2', 'C1', 'C2'],
            '数值列': [10.0, 200.0, 30.0, 4000.0, 50.0, 60.0],
            '数量': [1, 2, 3, 4, 5, 6],
            '单价': [100.0, 200.0, 300.0, 400.0, 500.0, 600.0],
            '文本列': [' a-1 ', 'b-2', ' c-3', 'd-4 ', 'e-5', 'f-6'],
            '日期列': ['2026-01-15', '2026-01-20', '2026-02-01',
                       '2026-02-10', '2026-03-05', '2026-03-08'],
            '数值列X': [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            '数值列Y': [2.0, 4.0, 6.0, 8.0, 10.0, 12.0],
        })

    @pytest.mark.parametrize("name", list(DEFAULT_PRESETS))
    def test_preset_runs_clean(self, name, tmp_path):
        # current_file 指向 tmp，图表类预设的 save_figure 落在临时目录
        worker = CodeRunWorker(DEFAULT_PRESETS[name], self._sample_df(),
                               str(tmp_path / 'data.xlsx'))
        results = []
        worker.done.connect(lambda *a: results.append(a))
        worker.run()
        output = results[0][0]
        assert '[执行错误]' not in output and 'Traceback' not in output, \
            f'{name} 执行失败:\n{output}'


class TestCodeEditor:
    def _press_return(self, editor):
        editor.keyPressEvent(QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return,
            Qt.KeyboardModifier.NoModifier, "\r"))

    def test_auto_indent_keeps_level(self):
        ed = CodeEditor()
        ed.setPlainText("    x = 1")
        ed.moveCursor(ed.textCursor().MoveOperation.End)
        self._press_return(ed)
        assert ed.toPlainText() == "    x = 1\n    "

    def test_auto_indent_adds_level_after_colon(self):
        ed = CodeEditor()
        ed.setPlainText("if x:")
        ed.moveCursor(ed.textCursor().MoveOperation.End)
        self._press_return(ed)
        assert ed.toPlainText() == "if x:\n    "

    def test_tab_inserts_spaces(self):
        ed = CodeEditor()
        ed.keyPressEvent(QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Tab,
            Qt.KeyboardModifier.NoModifier))
        assert ed.toPlainText() == "    "

    def test_line_number_width_grows(self):
        ed = CodeEditor()
        w_small = ed.line_number_width()
        ed.setPlainText("\n" * 120)
        assert ed.line_number_width() > w_small
