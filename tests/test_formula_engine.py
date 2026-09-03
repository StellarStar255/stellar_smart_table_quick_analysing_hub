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


# ---------- Excel 坐标系：第 1 行是表头 ----------

class TestExcelCoordinates:
    def test_row1_is_header(self, engine):
        assert engine.evaluate('=A1') == 'X'
        assert engine.evaluate('=C1') == 'N'

    def test_header_in_concat(self, engine):
        assert engine.evaluate('=CONCAT(A1, ": ", A2)') == 'X: 5'

    def test_range_including_header(self, engine):
        # 表头文本被数值聚合过滤（同 Excel），COUNTA 计入
        assert engine.evaluate('=SUM(A1:A4)') == 12
        assert engine.evaluate('=COUNTA(A1:A4)') == 4

    def test_row2_is_first_data_row(self, engine):
        assert engine.evaluate('=A2') == 5


# ---------- 基础 ----------

class TestBasics:
    def test_non_formula_returned_as_is(self, engine):
        assert engine.evaluate('hello') == 'hello'

    def test_arithmetic(self, engine):
        assert engine.evaluate('=A2+B2') == 6
        assert engine.evaluate('=A2*2+B3/2') == 11

    def test_cell_ref_case_insensitive(self, engine):
        assert engine.evaluate('=a2+b2') == 6

    def test_absolute_ref(self, engine):
        assert engine.evaluate('=$A$2+B2') == 6

    def test_out_of_range_ref_is_zero(self, engine):
        assert engine.evaluate('=A10+A2') == 5

    def test_integer_result_has_no_decimal(self, engine):
        result = engine.evaluate('=A2+B2')
        assert isinstance(result, int)


# ---------- 函数 ----------

class TestFunctions:
    def test_sum_range(self, engine):
        assert engine.evaluate('=SUM(A2:A4)') == 12

    def test_average(self, engine):
        assert engine.evaluate('=AVERAGE(A2:A4)') == 4

    def test_max_min(self, engine):
        assert engine.evaluate('=MAX(A2:A4)') == 5
        assert engine.evaluate('=MIN(A2:A4)') == 3

    def test_count(self, engine):
        assert engine.evaluate('=COUNT(A2:A4)') == 3

    def test_if(self, engine):
        assert engine.evaluate('=IF(A2>10, "高", "低")') == '低'

    def test_abs_round_power_sqrt_mod(self, engine):
        assert engine.evaluate('=ABS(0-A2)') == 5
        assert engine.evaluate('=ROUND(A2/B3, 2)') == 2.5
        assert engine.evaluate('=POWER(B3, 3)') == 8
        assert engine.evaluate('=SQRT(A3*B3*2)') == 4
        assert engine.evaluate('=MOD(A2, B3)') == 1

    def test_text_functions(self, engine):
        assert engine.evaluate('=CONCAT(C2, "-")') == 'ab-'
        assert engine.evaluate('=CONCATENATE(C2, C2)') == 'abab'
        assert engine.evaluate('=LEFT(C2, 1)') == 'a'
        assert engine.evaluate('=RIGHT(C2, 1)') == 'b'
        assert engine.evaluate('=LEN(C2)') == 2
        assert engine.evaluate('=UPPER(C2)') == 'AB'
        assert engine.evaluate('=LOWER(UPPER(C2))') == 'ab'
        assert engine.evaluate('=TRIM(CONCAT(C2, " "))') == 'ab'

    def test_right_zero_chars(self, engine):
        assert engine.evaluate('=RIGHT(C2, 0)') == ''

    def test_concat_formats_integral_float_without_decimal(self, engine):
        assert engine.evaluate('=CONCAT(A2, C2)') == '5ab'

    def test_function_name_case_insensitive(self, engine):
        assert engine.evaluate('=sum(A2:A4)') == 12
        assert engine.evaluate('=Sum(A2:A4)') == 12

    def test_aggregates_skip_text_cells(self, engine):
        # 区域含文本列时聚合只取数值
        assert engine.evaluate('=SUM(A2:C2)') == 6


# ---------- 嵌套 ----------

