"""Python 数据分析窗口：参数表单、列名补全、结果/图表标签页、分组预设"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QComboBox

_app = QApplication.instance() or QApplication([])

from qtui import python_analysis
from qtui.i18n import tr
from qtui.python_analysis import (
    CodeEditor, CodeRunWorker, PythonAnalysisWindow, DEFAULT_PRESETS,
    PRESET_CATEGORIES, flatten_frame,
)


def _df():
    return pd.DataFrame({
        'ground_truth': ['a', 'b', 'a'],
        'ground_truth_age': [1, 2, 3],
        'prediction': ['a', 'a', 'b'],
        '序号': [1, 2, 3],
    })


class _Host:
    def __init__(self, df):
        self.model = SimpleNamespace(df=df)
        self.current_file = None
        self.added = []
        self.status = []

    def add_sheet_from_df(self, df, name):
        self.added.append((df, name))

    def update_statusbar(self, msg):
        self.status.append(msg)


@pytest.fixture
def win(monkeypatch, tmp_path):
    monkeypatch.setattr(python_analysis, "PRESETS_FILE", str(tmp_path / "p.json"))
    w = PythonAnalysisWindow(host=_Host(_df()))
    yield w
    w.close()


def run_sync(win):
    """同步跑一遍 run_code 的工作线程（不真正开线程）。"""
    win.run_code()
    worker = win._worker
    worker.run()
    _app.processEvents()
    return worker


class TestParamPanel:
    def test_preset_with_params_shows_form_with_column_dropdowns(self, win):
        win.preset_combo.setCurrentText("透视表")
        assert not win.param_panel.isHidden()
        names = [p.name for p in win.param_panel.params()]
        assert names == ['ROW_DIM', 'COL_DIM', 'VALUE_COL']
        combo = win.param_panel.widget_for('ROW_DIM')
        assert isinstance(combo, QComboBox)
        assert [combo.itemText(i) for i in range(combo.count())] == list(_df().columns)
        assert combo.currentText() == '行维度'   # 占位符原样保留，等用户选

    def test_changing_dropdown_rewrites_only_that_line(self, win):
        win.preset_combo.setCurrentText("透视表")
        before = win.code_edit.toPlainText()
        combo = win.param_panel.widget_for('ROW_DIM')
        combo.setCurrentText('ground_truth')
        after = win.code_edit.toPlainText()
        b, a = before.split("\n"), after.split("\n")
        assert a[1].startswith("ROW_DIM = 'ground_truth'") and a[1].endswith("# 透视表的行")
        assert [x for i, x in enumerate(a) if i != 1] == [x for i, x in enumerate(b) if i != 1]

    def test_form_edit_keeps_editor_clean_for_next_preset(self, win):
        win.preset_combo.setCurrentText("透视表")
        win.param_panel.widget_for('ROW_DIM').setCurrentText('ground_truth')
        # 改过表单后再选别的预设仍应自动应用（不是"手改过的脏内容"）
        win.preset_combo.setCurrentText("频次统计")
        assert win.code_edit.toPlainText() == DEFAULT_PRESETS["频次统计"]

    def test_hand_edit_syncs_back_to_form(self, win):
        win.preset_combo.setCurrentText("TopN 排序")
        code = win.code_edit.toPlainText().replace("TOP_N = 10", "TOP_N = 99")
        win.code_edit.setPlainText(code)
        win._sync_params_from_code()
        assert win.param_panel.widget_for('TOP_N').text() == "99"

    def test_number_param_updates_code(self, win):
        win.preset_combo.setCurrentText("TopN 排序")
        edit = win.param_panel.widget_for('TOP_N')
        edit.setText("5")
        edit.editingFinished.emit()
        assert "TOP_N = 5 " in win.code_edit.toPlainText()

    def test_code_without_params_hides_form(self, win):
        win.preset_combo.setCurrentText("透视表")
        win.code_edit.setPlainText("print(df)")
        win._sync_params_from_code()
        assert win.param_panel.isHidden()

    def test_columns_param_none_and_list(self, win):
        win.preset_combo.setCurrentText("重复值检查")
        picker = win.param_panel.widget_for('SUBSET_COLS')
        picker.edit.setText("序号, prediction")
        picker.changed.emit()
        assert "SUBSET_COLS = ['序号', 'prediction']" in win.code_edit.toPlainText()
        picker.edit.setText("")
        picker.changed.emit()
        assert "SUBSET_COLS = None" in win.code_edit.toPlainText()


class TestPresetCombo:
    def test_grouped_with_disabled_headers_and_no_initial_selection(self, win):
        combo = win.preset_combo
        assert combo.currentIndex() == -1
        headers = [combo.itemText(i) for i in range(combo.count())
                   if not combo.model().item(i).isEnabled()]
        assert headers[0].startswith("── ")
        assert len(headers) == len(PRESET_CATEGORIES)
        items = [combo.itemText(i) for i in range(combo.count())
                 if combo.model().item(i).isEnabled()]
        assert set(items) == set(DEFAULT_PRESETS)

    def test_every_default_preset_is_categorized(self):
        cat = {n for _, names in PRESET_CATEGORIES for n in names}
        assert cat == set(DEFAULT_PRESETS)

    def test_user_preset_goes_to_my_presets_group(self, win):
        win._user_presets["我的"] = "print(1)"
        win.presets = win._compose_presets()
        win._update_preset_combo(select_name="我的")
        combo = win.preset_combo
        idx = combo.currentIndex()
        assert combo.itemText(idx) == "我的"
        assert combo.itemText(idx - 1) == f"── {tr('我的预设')} ──"


class TestCodeEditorCompletion:
    def test_string_prefix_detects_open_quote(self):
        ed = CodeEditor()
        ed.setPlainText("x = df['gro")
        ed.moveCursor(ed.textCursor().MoveOperation.End)
        assert ed.string_prefix() == ("'", "gro")
        ed.setPlainText("x = df['a'] + 1")
        ed.moveCursor(ed.textCursor().MoveOperation.End)
        assert ed.string_prefix() is None
        ed.setPlainText("# 'comment")
        ed.moveCursor(ed.textCursor().MoveOperation.End)
        assert ed.string_prefix() is None

    def test_insert_column_name_quotes_when_needed(self):
        ed = CodeEditor()
        ed.setPlainText("df[")
        ed.moveCursor(ed.textCursor().MoveOperation.End)
        ed.insert_column_name("序号")
        assert ed.toPlainText() == "df['序号'"
        ed.setPlainText("df['")
        ed.moveCursor(ed.textCursor().MoveOperation.End)
        ed.insert_column_name("序号")
        assert ed.toPlainText() == "df['序号"

    def test_completion_inserts_full_name(self):
        ed = CodeEditor()
        ed.set_completion_words(['ground_truth', 'ground_truth_age'])
        ed.setPlainText("df['gro")
        ed.moveCursor(ed.textCursor().MoveOperation.End)
        ed._insert_completion('ground_truth_age')
        assert ed.toPlainText() == "df['ground_truth_age"

    def test_replace_line_keeps_other_lines(self):
        ed = CodeEditor()
        ed.setPlainText("a = 1\nb = 2\nc = 3")
        ed.replace_line(1, "b = 20  # x")
        assert ed.toPlainText() == "a = 1\nb = 20  # x\nc = 3"


class TestRunResultsAndTabs:
    def test_result_dataframe_shown_in_result_tab(self, win):
        win.code_edit.setPlainText("result = df[df['序号'] > 1]")
        run_sync(win)
        assert win.tabs.currentIndex() == win.TAB_RESULT
        assert win.result_combo.currentData() == 'result'
        assert win.result_view.model().rowCount() >= 2
        assert "(2)" in win.tabs.tabText(win.TAB_RESULT)   # df + result

    def test_unchanged_df_only_goes_to_output(self, win):
        win.code_edit.setPlainText("print(len(df))")
        run_sync(win)
        assert win.tabs.currentIndex() == win.TAB_OUTPUT
        assert "3" in win.output_edit.toPlainText()

    def test_error_goes_to_output_with_column_hint(self, win):
        win.code_edit.setPlainText("df.groupby('ground_trut').size()")
        run_sync(win)
        assert win.tabs.currentIndex() == win.TAB_OUTPUT
        out = win.output_edit.toPlainText()
        assert "KeyError" in out
        assert tr("列 '{}' 不存在").format('ground_trut') in out and "'ground_truth'" in out

    def test_figures_shown_in_chart_tab(self, win):
        win.code_edit.setPlainText(
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots()\n"
            "df['序号'].plot(ax=ax)\n"
            "show_figure(fig, '趋势')\n"
            "df['序号'].hist()\n")   # 第二张没显式调用，也要被抓到
        run_sync(win)
        assert win.tabs.currentIndex() == win.TAB_FIGURES
        assert win.figure_gallery.count() == 2
        assert "(2)" in win.tabs.tabText(win.TAB_FIGURES)

    def test_clear_outputs_resets_everything(self, win):
        win.code_edit.setPlainText("result = df.head(1)")
        run_sync(win)
        win._clear_outputs()
        assert win.result_combo.count() == 0
        assert win.result_view.model() is None
        assert win.figure_gallery.count() == 0


class TestWorkerExtras:
    def test_extra_namespace_injected_and_not_listed_as_result(self):
        other = pd.DataFrame({'x': [1]})
        w = CodeRunWorker("out = extra_df.copy()", _df(), None,
                          extra={"extra_df": other})
        got = []
        w.done.connect(lambda *a: got.append(a))
        w.run()
        output, result_dfs, *_ = got[0]
        assert 'out' in result_dfs and 'extra_df' not in result_dfs


class TestFlattenFrame:
    def test_pivot_keeps_row_labels(self):
        df = _df()
        pv = pd.pivot_table(df, index='ground_truth', columns='prediction',
                            values='序号', aggfunc='sum', fill_value=0)
        flat = flatten_frame(pv)
        assert list(flat.columns)[0] == 'ground_truth'
        assert list(flat['ground_truth']) == ['a', 'b']
        assert isinstance(flat.index, pd.RangeIndex)

    def test_groupby_multi_agg_flattens_columns(self):
        g = _df().groupby('ground_truth').agg({'序号': ['sum', 'mean']})
        flat = flatten_frame(g)
        assert list(flat.columns) == ['ground_truth', '序号_sum', '序号_mean']

    def test_plain_frame_untouched_but_copied(self):
        df = _df()
        flat = flatten_frame(df)
        assert flat is not df and flat.equals(df)

    def test_index_name_clashing_with_column(self):
        df = pd.DataFrame({'k': [1, 2], 'v': [3, 4]})
        df.index = pd.Index(['x', 'y'], name='k')
        flat = flatten_frame(df)
        assert 'k_index' in flat.columns and 'k' in flat.columns

    def test_result_tab_shows_pivot_row_labels(self, win):
        win.code_edit.setPlainText(
            "result = pd.pivot_table(df, index='ground_truth', columns='prediction', "
            "values='序号', aggfunc='sum', fill_value=0)")
        run_sync(win)
        model = win.result_view.model()
        assert model.columnCount() == 3   # ground_truth + a + b

    def test_save_as_sheet_uses_flattened_frame(self, win, monkeypatch):
        win.code_edit.setPlainText("result = df.groupby('ground_truth').size().to_frame('n')")
        run_sync(win)
        monkeypatch.setattr(python_analysis.QInputDialog, "getText",
                            staticmethod(lambda *a, **k: ("汇总", True)))
        win._save_result_as_sheet()
        saved_df, name = win.host.added[-1]
        assert name == "汇总" and list(saved_df.columns) == ['ground_truth', 'n']


class TestErrorLocation:
    def test_output_names_the_failing_user_line(self, win):
        win.code_edit.setPlainText("x = 1\ny = 2\nz = df['nope']\n")
        run_sync(win)
        out = win.output_edit.toPlainText()
        assert tr("⚠ 出错位置：你的代码第 {} 行：{}").format(3, "z = df['nope']") in out


class _RichHost(_Host):
    """带 sheet / 选区 / 回写接口的假宿主。"""

    def __init__(self, df):
        super().__init__(df)
        self.sheet_names = ['Sheet1', '其他']
        self.current_sheet = 'Sheet1'
        self.original_df = None
        self.replaced = []
        self.appended = []
        self._sel = (None, [])

    def get_sheet_df(self, name):
        if name == 'Sheet1':
            return self.model.df.copy()
        if name == '其他':
            return pd.DataFrame({'k': [1, 2]})
        raise KeyError(name)

    def selection_frame(self):
        return self._sel

    def replace_current_sheet_df(self, df):
        self.replaced.append(df)

    def append_columns_to_current(self, df):
        self.appended.append(df)
        return list(df.columns)


@pytest.fixture
def rich(monkeypatch, tmp_path):
    w = PythonAnalysisWindow(host=_RichHost(_df()))
    yield w
    w.close()


class TestExtraNamespace:
    def test_sheets_and_names_available(self, rich):
        rich.code_edit.setPlainText(
            "print(sheet_names)\nother = sheets['其他']\nprint(len(sheets), '其他' in sheets)")
        run_sync(rich)
        out = rich.output_edit.toPlainText()
        assert "['Sheet1', '其他']" in out and "2 True" in out
        assert 'other' in rich._result_dfs and len(rich._result_dfs['other']) == 2

    def test_missing_sheet_error_lists_available(self, rich):
        rich.code_edit.setPlainText("sheets['没有']")
        run_sync(rich)
        out = rich.output_edit.toPlainText()
        assert "'Sheet1', '其他'" in out

    def test_selection_and_df_full(self, rich):
        rich.host._sel = (_df().iloc[[0, 1], [0, 3]], ['ground_truth', '序号'])
        rich.host.original_df = pd.concat([_df(), _df()], ignore_index=True)
        rich.code_edit.setPlainText(
            "print(selection.shape, selected_columns)\nprint(len(df_full), len(df))")
        run_sync(rich)
        out = rich.output_edit.toPlainText()
        assert "(2, 2) ['ground_truth', '序号']" in out
        assert "6 3" in out

    def test_no_selection_is_none(self, win):
        win.code_edit.setPlainText("print(selection is None, sheet_names, len(df_full))")
        run_sync(win)
        assert "True [] 3" in win.output_edit.toPlainText()


class TestWriteBack:
    def _yes(self, monkeypatch):
        monkeypatch.setattr(python_analysis.QMessageBox, "question",
                            staticmethod(lambda *a, **k: python_analysis.QMessageBox.StandardButton.Yes))

    def _no(self, monkeypatch):
        monkeypatch.setattr(python_analysis.QMessageBox, "question",
                            staticmethod(lambda *a, **k: python_analysis.QMessageBox.StandardButton.No))

    def test_replace_requires_confirmation(self, rich, monkeypatch):
        rich.code_edit.setPlainText("result = df.groupby('ground_truth').size().to_frame('n')")
        run_sync(rich)
        self._no(monkeypatch)
        rich._replace_current_sheet()
        assert rich.host.replaced == []
        self._yes(monkeypatch)
        rich._replace_current_sheet()
        assert len(rich.host.replaced) == 1
        assert list(rich.host.replaced[0].columns) == ['ground_truth', 'n']   # 索引已展开

    def test_append_checks_row_count_then_confirms(self, rich, monkeypatch):
        rich.code_edit.setPlainText("result = df[['序号']].rename(columns={'序号': 'x2'}) * 2")
        run_sync(rich)
        self._yes(monkeypatch)
        rich._append_result_columns()
        assert len(rich.host.appended) == 1 and list(rich.host.appended[0]['x2']) == [2, 4, 6]
        # 行数不一致：不弹确认直接提示
        rich.code_edit.setPlainText("result = df.head(1)")
        run_sync(rich)
        warned = []
        monkeypatch.setattr(python_analysis.QMessageBox, "warning",
                            staticmethod(lambda *a, **k: warned.append(a)))
        rich._append_result_columns()
        assert warned and len(rich.host.appended) == 1


class TestHistoryAndPersistence:
    def test_run_records_history_newest_first_and_dedupes(self, win):
        win.code_edit.setPlainText("a = 1")
        run_sync(win)
        win.code_edit.setPlainText("b = 2")
        run_sync(win)
        win.code_edit.setPlainText("a = 1")
        run_sync(win)
        assert [h['code'] for h in win._history] == ["a = 1", "b = 2"]
        assert os.path.exists(python_analysis.HISTORY_FILE)

    def test_failed_run_marked(self, win):
        win.code_edit.setPlainText("1/0")
        run_sync(win)
        assert win._history[0]['ok'] is False
        assert "✗" in win._history_label(win._history[0])

    def test_history_limit(self, win):
        for i in range(python_analysis.HISTORY_LIMIT + 5):
            win._record_history(f"x = {i}")
        assert len(win._history) == python_analysis.HISTORY_LIMIT

    def test_last_code_restored_in_new_window(self, win):
        win.code_edit.setPlainText("print('again')")
        win.close()
        w2 = PythonAnalysisWindow(host=_Host(_df()))
        try:
            assert w2.code_edit.toPlainText() == "print('again')"
            assert w2._editor_is_clean()   # 恢复的内容不算"脏"
        finally:
            w2.close()

    def test_load_history_entry_asks_when_dirty(self, win, monkeypatch):
        win._record_history("z = 3")
        win.code_edit.setPlainText("my own edits")
        monkeypatch.setattr(python_analysis.QMessageBox, "question",
                            staticmethod(lambda *a, **k: python_analysis.QMessageBox.StandardButton.No))
        win._load_history_entry(win._history[0])
        assert win.code_edit.toPlainText() == "my own edits"
        monkeypatch.setattr(python_analysis.QMessageBox, "question",
                            staticmethod(lambda *a, **k: python_analysis.QMessageBox.StandardButton.Yes))
        win._load_history_entry(win._history[0])
        assert win.code_edit.toPlainText() == "z = 3"
