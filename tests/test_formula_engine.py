"""公式引擎单元测试

运行: python3 -m pytest tests/ -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from core.formula_engine import FormulaEngine


@pytest.fixture
def engine():
    df = pd.DataFrame({
        'X': [5, 4, 3],
        'Y': [1, 2, 3],
        'N': ['ab', 'a=b', 'x<>y'],
    })
    return FormulaEngine(df)


# ---------- 基础 ----------

class TestBasics:
    def test_non_formula_returned_as_is(self, engine):
        assert engine.evaluate('hello') == 'hello'

    def test_arithmetic(self, engine):
        assert engine.evaluate('=A1+B1') == 6
        assert engine.evaluate('=A1*2+B2/2') == 11

    def test_cell_ref_case_insensitive(self, engine):
        assert engine.evaluate('=a1+b1') == 6

    def test_absolute_ref(self, engine):
        assert engine.evaluate('=$A$1+B1') == 6

    def test_out_of_range_ref_is_zero(self, engine):
        assert engine.evaluate('=A9+A1') == 5

    def test_integer_result_has_no_decimal(self, engine):
        result = engine.evaluate('=A1+B1')
        assert isinstance(result, int)


# ---------- 函数 ----------

class TestFunctions:
    def test_sum_range(self, engine):
        assert engine.evaluate('=SUM(A1:A3)') == 12

    def test_average(self, engine):
        assert engine.evaluate('=AVERAGE(A1:A3)') == 4

    def test_max_min(self, engine):
        assert engine.evaluate('=MAX(A1:A3)') == 5
        assert engine.evaluate('=MIN(A1:A3)') == 3

    def test_count(self, engine):
        assert engine.evaluate('=COUNT(A1:A3)') == 3

    def test_if(self, engine):
        assert engine.evaluate('=IF(A1>10, "高", "低")') == '低'

    def test_abs_round_power_sqrt_mod(self, engine):
        assert engine.evaluate('=ABS(0-A1)') == 5
        assert engine.evaluate('=ROUND(A1/B2, 2)') == 2.5
        assert engine.evaluate('=POWER(B2, 3)') == 8
        assert engine.evaluate('=SQRT(A2*B2*2)') == 4
        assert engine.evaluate('=MOD(A1, B2)') == 1

    def test_text_functions(self, engine):
        assert engine.evaluate('=CONCAT(C1, "-")') == 'ab-'
        assert engine.evaluate('=CONCATENATE(C1, C1)') == 'abab'
        assert engine.evaluate('=LEFT(C1, 1)') == 'a'
        assert engine.evaluate('=RIGHT(C1, 1)') == 'b'
        assert engine.evaluate('=LEN(C1)') == 2
        assert engine.evaluate('=UPPER(C1)') == 'AB'
        assert engine.evaluate('=LOWER(UPPER(C1))') == 'ab'
        assert engine.evaluate('=TRIM(CONCAT(C1, " "))') == 'ab'

    def test_right_zero_chars(self, engine):
        assert engine.evaluate('=RIGHT(C1, 0)') == ''

    def test_concat_formats_integral_float_without_decimal(self, engine):
        assert engine.evaluate('=CONCAT(A1, C1)') == '5ab'

    def test_function_name_case_insensitive(self, engine):
        assert engine.evaluate('=sum(A1:A3)') == 12
        assert engine.evaluate('=Sum(A1:A3)') == 12

    def test_aggregates_skip_text_cells(self, engine):
        # 区域含文本列时聚合只取数值
        assert engine.evaluate('=SUM(A1:C1)') == 6


# ---------- 嵌套 ----------

class TestNesting:
    def test_if_with_nested_sum(self, engine):
        assert engine.evaluate('=IF(SUM(A1:A3)>10, "高", "低")') == '高'
        assert engine.evaluate('=IF(SUM(B1:B2)>10, "高", "低")') == '低'

    def test_round_average(self, engine):
        assert engine.evaluate('=ROUND(AVERAGE(A1:A3), 1)') == 4

    def test_deep_nesting(self, engine):
        assert engine.evaluate('=IF(LEN(CONCAT(A1, B1))>1, 1, 0)') == 1

    def test_nested_in_condition_and_branches(self, engine):
        assert engine.evaluate('=IF(A1>MAX(B1:B3), SUM(A1:A2), MIN(A1:A2))') == 9

    def test_function_mixed_with_arithmetic(self, engine):
        assert engine.evaluate('=SUM(A1:A3) + MIN(B1:B2)') == 13


# ---------- Excel 风格比较符 ----------

class TestOperators:
    def test_excel_equals(self, engine):
        assert engine.evaluate('=IF(A1=5, "yes", "no")') == 'yes'

    def test_excel_not_equals(self, engine):
        assert engine.evaluate('=IF(A1<>5, "yes", "no")') == 'no'

    def test_equals_with_nested_function(self, engine):
        assert engine.evaluate('=IF(MOD(SUM(A1:A3),2)=0, "even", "odd")') == 'even'

    def test_string_comparison(self, engine):
        assert engine.evaluate('=IF(C1="ab", 1, 0)') == 1
        assert engine.evaluate('=IF(C1<>"ab", 1, 0)') == 0

    def test_python_style_still_works(self, engine):
        assert engine.evaluate('=IF(A1==5, 1, 0)') == 1
        assert engine.evaluate('=IF(A1!=5, 1, 0)') == 0
        assert engine.evaluate('=IF(A1>=5, 1, 0)') == 1
        assert engine.evaluate('=IF(A1<=4, 1, 0)') == 0


# ---------- 字符串字面量保护 ----------

class TestStringLiteralProtection:
    def test_cell_ref_inside_string_not_replaced(self, engine):
        assert engine.evaluate('=CONCAT("A1 is: ", A1)') == 'A1 is: 5'

    def test_function_name_inside_string_not_replaced(self, engine):
        assert engine.evaluate('=CONCAT("SUM(x) = ", SUM(A1:A2))') == 'SUM(x) = 9'

    def test_operators_inside_string_not_converted(self, engine):
        assert engine.evaluate('=CONCAT("a=", "b<>c")') == 'a=b<>c'

    def test_comparison_with_string_containing_equals(self, engine):
        assert engine.evaluate('=IF(C2="a=b", 1, 0)') == 1
        assert engine.evaluate('=IF(C3="x<>y", 1, 0)') == 1

    def test_cell_value_containing_operators(self, engine):
        # 单元格文本值本身含 = / <>，替换进表达式后不应被二次处理
        assert engine.evaluate('=CONCAT(C2, "!")') == 'a=b!'

    def test_cell_value_containing_quote(self):
        df = pd.DataFrame({'X': ['say "hi"']})
        e = FormulaEngine(df)
        assert e.evaluate('=CONCAT(A1, "!")') == 'say "hi"!'


# ---------- 错误处理 ----------

class TestErrors:
    def test_sqrt_negative_is_num_error(self, engine):
        assert engine.evaluate('=SQRT(0-A1)') == '#NUM!'

    def test_power_producing_complex_is_num_error(self, engine):
        assert engine.evaluate('=POWER(0-A1, 0.5)') == '#NUM!'

    def test_division_by_zero(self, engine):
        assert engine.evaluate('=A1/A9') == '#DIV/0!'

    def test_unsupported_function_is_name_error(self, engine):
        assert engine.evaluate('=VLOOKUP(A1, B1:B3, 1)') == '#NAME?'

    def test_malformed_formula(self, engine):
        assert engine.evaluate('=SUM(A1:A3') == '#ERROR'

    def test_type_mismatch_is_value_error(self, engine):
        assert engine.evaluate('=C1+A1') == '#VALUE!'

    def test_builtins_not_reachable(self, engine):
        assert engine.evaluate('=__import__("os")') == '#NAME?'
        assert engine.evaluate('=open("/etc/passwd")') == '#NAME?'

    def test_is_error_helper(self):
        assert FormulaEngine.is_error('#DIV/0!')
        assert FormulaEngine.is_error('#ERROR')
        assert not FormulaEngine.is_error('ok')
        assert not FormulaEngine.is_error(12)


# ---------- 逻辑函数 ----------

class TestLogicFunctions:
    def test_and(self, engine):
        assert engine.evaluate('=AND(A1>4, B1=1)') is True
        assert engine.evaluate('=AND(A1>4, B1=2)') is False

    def test_or(self, engine):
        assert engine.evaluate('=OR(A1>10, B3=3)') is True
        assert engine.evaluate('=OR(A1>10, B3=99)') is False

    def test_not(self, engine):
        assert engine.evaluate('=NOT(A1>10)') is True

    def test_nested_in_if(self, engine):
        assert engine.evaluate('=IF(AND(A1>4, A2>3), "y", "n")') == 'y'

    def test_true_false_literals(self, engine):
        assert engine.evaluate('=AND(TRUE, A1>4)') is True
        assert engine.evaluate('=IF(FALSE, 1, 2)') == 2
        assert engine.evaluate('=if(false, 1, 2)') == 2

    def test_true_inside_string_untouched(self, engine):
        assert engine.evaluate('=CONCAT("TRUE", "!")') == 'TRUE!'


# ---------- 条件聚合 ----------

@pytest.fixture
def sales():
    df = pd.DataFrame({
        'Cat': ['a', 'b', 'a', 'c'],
        'Amt': [10, 20, 30, 40],
    })
    return FormulaEngine(df)


class TestConditionalAggregates:
    def test_countif_text(self, sales):
        assert sales.evaluate('=COUNTIF(A1:A4, "a")') == 2

    def test_countif_numeric_criteria(self, sales):
        assert sales.evaluate('=COUNTIF(B1:B4, ">15")') == 3
        assert sales.evaluate('=COUNTIF(B1:B4, "<=20")') == 2
        assert sales.evaluate('=COUNTIF(B1:B4, 20)') == 1

    def test_countif_not_equal(self, sales):
        assert sales.evaluate('=COUNTIF(A1:A4, "<>a")') == 2

    def test_countif_wildcard(self, sales):
        assert sales.evaluate('=COUNTIF(A1:A4, "a*")') == 2
        assert sales.evaluate('=COUNTIF(A1:A4, "?")') == 4

    def test_countif_case_insensitive(self, sales):
        assert sales.evaluate('=COUNTIF(A1:A4, "A")') == 2

    def test_sumif_with_sum_range(self, sales):
        assert sales.evaluate('=SUMIF(A1:A4, "a", B1:B4)') == 40

    def test_sumif_without_sum_range(self, sales):
        assert sales.evaluate('=SUMIF(B1:B4, ">15")') == 90

    def test_averageif(self, sales):
        assert sales.evaluate('=AVERAGEIF(A1:A4, "a", B1:B4)') == 20

    def test_averageif_no_match_is_div0(self, sales):
        assert sales.evaluate('=AVERAGEIF(A1:A4, "zzz", B1:B4)') == '#DIV/0!'

    def test_nested_with_countif(self, sales):
        assert sales.evaluate('=IF(COUNTIF(A1:A4, "a")=2, "ok", "no")') == 'ok'


# ---------- COUNT / COUNTA ----------

class TestCountSemantics:
    def test_count_only_numbers(self, engine):
        # A1=5, B1=1 是数值，C1='ab' 是文本
        assert engine.evaluate('=COUNT(A1:C1)') == 2

    def test_counta_includes_text(self, engine):
        assert engine.evaluate('=COUNTA(A1:C1)') == 3


# ---------- 排序行号重映射 ----------

class TestRemapFormulaRows:
    def test_single_refs_remapped(self, engine):
        row_map = {0: 2, 1: 0, 2: 1}
        assert engine.remap_formula_rows('=A1+B2', row_map) == '=A3+B1'

    def test_range_refs_unchanged(self, engine):
        row_map = {0: 2, 1: 0, 2: 1}
        assert engine.remap_formula_rows('=SUM(A1:A3)+A1', row_map) == '=SUM(A1:A3)+A3'

    def test_absolute_row_unchanged(self, engine):
        row_map = {0: 2, 1: 0, 2: 1}
        assert engine.remap_formula_rows('=A$1+A2', row_map) == '=A$1+A1'

    def test_string_literal_unchanged(self, engine):
        row_map = {0: 2}
        assert engine.remap_formula_rows('=CONCAT("A1", A1)', row_map) == '=CONCAT("A1", A3)'

    def test_unmapped_row_unchanged(self, engine):
        assert engine.remap_formula_rows('=A9+A1', {0: 1}) == '=A9+A2'

    def test_non_formula_unchanged(self, engine):
        assert engine.remap_formula_rows('hello', {0: 1}) == 'hello'
