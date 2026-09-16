"""公式引擎 Excel 语义回归测试（代码审查 finding 1-12）

运行: QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests/test_formula_semantics.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from core.formula_engine import FormulaEngine, _RangeValues


@pytest.fixture
def eng():
    df = pd.DataFrame({
        # 列名按位置取 A/B/C/D：A 数值、B 错误值、C 布尔、D 大数
        'A': [-3.0, 1.0, 2.0],
        'B': ['#DIV/0!', 'x', 'y'],
        'C': [True, False, True],
        'D': [1e300, 0.1, 10.0],
    })
    return FormulaEngine(df)


# ---------- 1. 负数与 ^ 的优先级 ----------

class TestNegationAndPower:
    def test_negative_cell_squared(self, eng):
        # A2 = -3：Excel 里 =A2^2 是 9，不能拼成 -3.0**2 = -9
        assert eng.evaluate('=A2^2') == 9

    def test_literal_unary_minus_binds_tighter_than_power(self, eng):
        assert eng.evaluate('=-2^2') == 4
        assert eng.evaluate('=-A2^2') == 9

    def test_binary_minus_is_lower_than_power(self, eng):
        assert eng.evaluate('=0-2^2') == -4
        assert eng.evaluate('=1-2^2') == -3

    def test_negative_exponent_and_parenthesised_base(self, eng):
        assert eng.evaluate('=2^-2') == 0.25
        assert eng.evaluate('=-(2^3)^2') == 64

    def test_negative_cell_in_arithmetic(self, eng):
        assert eng.evaluate('=A2*A2') == 9
        assert eng.evaluate('=2-A2') == 5


# ---------- 1b. ^ 左结合 ----------

class TestPowerAssociativity:
    def test_chain_is_left_associative(self, eng):
        # Excel: 2^3^2 = (2^3)^2 = 64；Python ** 右结合会得 512
        assert eng.evaluate('=2^3^2') == 64
        assert eng.evaluate('=2^3^2^2') == 4096

    def test_explicit_parentheses_respected(self, eng):
        assert eng.evaluate('=2^(3^2)') == 512
        assert eng.evaluate('=(2^3)^2') == 64
        assert eng.evaluate('=2^(3^2)^2') == 262144      # (2^(3^2))^2
        assert eng.evaluate('=2^3^(2^2)') == 4096        # (2^3)^(2^2)
        assert eng.evaluate('=2^(1+2)^2') == 64          # (2^3)^2

    def test_chain_with_cell_references(self, eng):
        # A4 = 2：单元格替换成 (2.0) 带括号，不能被当成分组打断链
        assert eng.evaluate('=2^A4^3') == 64             # (2^2)^3
        assert eng.evaluate('=2^(A4^3)') == 256
        assert eng.evaluate('=A4^A4^A4') == 16           # (2^2)^2

    def test_composes_with_unary_minus(self, eng):
        # 负号落在最左底数上，再左结合
        assert eng.evaluate('=-2^3^2') == 64             # ((-2)^3)^2
        assert eng.evaluate('=-2^2^3') == 64             # ((-2)^2)^3；右结合会得 -256
        assert eng.evaluate('=-2^3^3') == -512           # ((-2)^3)^3
        assert eng.evaluate('=-(2^2)^3') == -64          # (-(2^2))^3：括号分组停住负号
        assert eng.evaluate('=-(2^3)^2') == 64

    def test_composes_with_percent(self, eng):
        assert eng.evaluate('=4^50%^2') == 4             # (4^0.5)^2；右结合会得 4^0.25
        assert eng.evaluate('=10%^2^2') == 0.0001        # ((0.1)^2)^2
        assert eng.evaluate('=2^3^2%') == pytest.approx(8 ** 0.02)   # % 紧贴 2

    def test_multibyte_text_before_chain(self, eng):
        # 位置是 UTF-8 字节偏移，中文字面量在前不能让分组判断错位
        assert eng.evaluate('=LEN("高")&2^3^2') == '164'
        assert eng.evaluate('=IF("高"="高", 2^3^2, 0)') == 64

    def test_lazy_branches_and_precedence_unchanged(self, eng):
        assert eng.evaluate('=IF(TRUE, 2^3^2, 1/0)') == 64
        assert eng.evaluate('=2*3^2') == 18
        assert eng.evaluate('=2^3*2') == 16
        assert eng.evaluate('=POWER(2,3)^2') == 64


# ---------- 2. 错误值传播 ----------

class TestErrorPropagation:
    def test_sum_over_range_with_error_cell(self, eng):
        assert eng.evaluate('=SUM(A2:B4)') == '#DIV/0!'
        assert eng.evaluate('=AVERAGE(A2:B4)') == '#DIV/0!'
        assert eng.evaluate('=MAX(B2:B4)') == '#DIV/0!'

    def test_scalar_reference_propagates_original_code(self, eng):
        assert eng.evaluate('=B2+1') == '#DIV/0!'
        assert eng.evaluate('=B2') == '#DIV/0!'
        assert eng.evaluate('=CONCAT(B2, "x")') == '#DIV/0!'
        assert eng.evaluate('=AND(B2, TRUE)') == '#DIV/0!'

    def test_other_error_codes_kept(self):
        e = FormulaEngine(pd.DataFrame({'A': ['#N/A', '#NAME?']}))
        assert e.evaluate('=A2*2') == '#N/A'
        assert e.evaluate('=SUM(A2:A3)') == '#N/A'
        assert e.evaluate('=A3&"x"') == '#NAME?'

    def test_iferror_still_catches(self, eng):
        assert eng.evaluate('=IFERROR(B2, "bad")') == 'bad'
        assert eng.evaluate('=IFERROR(SUM(A2:B4), -1)') == -1
        assert eng.evaluate('=IFERROR(B2+1, 0)') == 0

    def test_if_untaken_branch_is_lazy(self, eng):
        assert eng.evaluate('=IF(FALSE, B2, 1)') == 1
        assert eng.evaluate('=IF(TRUE, B2, 1)') == '#DIV/0!'

    def test_count_ignores_errors_counta_counts_them(self, eng):
        assert eng.evaluate('=COUNT(A2:B4)') == 3
        assert eng.evaluate('=COUNTA(A2:B4)') == 6

    def test_conditional_aggregates_propagate_matched_errors(self, eng):
        # 命中的求和格是错误 -> 错误；未命中的错误格被忽略
        assert eng.evaluate('=SUMIF(A2:A4, "<0", B2:B4)') == '#DIV/0!'
        assert eng.evaluate('=SUMIF(A2:A4, ">0", B2:B4)') == 0
        assert eng.evaluate('=SUMIFS(B2:B4, A2:A4, "<0")') == '#DIV/0!'
        assert eng.evaluate('=SUMIFS(B2:B4, A2:A4, ">0")') == 0

    def test_range_values_first_error_scans_only_text_columns(self):
        rv = _RangeValues([[1.0, 2.0], ['x', '#NUM!']], [True, False])
        assert rv.first_error() == '#NUM!'
        assert _RangeValues([[1.0]], [True]).first_error() is None


# ---------- 3. & 文本连接与 % 后缀 ----------

class TestAmpersandAndPercent:
    def test_ampersand_concatenates(self, eng):
        assert eng.evaluate('="a"&"b"') == 'ab'
        assert eng.evaluate('=A3&A4') == '12'
        assert eng.evaluate('="x"&C2') == 'xTRUE'

    def test_ampersand_precedence(self, eng):
        # & 低于加减、高于比较（与 Excel 一致）
        assert eng.evaluate('=1+2&"x"') == '3x'
        assert eng.evaluate('=A3&"b"="1b"') is True

    def test_percent_postfix(self, eng):
        assert eng.evaluate('=10%') == 0.1
        assert eng.evaluate('=D4*10%') == 1
        assert eng.evaluate('=A4%') == 0.02
        assert eng.evaluate('=-10%') == -0.1

    def test_percent_binds_tighter_than_power(self, eng):
        assert eng.evaluate('=10%^2') == 0.01
        assert eng.evaluate('=2^10%') == pytest.approx(2 ** 0.1)

    def test_percent_after_parenthesis_and_function(self, eng):
        assert eng.evaluate('=(1+1)%') == 0.02
        assert eng.evaluate('=SUM(A3:A4)%') == 0.03

    def test_percent_inside_string_untouched(self, eng):
        assert eng.evaluate('=CONCAT("100%", "!")') == '100%!'
        assert eng.evaluate('="a&b"') == 'a&b'

    def test_pct_marker_name_not_reachable(self, eng):
        assert eng.evaluate('=_pct_') == '#NAME?'


# ---------- 4. INT 与裸内建 ----------

class TestIntFunction:
    def test_int_is_floor(self, eng):
        assert eng.evaluate('=INT(2.9)') == 2
        assert eng.evaluate('=INT(-1.5)') == -2
        assert eng.evaluate('=int(A4/3)') == 0
        assert eng.evaluate('=INT(TRUE)') == 1

    def test_int_of_text_is_value_error(self, eng):
        assert eng.evaluate('=INT("x")') == '#VALUE!'

    def test_index_still_works_next_to_int(self, eng):
        assert eng.evaluate('=INDEX(A2:B4, 2, 2)') == 'x'

    def test_raw_python_builtins_not_reachable(self, eng):
        assert eng.evaluate('=float(1)') == '#NAME?'
        assert eng.evaluate('=str(1)') == '#NAME?'
        assert eng.evaluate('=round(1.5)') == 2   # ROUND 的实现，不是 Python round


# ---------- 5. AVERAGE 无数值 ----------

class TestAverageEmpty:
    def test_average_of_no_numbers_is_div0(self):
        e = FormulaEngine(pd.DataFrame({'T': ['a', 'b'], 'X': [None, None]}))
        assert e.evaluate('=AVERAGE(A2:A3)') == '#DIV/0!'
        assert e.evaluate('=AVERAGE(B2:B3)') == '#DIV/0!'
        assert e.evaluate('=IFERROR(AVERAGE(A2:A3), 0)') == 0

    def test_average_with_numbers_unchanged(self, eng):
        assert eng.evaluate('=AVERAGE(A2:A4)') == 0


# ---------- 6. 布尔保持布尔 ----------

class TestBooleanCells:
    def test_bool_cell_reads_as_bool(self, eng):
        assert eng.evaluate('=C2') is True
        assert eng.evaluate('=C3') is False
        assert eng.evaluate('=CONCAT(C2, "")') == 'TRUE'
        assert eng.evaluate('=IF(C2, "y", "n")') == 'y'

    def test_bool_in_arithmetic_is_one_or_zero(self, eng):
        assert eng.evaluate('=C2+1') == 2
        assert eng.evaluate('=C3*5') == 0
        assert eng.evaluate('=C2=TRUE') is True

    def test_bool_ignored_by_numeric_aggregates(self, eng):
        # 与 Excel 一致：区域里的布尔不参与 SUM/COUNT
        assert eng.evaluate('=SUM(C2:C4)') == 0
        assert eng.evaluate('=COUNT(C2:C4)') == 0
        assert eng.evaluate('=COUNTA(C2:C4)') == 3

    def test_numpy_bool_in_object_column(self):
        import numpy as np
        e = FormulaEngine(pd.DataFrame({'A': pd.Series([np.True_, 'x'], dtype=object)}))
        assert e.evaluate('=A2') is True


# ---------- 7. 溢出 ----------

class TestOverflow:
    def test_float_overflow_is_num_error(self, eng):
        assert eng.evaluate('=2.0^100000') == '#NUM!'
        assert eng.evaluate('=POWER(10, 400)') == '#NUM!'
        assert eng.evaluate('=D2*D2') == '#NUM!'   # 1e300 * 1e300 -> inf


# ---------- 8. LEFT / RIGHT / MID 参数校验 ----------

class TestTextFunctionArguments:
    def test_negative_length_is_value_error(self, eng):
        assert eng.evaluate('=LEFT("abc", -1)') == '#VALUE!'
        assert eng.evaluate('=RIGHT("abc", -1)') == '#VALUE!'
        assert eng.evaluate('=MID("abc", 2, -1)') == '#VALUE!'

    def test_mid_start_below_one_is_value_error(self, eng):
        assert eng.evaluate('=MID("abc", 0, 1)') == '#VALUE!'

    def test_non_numeric_length_is_value_error(self, eng):
        assert eng.evaluate('=LEFT("abc", "x")') == '#VALUE!'

    def test_valid_edge_cases(self, eng):
        assert eng.evaluate('=LEFT("abc", 0)') == ''
        assert eng.evaluate('=MID("abc", 2, 2)') == 'bc'
        assert eng.evaluate('=MID("abc", 5, 2)') == ''
        assert eng.evaluate('=LEFT("abc", 1.9)') == 'a'


# ---------- 9. 大浮点不转 int ----------

class TestLargeFloatFormatting:
    def test_huge_integral_float_stays_float(self, eng):
        r = eng.evaluate('=D2')
        assert isinstance(r, float) and r == 1e300
        r = eng.evaluate('=1E300*1')
        assert isinstance(r, float)

    def test_safe_integers_still_become_int(self, eng):
        assert eng.evaluate('=2^52') == 2 ** 52
        assert isinstance(eng.evaluate('=2^52'), int)
        assert isinstance(eng.evaluate('=2^53'), float)


# ---------- 10. 乘方炸弹 ----------

class TestExponentBomb:
    def test_power_tower_returns_num_error_quickly(self, eng):
        t = time.time()
        assert eng.evaluate('=9^9^9^9') == '#NUM!'
        assert eng.evaluate('=POWER(9, 9^9^9)') == '#NUM!'
        assert time.time() - t < 1.0

    def test_integer_literals_still_format_as_int(self, eng):
        assert eng.evaluate('=2^10') == 1024
        assert isinstance(eng.evaluate('=1+2'), int)
        assert eng.evaluate('=VLOOKUP(2, A2:B4, 2, FALSE)') == 'y'
        assert eng.evaluate('=ROUND(2.567, 2)') == 2.57


# ---------- 11. 查找快路径 ----------

class TestLookupFastPath:
    def test_vlookup_on_range_values(self, eng):
        assert eng.evaluate('=VLOOKUP(2, A2:D4, 2, FALSE)') == 'y'
        assert eng.evaluate('=VLOOKUP(2, A2:D4, 4, FALSE)') == 10
        assert eng.evaluate('=VLOOKUP(2, A2:D4, 5, FALSE)') == '#REF!'
        assert eng.evaluate('=VLOOKUP(2, A2:D4, 0, FALSE)') == '#REF!'
        assert eng.evaluate('=VLOOKUP(1.5, A2:A4, 1)') == 1

    def test_vlookup_on_plain_grid_with_header_row(self, eng):
        # 含表头行的区域退化为普通嵌套列表，慢路径仍正确
        assert eng.evaluate('=VLOOKUP("A", A1:B4, 2, FALSE)') == 'B'
        assert eng.evaluate('=VLOOKUP(1, A1:B4, 2, FALSE)') == 'x'

    def test_vlookup_does_not_copy_table(self, monkeypatch):
        e = FormulaEngine(pd.DataFrame({'K': list(range(2000)), 'V': list(range(2000))}))
        calls = []
        orig = _RangeValues.__iter__

        def counting_iter(self):
            calls.append(1)
            return orig(self)

        monkeypatch.setattr(_RangeValues, '__iter__', counting_iter)
        assert e.evaluate('=VLOOKUP(1999, A2:B2001, 2, FALSE)') == 1999
        # 快路径按列取首列、按位置取行，不再整表迭代复制
        assert calls == []


# ---------- 12. 依赖提取薄封装 ----------

class TestDependencyWrappers:
    def test_extract_dependencies_expands_spec(self):
        e = FormulaEngine(pd.DataFrame({'A': [1.0, 2.0], 'B': [3.0, 4.0]}))
        assert e.extract_dependencies('=SUM(A2:B3)+B2') == {(0, 0), (1, 0), (0, 1), (1, 1)}
        cells, ranges = e.extract_dependency_spec('=SUM(A2:B3)+B2')
        assert cells == {(0, 1)} and ranges == [(0, 1, 0, 1)]

    def test_out_of_table_range_dropped(self):
        e = FormulaEngine(pd.DataFrame({'A': [1.0]}))
        assert e.extract_dependencies('=SUM(C5:C9)') == set()
        assert e.extract_dependencies('=SUM(A2:A9)') == {(0, 0)}

    def test_parse_range_ref_removed(self):
        assert not hasattr(FormulaEngine, 'parse_range_ref')