class TestNesting:
    def test_if_with_nested_sum(self, engine):
        assert engine.evaluate('=IF(SUM(A2:A4)>10, "高", "低")') == '高'
        assert engine.evaluate('=IF(SUM(B2:B3)>10, "高", "低")') == '低'

    def test_round_average(self, engine):
        assert engine.evaluate('=ROUND(AVERAGE(A2:A4), 1)') == 4

    def test_deep_nesting(self, engine):
        assert engine.evaluate('=IF(LEN(CONCAT(A2, B2))>1, 1, 0)') == 1

    def test_nested_in_condition_and_branches(self, engine):
        assert engine.evaluate('=IF(A2>MAX(B2:B4), SUM(A2:A3), MIN(A2:A3))') == 9

    def test_function_mixed_with_arithmetic(self, engine):
        assert engine.evaluate('=SUM(A2:A4) + MIN(B2:B3)') == 13


# ---------- Excel 风格比较符 ----------

class TestOperators:
    def test_excel_equals(self, engine):
        assert engine.evaluate('=IF(A2=5, "yes", "no")') == 'yes'

    def test_excel_not_equals(self, engine):
        assert engine.evaluate('=IF(A2<>5, "yes", "no")') == 'no'

    def test_equals_with_nested_function(self, engine):
        assert engine.evaluate('=IF(MOD(SUM(A2:A4),2)=0, "even", "odd")') == 'even'

    def test_string_comparison(self, engine):
        assert engine.evaluate('=IF(C2="ab", 1, 0)') == 1
        assert engine.evaluate('=IF(C2<>"ab", 1, 0)') == 0

    def test_python_style_still_works(self, engine):
        assert engine.evaluate('=IF(A2==5, 1, 0)') == 1
        assert engine.evaluate('=IF(A2!=5, 1, 0)') == 0
        assert engine.evaluate('=IF(A2>=5, 1, 0)') == 1
        assert engine.evaluate('=IF(A2<=4, 1, 0)') == 0


# ---------- 字符串字面量保护 ----------

class TestStringLiteralProtection:
    def test_cell_ref_inside_string_not_replaced(self, engine):
        assert engine.evaluate('=CONCAT("A1 is: ", A2)') == 'A1 is: 5'

    def test_function_name_inside_string_not_replaced(self, engine):
        assert engine.evaluate('=CONCAT("SUM(x) = ", SUM(A2:A3))') == 'SUM(x) = 9'

    def test_operators_inside_string_not_converted(self, engine):
        assert engine.evaluate('=CONCAT("a=", "b<>c")') == 'a=b<>c'

    def test_comparison_with_string_containing_equals(self, engine):
        assert engine.evaluate('=IF(C3="a=b", 1, 0)') == 1
        assert engine.evaluate('=IF(C4="x<>y", 1, 0)') == 1

    def test_cell_value_containing_operators(self, engine):
        # 单元格文本值本身含 = / <>，替换进表达式后不应被二次处理
        assert engine.evaluate('=CONCAT(C3, "!")') == 'a=b!'

    def test_cell_value_containing_quote(self):
        df = pd.DataFrame({'X': ['say "hi"']})
        e = FormulaEngine(df)
        assert e.evaluate('=CONCAT(A2, "!")') == 'say "hi"!'


# ---------- 错误处理 ----------

