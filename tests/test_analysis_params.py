"""预设参数块解析/回填 与 列名友好报错 的测试"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from qtui import analysis_params as ap
from qtui.python_analysis import DEFAULT_PRESETS

PIVOT = (
    "# ===== 参数：把引号里的列名改成你的 =====\n"
    "ROW_DIM = '行维度'     # 透视表的行\n"
    "COL_DIM = '列维度'     # 透视表的列\n"
    "VALUE_COL = '数值列'   # 汇总的值\n"
    "TOP_N = 10             # 取前几行\n"
    "SUBSET_COLS = None\n"
    "FLAG = True  # 开关\n"
    "TARGET = '目标值 # 不是注释'   # 分类要等于什么\n"
    "# =====================================\n"
    "result = pd.pivot_table(df, index=ROW_DIM)\n"
)


class TestParse:
    def test_parses_block_in_order_with_kinds(self):
        params = ap.parse_params(PIVOT)
        assert [p.name for p in params] == [
            'ROW_DIM', 'COL_DIM', 'VALUE_COL', 'TOP_N', 'SUBSET_COLS', 'FLAG', 'TARGET']
        kinds = {p.name: p.kind for p in params}
        assert kinds['ROW_DIM'] == ap.KIND_COLUMN
        assert kinds['VALUE_COL'] == ap.KIND_COLUMN
        assert kinds['TOP_N'] == ap.KIND_NUMBER
        assert kinds['SUBSET_COLS'] == ap.KIND_COLUMNS
        assert kinds['FLAG'] == ap.KIND_BOOL
        assert kinds['TARGET'] == ap.KIND_TEXT

    def test_values_and_comments(self):
        by = {p.name: p for p in ap.parse_params(PIVOT)}
        assert by['ROW_DIM'].value == '行维度'
        assert by['ROW_DIM'].comment == '透视表的行'
        assert by['TOP_N'].value == 10
        assert by['SUBSET_COLS'].value is None and by['SUBSET_COLS'].comment == ''
        # 字符串里的 # 不是注释
        assert by['TARGET'].value == '目标值 # 不是注释'
        assert by['TARGET'].comment == '分类要等于什么'

    def test_no_fence_means_no_params(self):
        assert ap.parse_params("X_COL = 'a'\nprint(df)") == []
        assert ap.parse_params("print(df.describe())") == []

    def test_fence_must_be_at_top(self):
        code = "print(1)\n# =====\nA_COL = 'x'\n# =====\n"
        assert ap.parse_params(code) == []

    def test_non_literal_lines_skipped(self):
        code = "# =====\nA_COL = df.columns[0]\nB_COL = 'b'\n# =====\n"
        assert [p.name for p in ap.parse_params(code)] == ['B_COL']

    @pytest.mark.parametrize("name", list(DEFAULT_PRESETS))
    def test_default_presets_with_fence_expose_params(self, name):
        code = DEFAULT_PRESETS[name]
        params = ap.parse_params(code)
        if "# =====" in code:
            assert params, f"{name} 有围栏但没解析出参数"
        else:
            assert params == []


class TestSetParam:
    def test_replaces_value_keeps_comment_and_rest(self):
        out = ap.set_param(PIVOT, 'ROW_DIM', 'ground_truth')
        lines = out.split("\n")
        assert lines[1].startswith("ROW_DIM = 'ground_truth'")
        assert lines[1].endswith("# 透视表的行")
        # 其他行一字不改
        assert lines[0] == PIVOT.split("\n")[0]
        assert lines[-2] == "result = pd.pivot_table(df, index=ROW_DIM)"
        assert ap.parse_params(out)[0].value == 'ground_truth'

    def test_number_bool_none_list(self):
        out = ap.set_param(PIVOT, 'TOP_N', 25)
        assert "TOP_N = 25" in out
        out = ap.set_param(out, 'FLAG', False)
        assert "FLAG = False  # 开关" in out
        out = ap.set_param(out, 'SUBSET_COLS', ['a', 'b'])
        assert "SUBSET_COLS = ['a', 'b']" in out
        out = ap.set_param(out, 'SUBSET_COLS', None)
        assert "SUBSET_COLS = None" in out

    def test_unknown_param_untouched(self):
        assert ap.set_param(PIVOT, 'NOPE', 1) == PIVOT

    def test_value_with_quote_is_escaped(self):
        out = ap.set_param(PIVOT, 'TARGET', "it's")
        assert ap.parse_params(out)[-1].value == "it's"
        compile(out, "<t>", "exec")   # 仍是合法 Python


class TestColumnHint:
    COLS = ['ground_truth', 'ground_truth_age', 'prediction_wide', '序号', '时间']

    def _run(self, code):
        df = pd.DataFrame({c: [1, 2] for c in self.COLS})
        try:
            exec(code, {"df": df, "pd": pd})
        except Exception as e:
            return e
        return None

    def test_plain_keyerror_suggests_close_match(self):
        exc = self._run("df['ground_trut']")
        hint = ap.column_hint(exc, self.COLS)
        assert "列 'ground_trut' 不存在" in hint
        assert "'ground_truth'" in hint
        assert "当前可用列" in hint

    def test_groupby_missing_column(self):
        exc = self._run("df.groupby('分组列').sum()")
        hint = ap.column_hint(exc, self.COLS)
        assert "'分组列' 不存在" in hint

    def test_list_indexing_not_in_index(self):
        exc = self._run("df[['序号', '时闻']]")
        hint = ap.column_hint(exc, self.COLS)
        assert "'时闻' 不存在" in hint and "'时间'" in hint
        assert "'序号' 不存在" not in hint

    def test_unrelated_errors_return_none(self):
        assert ap.column_hint(ZeroDivisionError("x"), self.COLS) is None
        assert ap.column_hint(KeyError('序号'), self.COLS) is None  # 列其实存在
        assert ap.column_hint(KeyError('x'), []) is None

    def test_long_column_list_is_truncated(self):
        cols = [f'c{i}' for i in range(50)]
        hint = ap.column_hint(KeyError('zz'), cols, max_list=10)
        assert "共 50 列" in hint and "'c10'" not in hint