class TestErrors:
    def test_sqrt_negative_is_num_error(self, engine):
        assert engine.evaluate('=SQRT(0-A2)') == '#NUM!'

    def test_power_producing_complex_is_num_error(self, engine):
        assert engine.evaluate('=POWER(0-A2, 0.5)') == '#NUM!'

    def test_division_by_zero(self, engine):
        assert engine.evaluate('=A2/A10') == '#DIV/0!'

    def test_unsupported_function_is_name_error(self, engine):
        assert engine.evaluate('=SUMPRODUCT(A2:A4, B2:B4)') == '#NAME?'

    def test_malformed_formula(self, engine):
        assert engine.evaluate('=SUM(A2:A4') == '#ERROR'

    def test_type_mismatch_is_value_error(self, engine):
        assert engine.evaluate('=C2+A2') == '#VALUE!'

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
        assert engine.evaluate('=AND(A2>4, B2=1)') is True
        assert engine.evaluate('=AND(A2>4, B2=2)') is False

    def test_or(self, engine):
        assert engine.evaluate('=OR(A2>10, B4=3)') is True
        assert engine.evaluate('=OR(A2>10, B4=99)') is False

    def test_not(self, engine):
        assert engine.evaluate('=NOT(A2>10)') is True

    def test_nested_in_if(self, engine):
        assert engine.evaluate('=IF(AND(A2>4, A3>3), "y", "n")') == 'y'

    def test_true_false_literals(self, engine):
        assert engine.evaluate('=AND(TRUE, A2>4)') is True
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
        assert sales.evaluate('=COUNTIF(A2:A5, "a")') == 2

    def test_countif_numeric_criteria(self, sales):
        assert sales.evaluate('=COUNTIF(B2:B5, ">15")') == 3
        assert sales.evaluate('=COUNTIF(B2:B5, "<=20")') == 2
        assert sales.evaluate('=COUNTIF(B2:B5, 20)') == 1

    def test_countif_not_equal(self, sales):
        assert sales.evaluate('=COUNTIF(A2:A5, "<>a")') == 2

    def test_countif_wildcard(self, sales):
        assert sales.evaluate('=COUNTIF(A2:A5, "a*")') == 2
        assert sales.evaluate('=COUNTIF(A2:A5, "?")') == 4

    def test_countif_case_insensitive(self, sales):
        assert sales.evaluate('=COUNTIF(A2:A5, "A")') == 2

    def test_sumif_with_sum_range(self, sales):
        assert sales.evaluate('=SUMIF(A2:A5, "a", B2:B5)') == 40

    def test_sumif_without_sum_range(self, sales):
        assert sales.evaluate('=SUMIF(B2:B5, ">15")') == 90

    def test_averageif(self, sales):
        assert sales.evaluate('=AVERAGEIF(A2:A5, "a", B2:B5)') == 20

    def test_averageif_no_match_is_div0(self, sales):
        assert sales.evaluate('=AVERAGEIF(A2:A5, "zzz", B2:B5)') == '#DIV/0!'

    def test_nested_with_countif(self, sales):
        assert sales.evaluate('=IF(COUNTIF(A2:A5, "a")=2, "ok", "no")') == 'ok'


# ---------- 查找函数 ----------

@pytest.fixture
def lookup():
    df = pd.DataFrame({
        'ID': ['a', 'b', 'c', 'd'],
        'Name': ['Ann', 'Bob', 'Cid', 'Dan'],
        'Score': [10, 20, 30, 40],
    })
    return FormulaEngine(df)


class TestVlookup:
    def test_exact_match(self, lookup):
        assert lookup.evaluate('=VLOOKUP("b", A2:C5, 2, FALSE)') == 'Bob'
        assert lookup.evaluate('=VLOOKUP("b", A2:C5, 3, FALSE)') == 20

    def test_case_insensitive(self, lookup):
        assert lookup.evaluate('=VLOOKUP("B", A2:C5, 2, FALSE)') == 'Bob'

    def test_wildcard(self, lookup):
        assert lookup.evaluate('=VLOOKUP("b*", A2:C5, 2, FALSE)') == 'Bob'

    def test_approximate_default(self, lookup):
        # 默认近似匹配：<= 25 的最大值是 20
        assert lookup.evaluate('=VLOOKUP(25, C2:C5, 1)') == 20

    def test_not_found_is_na(self, lookup):
        assert lookup.evaluate('=VLOOKUP("z", A2:C5, 2, FALSE)') == '#N/A'

    def test_col_index_out_of_range_is_ref(self, lookup):
        assert lookup.evaluate('=VLOOKUP("a", A2:C5, 9, FALSE)') == '#REF!'

    def test_cell_ref_as_lookup_value(self, lookup):
        assert lookup.evaluate('=VLOOKUP(A3, A2:C5, 3, FALSE)') == 20


class TestMatch:
    def test_exact(self, lookup):
        assert lookup.evaluate('=MATCH("c", A2:A5, 0)') == 3
        assert lookup.evaluate('=MATCH("C", A2:A5, 0)') == 3

    def test_approximate_ascending(self, lookup):
        assert lookup.evaluate('=MATCH(25, C2:C5, 1)') == 2
        assert lookup.evaluate('=MATCH(25, C2:C5)') == 2  # 默认 1

    def test_approximate_descending(self, lookup):
        # -1：>= value 的最小值 -> 30，位置 3
        assert lookup.evaluate('=MATCH(25, C2:C5, 0-1)') == 3

    def test_not_found_is_na(self, lookup):
        assert lookup.evaluate('=MATCH("z", A2:A5, 0)') == '#N/A'

    def test_row_vector(self, lookup):
        assert lookup.evaluate('=MATCH("Name", A2:C2, 0)') == '#N/A'  # 无表头行数据


class TestIndex:
    def test_2d(self, lookup):
        assert lookup.evaluate('=INDEX(A2:C5, 2, 2)') == 'Bob'

    def test_column_vector(self, lookup):
        assert lookup.evaluate('=INDEX(B2:B5, 3)') == 'Cid'

    def test_row_vector(self, lookup):
        assert lookup.evaluate('=INDEX(A3:C3, 1, 3)') == 20

    def test_out_of_range_is_ref(self, lookup):
        assert lookup.evaluate('=INDEX(A2:C5, 9, 1)') == '#REF!'
        assert lookup.evaluate('=INDEX(B2:B5, 0)') == '#REF!'

    def test_index_match_combo(self, lookup):
        assert lookup.evaluate('=INDEX(C2:C5, MATCH("c", A2:A5, 0))') == 30
        assert lookup.evaluate('=INDEX(B2:B5, MATCH(35, C2:C5, 1))') == 'Cid'


class TestXlookup:
    def test_exact(self, lookup):
        assert lookup.evaluate('=XLOOKUP("b", A2:A5, C2:C5)') == 20

    def test_not_found_default_na(self, lookup):
        assert lookup.evaluate('=XLOOKUP("z", A2:A5, C2:C5)') == '#N/A'

    def test_if_not_found(self, lookup):
        assert lookup.evaluate('=XLOOKUP("z", A2:A5, C2:C5, "无")') == '无'

    def test_match_mode_next_smaller(self, lookup):
        assert lookup.evaluate('=XLOOKUP(25, C2:C5, B2:B5, "x", 0-1)') == 'Bob'

    def test_match_mode_next_larger(self, lookup):
        assert lookup.evaluate('=XLOOKUP(25, C2:C5, B2:B5, "x", 1)') == 'Cid'

    def test_wildcard_mode(self, lookup):
        assert lookup.evaluate('=XLOOKUP("c*", A2:A5, B2:B5, "x", 2)') == 'Cid'

    def test_nested_in_if(self, lookup):
        assert lookup.evaluate(
            '=IF(XLOOKUP("b", A2:A5, C2:C5)>15, "高", "低")') == '高'


# ---------- 日期函数 ----------

@pytest.fixture
def dates():
    df = pd.DataFrame({
        'D': pd.to_datetime(['2026-01-15', '2026-07-30', '2024-02-29']),
        'T': pd.to_datetime(['2026-01-15 08:30:00'] * 3),
        'N': [1, 2, 3],
    })
    return FormulaEngine(df)


class TestDateFunctions:
    def test_datetime_cell_reads_as_iso_string(self, dates):
        # datetime 单元格进公式不再是非法语法；0 点只保留日期
        assert dates.evaluate('=CONCAT(A2, "!")') == '2026-01-15!'
        assert dates.evaluate('=CONCAT(B2, "!")') == '2026-01-15 08:30:00!'

    def test_datetime_cells_compare_chronologically(self, dates):
        assert dates.evaluate('=IF(A2<A3, 1, 0)') == 1
        assert dates.evaluate('=IF(A4<A2, 1, 0)') == 1

    def test_year_month_day_on_datetime_cell(self, dates):
        assert dates.evaluate('=YEAR(A2)') == 2026
        assert dates.evaluate('=MONTH(A3)') == 7
        assert dates.evaluate('=DAY(A4)') == 29

    def test_year_on_string_and_serial(self, dates):
        assert dates.evaluate('=YEAR("2026-07-30")') == 2026
        assert dates.evaluate('=YEAR(45000)') == 2023  # Excel 序列号

    def test_date_builds_iso_string(self, dates):
        assert dates.evaluate('=DATE(2026, 7, 30)') == '2026-07-30'

    def test_date_overflow_normalizes(self, dates):
        assert dates.evaluate('=DATE(2025, 13, 1)') == '2026-01-01'
        assert dates.evaluate('=DATE(2026, 2, 30)') == '2026-03-02'

    def test_today_and_now(self, dates):
        import datetime as dt
        assert dates.evaluate('=TODAY()') == dt.date.today().isoformat()
        now = dates.evaluate('=NOW()')
        assert now.startswith(dt.date.today().isoformat()) and len(now) == 19

    def test_today_composes(self, dates):
        assert dates.evaluate('=YEAR(TODAY())') >= 2026

    def test_weekday(self, dates):
        # 2026-07-30 是周四
        assert dates.evaluate('=WEEKDAY("2026-07-30")') == 5      # 周日=1
        assert dates.evaluate('=WEEKDAY("2026-07-30", 2)') == 4   # 周一=1
        assert dates.evaluate('=WEEKDAY("2026-07-30", 3)') == 3   # 周一=0

    def test_days(self, dates):
        assert dates.evaluate('=DAYS("2026-07-30", "2026-07-01")') == 29
        assert dates.evaluate('=DAYS(A3, A2)') == 196

    def test_datedif(self, dates):
        assert dates.evaluate('=DATEDIF("2024-02-29", "2026-07-30", "Y")') == 2
        assert dates.evaluate('=DATEDIF("2024-02-29", "2026-07-30", "M")') == 29
        assert dates.evaluate('=DATEDIF(A2, A3, "D")') == 196

    def test_datedif_errors(self, dates):
        assert dates.evaluate('=DATEDIF("2026-01-01", "2025-01-01", "Y")') == '#NUM!'
        assert dates.evaluate('=DATEDIF("2025-01-01", "2026-01-01", "X")') == '#VALUE!'

    def test_unparseable_date_is_value_error(self, dates):
        assert dates.evaluate('=YEAR("abc")') == '#VALUE!'

    def test_nested_with_if(self, dates):
        assert dates.evaluate('=IF(YEAR(A2)=2026, "今年", "往年")') == '今年'


# ---------- COUNT / COUNTA ----------

class TestCountSemantics:
    def test_count_only_numbers(self, engine):
        # A1=5, B1=1 是数值，C1='ab' 是文本
        assert engine.evaluate('=COUNT(A2:C2)') == 2

    def test_counta_includes_text(self, engine):
        assert engine.evaluate('=COUNTA(A2:C2)') == 3


# ---------- 代码审查回归（finding 2/3/9/10/4） ----------

class TestIfLazyEvaluation:
    """finding 2: IF 分支必须惰性求值，防错写法不能先触发错误"""

    def test_guarded_division(self, engine):
        # A9 越界读为 0，防除零守卫应返回 0 而不是 #DIV/0!
        assert engine.evaluate('=IF(A10=0, 0, A2/A10)') == 0

    def test_guarded_lookup_untaken_branch(self, engine):
        assert engine.evaluate(
            '=IF(FALSE, VLOOKUP("zz", A2:B4, 2, FALSE), "ok")') == 'ok'

    def test_taken_branch_error_still_surfaces(self, engine):
        assert engine.evaluate('=IF(TRUE, A2/A10, 0)') == '#DIV/0!'

    def test_two_arg_if(self, engine):
        assert engine.evaluate('=IF(A2>10, "big")') is False
        assert engine.evaluate('=IF(A2>1, "big")') == 'big'


class TestStringEscapes:
    """finding 3: 反斜杠不是转义符，"" 是 Excel 的引号转义"""

    def test_backslash_not_python_escape(self, engine):
        assert engine.evaluate(r'=CONCAT("C:\new", "!")') == r'C:\new!'

    def test_invalid_python_escape_ok(self, engine):
        assert engine.evaluate(r'="C:\Users"') == r'C:\Users'

    def test_doubled_quote_escape(self, engine):
        assert engine.evaluate('="He said ""hi"""') == 'He said "hi"'

    def test_doubled_quote_in_comparison(self, engine):
        assert engine.evaluate('=IF("a""b"="a""b", 1, 0)') == 1


class TestScientificNotation:
    """finding 9: 1E5 是数字字面量，不是 E 列引用"""

    def test_evaluate(self, engine):
        assert engine.evaluate('=1E5') == 100000
        assert engine.evaluate('=1e5+A2') == 100005

    def test_shift_leaves_literal(self, engine):
        assert engine.shift_formula('=1E5+A2', 1, 0) == '=1E5+A3'

    def test_adjust_leaves_literal(self, engine):
        assert engine.adjust_formula_refs(
            '=1E5+A2', row_map=lambda i: i + 1) == '=1E5+A3'

    def test_remap_leaves_literal(self, engine):
        assert engine.remap_formula_rows('=1E5+A2', {0: 1}) == '=1E5+A3'


class TestWildcardBracketLiteral:
    """finding 10: Excel 通配符里 [ 是字面字符，不是字符类"""

    def test_countif(self):
        df = pd.DataFrame({'C': ['item[12]a', 'item1x', 'item2y', 'other']})
        e = FormulaEngine(df)
        assert e.evaluate('=COUNTIF(A2:A5, "item[12]*")') == 1

    def test_vlookup(self):
        df = pd.DataFrame({'C': ['a[1]', 'a1'], 'V': [10, 20]})
        e = FormulaEngine(df)
        assert e.evaluate('=VLOOKUP("a[1]*", A2:B3, 2, FALSE)') == 10


class TestPartialRangeDetection:
    """finding 4 的引擎助手"""

    def test_partial(self, engine):
        assert engine.formula_has_partial_ranges('=SUM(A2:A3)', 3)
        assert engine.formula_has_partial_ranges('=SUM(A3:A4)', 3)

    def test_full_coverage(self, engine):
        assert not engine.formula_has_partial_ranges('=SUM(A2:A4)', 3)
        assert not engine.formula_has_partial_ranges('=SUM(A2:A1000)', 3)

    def test_no_range(self, engine):
        assert not engine.formula_has_partial_ranges('=A2*2', 3)
        assert not engine.formula_has_partial_ranges('text', 3)

    def test_range_in_string_ignored(self, engine):
        assert not engine.formula_has_partial_ranges('=CONCAT("A1:A2")', 3)


# ---------- 复制/填充引用平移 ----------

class TestShiftFormula:
    def test_relative_shift(self, engine):
        assert engine.shift_formula('=A2*2', 1, 0) == '=A3*2'
        assert engine.shift_formula('=A2+B3', 2, 1) == '=B4+C5'

    def test_negative_shift(self, engine):
        assert engine.shift_formula('=B3', -1, -1) == '=A2'

    def test_absolute_refs_stay(self, engine):
        assert engine.shift_formula('=$A$2', 3, 3) == '=$A$2'
        assert engine.shift_formula('=$A2', 3, 3) == '=$A5'
        assert engine.shift_formula('=A$2', 3, 3) == '=D$2'

    def test_range_endpoints_shift(self, engine):
        assert engine.shift_formula('=SUM(A2:A4)', 1, 1) == '=SUM(B3:B5)'

    def test_multi_letter_column(self, engine):
        assert engine.shift_formula('=Z2', 0, 1) == '=AA2'

    def test_out_of_bounds_becomes_ref_error(self, engine):
        # A2 上移一行落在表头行（合法引用）；再上移越界 -> #REF!
        assert engine.shift_formula('=A2', -1, 0) == '=A1'
        assert engine.shift_formula('=A1', -1, 0) == '=#REF!'
        assert engine.evaluate('=#REF!') == '#REF!'
        assert engine.evaluate('=A2+#REF!*2') == '#REF!'

    def test_string_literal_untouched(self, engine):
        assert engine.shift_formula('=CONCAT("A1", A2)', 1, 0) == '=CONCAT("A1", A3)'

    def test_zero_delta_and_non_formula(self, engine):
        assert engine.shift_formula('=A2', 0, 0) == '=A2'
        assert engine.shift_formula('text', 1, 1) == 'text'


# ---------- 插入/删除行列的引用调整 ----------

def _insert_map(pos, delta=1):
    return lambda i: i + delta if i >= pos else i


def _delete_map(deleted):
    dels = sorted(deleted)
    return lambda i: None if i in deleted else i - sum(1 for x in dels if x < i)


class TestAdjustFormulaRefs:
    def test_insert_row_shifts_refs_below(self, engine):
        assert engine.adjust_formula_refs('=A2+A4', row_map=_insert_map(1)) == '=A2+A5'

    def test_insert_row_grows_range(self, engine):
        assert engine.adjust_formula_refs('=SUM(A2:A4)', row_map=_insert_map(1)) == '=SUM(A2:A5)'

    def test_insert_row_below_range_no_change(self, engine):
        assert engine.adjust_formula_refs('=SUM(A2:A4)', row_map=_insert_map(3)) == '=SUM(A2:A4)'

    def test_insert_column_shifts_letters(self, engine):
        assert engine.adjust_formula_refs('=A2+B2', col_map=_insert_map(0)) == '=B2+C2'

    def test_delete_referenced_row_is_ref_error(self, engine):
        assert engine.adjust_formula_refs('=A3*2', row_map=_delete_map({1})) == '=#REF!*2'

    def test_delete_shifts_refs_after(self, engine):
        assert engine.adjust_formula_refs('=A4', row_map=_delete_map({0})) == '=A3'

    def test_delete_inside_range_shrinks(self, engine):
        assert engine.adjust_formula_refs('=SUM(A2:A4)', row_map=_delete_map({1})) == '=SUM(A2:A3)'

    def test_delete_range_endpoint_shrinks(self, engine):
        assert engine.adjust_formula_refs('=SUM(A2:A4)', row_map=_delete_map({2})) == '=SUM(A2:A3)'

    def test_delete_whole_range_is_ref_error(self, engine):
        # 与 Excel 一致：区域整体被删时保留函数外壳，区域位置显示 #REF!
        adjusted = engine.adjust_formula_refs('=SUM(A2:A3)+1', row_map=_delete_map({0, 1}))
        assert adjusted == '=SUM(#REF!)+1'
        assert engine.evaluate(adjusted) == '#REF!'

    def test_delete_column_in_range(self, engine):
        assert engine.adjust_formula_refs('=SUM(A2:C2)', col_map=_delete_map({1})) == '=SUM(A2:B2)'

    def test_absolute_refs_also_adjusted(self, engine):
        # $ 只固定复制填充，结构变化时同样调整
        assert engine.adjust_formula_refs('=$A$3', row_map=_insert_map(0)) == '=$A$4'

    def test_string_literal_untouched(self, engine):
        assert engine.adjust_formula_refs(
            '=CONCAT("A1", A2)', row_map=_insert_map(0)) == '=CONCAT("A1", A3)'

    def test_no_maps_returns_same(self, engine):
        assert engine.adjust_formula_refs('=A2') == '=A2'


# ---------- 排序行号重映射 ----------

class TestRemapFormulaRows:
    def test_single_refs_remapped(self, engine):
        row_map = {0: 2, 1: 0, 2: 1}
        assert engine.remap_formula_rows('=A2+B3', row_map) == '=A4+B2'

    def test_range_refs_unchanged(self, engine):
        row_map = {0: 2, 1: 0, 2: 1}
        assert engine.remap_formula_rows('=SUM(A2:A4)+A2', row_map) == '=SUM(A2:A4)+A4'

    def test_absolute_row_unchanged(self, engine):
        row_map = {0: 2, 1: 0, 2: 1}
        assert engine.remap_formula_rows('=A$2+A3', row_map) == '=A$2+A2'

    def test_string_literal_unchanged(self, engine):
        row_map = {0: 2}
        assert engine.remap_formula_rows('=CONCAT("A1", A2)', row_map) == '=CONCAT("A1", A4)'

    def test_unmapped_row_unchanged(self, engine):
        assert engine.remap_formula_rows('=A10+A2', {0: 1}) == '=A10+A3'

    def test_non_formula_unchanged(self, engine):
        assert engine.remap_formula_rows('hello', {0: 1}) == 'hello'


# ---------- 全面审查回归：区域空白、位置引用、运算符与文本函数 ----------

class TestBlankCellsInRanges:
    """区域里的空白单元格是空白，不是 0（COUNT/AVERAGE/MIN 才与 Excel 一致）"""

    @pytest.fixture
    def gaps(self):
        return FormulaEngine(pd.DataFrame({'X': [10.0, None, None, 20.0]}))

    def test_count_and_counta_skip_blanks(self, gaps):
        assert gaps.evaluate('=COUNT(A2:A5)') == 2
        assert gaps.evaluate('=COUNTA(A2:A5)') == 2

    def test_average_min_ignore_blanks(self, gaps):
        assert gaps.evaluate('=AVERAGE(A2:A5)') == 15
        assert gaps.evaluate('=MIN(A2:A5)') == 10

    def test_over_provisioned_range(self, gaps):
        # 常见写法：区域远大于数据行数，越界行不能算成 0
        assert gaps.evaluate('=AVERAGE(A2:A100)') == 15
        assert gaps.evaluate('=COUNTIF(A2:A100, "<15")') == 1

    def test_countif_blank_criteria(self, gaps):
        assert gaps.evaluate('=COUNTIF(A2:A5, "")') == 2

    def test_scalar_blank_ref_is_still_zero(self, gaps):
        assert gaps.evaluate('=A3+1') == 1
        assert gaps.evaluate('=A99+1') == 1

    def test_range_result_single_cell_and_multi(self, gaps):
        assert gaps.evaluate('=A2:A2') == 10
        assert gaps.evaluate('=A2:A5') == '#VALUE!'

    def test_reversed_range(self, gaps):
        assert gaps.evaluate('=SUM(A5:A2)') == 30


class TestPositionalColumnRefs:
    def test_out_of_range_column_is_blank_not_named_column(self):
        # 第二列恰好叫 "C"：=C2 是第三列（不存在），不能读到第二列
        e = FormulaEngine(pd.DataFrame({'A': [1.0], 'C': [99.0]}))
        assert e.evaluate('=C2') == 0
        assert e.evaluate('=B2') == 99
        assert e.extract_dependencies('=C2+B2') == {(0, 2), (0, 1)}

    def test_range_dependencies_clipped_to_table(self):
        e = FormulaEngine(pd.DataFrame({'A': [1.0, 2.0]}))
        assert e.extract_dependencies('=SUM(A2:A100)') == {(0, 0), (1, 0)}
        assert e.extract_dependencies('=SUM(A1:A3)') == {(-1, 0), (0, 0), (1, 0)}

    def test_string_literal_is_not_a_reference(self):
        e = FormulaEngine(pd.DataFrame({'A': [1.0, 2.0]}))
        assert e.extract_dependencies('=CONCAT("A2", A3)') == {(1, 0)}


class TestOperatorsAndText:
    @pytest.fixture
    def nums(self):
        return FormulaEngine(pd.DataFrame({'X': [10.0, 2.5, 0.125]}))

    def test_caret_is_power(self, nums):
        assert nums.evaluate('=2^3') == 8
        assert nums.evaluate('=A2^2') == 100

    def test_round_half_up(self, nums):
        assert nums.evaluate('=ROUND(A3, 0)') == 3
        assert nums.evaluate('=ROUND(A4, 2)') == 0.13
        assert nums.evaluate('=ROUND(-2.5, 0)') == -3
        assert nums.evaluate('=ROUND(1234, -2)') == 1200
        assert nums.evaluate('=ROUNDUP(1.21, 1)') == 1.3
        assert nums.evaluate('=ROUNDDOWN(1.29, 1)') == 1.2

    def test_text_functions_see_integers_without_decimal(self, nums):
        assert nums.evaluate('=LEN(A2)') == 2
        assert nums.evaluate('=LEFT(A2, 1)') == '1'
        assert nums.evaluate('=RIGHT(A2, 1)') == '0'
        assert nums.evaluate('=CONCAT(A2, "-", A3)') == '10-2.5'
        assert nums.evaluate('=CONCAT(TRUE, "")') == 'TRUE'

    def test_non_finite_cell_is_num_error(self):
        e = FormulaEngine(pd.DataFrame({'X': [float('inf')]}))
        assert e.evaluate('=A2+1') == '#NUM!'
        assert e.evaluate('=SUM(A2:A2)') == '#NUM!'


class TestSingleRowRangeRemap:
    def test_single_row_range_follows_row(self):
        e = FormulaEngine()
        assert e.remap_formula_rows('=SUM(A2:C2)', {0: 3}) == '=SUM(A5:C5)'
        # 多行区域保持不变
        assert e.remap_formula_rows('=SUM(A2:A9)', {0: 3}) == '=SUM(A2:A9)'
        # 绝对行不动
        assert e.remap_formula_rows('=SUM(A$2:C$2)', {0: 3}) == '=SUM(A$2:C$2)'

    def test_single_row_range_is_not_partial(self):
        e = FormulaEngine()
        assert not e.formula_has_partial_ranges('=SUM(A2:C2)', 10)
        assert e.formula_has_partial_ranges('=SUM(A2:A3)', 10)
