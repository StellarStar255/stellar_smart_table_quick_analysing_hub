"""筛选引擎：数值等于、字面包含、无效条件记录

运行: python3 -m pytest tests/ -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from qtui import filter_engine


def _df():
    return pd.DataFrame({'v': [10.0, 20.0, None], 't': ['a.b', 'axb', 'c(1']})


def test_equals_on_float_column_matches_integer_text():
    out, idx = filter_engine.apply_filters(_df(), [{'col': 'v', 'condition': '等于', 'value': '10'}])
    assert idx == [0]
    out, idx = filter_engine.apply_filters(_df(), [{'col': 'v', 'condition': '不等于', 'value': '10'}])
    assert idx == [1, 2]


def test_contains_is_literal_not_regex():
    out, idx = filter_engine.apply_filters(_df(), [{'col': 't', 'condition': '包含', 'value': 'a.b'}])
    assert idx == [0]
    out, idx = filter_engine.apply_filters(_df(), [{'col': 't', 'condition': '包含', 'value': 'c(1'}])
    assert idx == [2]
    assert not filter_engine.last_errors


def test_invalid_numeric_condition_is_reported_not_silent():
    out, idx = filter_engine.apply_filters(_df(), [{'col': 'v', 'condition': '大于', 'value': 'abc'}])
    assert idx == [0, 1, 2]
    assert len(filter_engine.last_errors) == 1
    assert filter_engine.last_errors[0][0]['condition'] == '大于'


# ---------- 缺失值与显示文本（回归） ----------

import numpy as np
import pytest


def _text_df():
    return pd.DataFrame({'t': ['banana', None, np.nan, 'apple', 'None'],
                         'v': [10.0, np.nan, 3.0, 4.5, None]})


def test_contains_startswith_endswith_do_not_match_nulls():
    # 回归：astype(str) 把 None/NaN 变成 "None"/"nan"，"包含 n" 曾命中空值行
    df = _text_df()
    _, idx = filter_engine.apply_filters(df, [{'col': 't', 'condition': '包含', 'value': 'n'}])
    assert idx == [0, 4]
    _, idx = filter_engine.apply_filters(df, [{'col': 't', 'condition': '开头是', 'value': 'n'}])
    assert idx == []
    _, idx = filter_engine.apply_filters(df, [{'col': 't', 'condition': '开头是', 'value': 'N'}])
    assert idx == [4]
    _, idx = filter_engine.apply_filters(df, [{'col': 't', 'condition': '结尾是', 'value': 'a'}])
    assert idx == [0]
    _, idx = filter_engine.apply_filters(df, [{'col': 'v', 'condition': '包含', 'value': 'a'}])
    assert idx == []
    assert not filter_engine.last_errors


def test_empty_and_not_empty_treat_whitespace_and_nulls():
    df = pd.DataFrame({'t': [' ', None, 'x', np.nan, '']})
    _, idx = filter_engine.apply_filters(df, [{'col': 't', 'condition': '为空', 'value': ''}])
    assert idx == [0, 1, 3, 4]
    _, idx = filter_engine.apply_filters(df, [{'col': 't', 'condition': '不为空', 'value': ''}])
    assert idx == [2]


def test_display_text_dates_match_grid():
    # 回归：表格显示 2024-01-01，筛选文本却是 2024-01-01 00:00:00，"等于" 找不到
    s = pd.Series(pd.to_datetime(['2024-01-01', '2024-01-01 08:30:00', None], format='ISO8601'))
    assert filter_engine.display_text(s).tolist() == ['2024-01-01', '2024-01-01 08:30:00', '']
    df = pd.DataFrame({'d': s})
    _, idx = filter_engine.apply_filters(df, [{'col': 'd', 'condition': '等于', 'value': '2024-01-01'}])
    assert idx == [0]
    assert filter_engine.value_counts(s) == [('2024-01-01', 1), ('2024-01-01 08:30:00', 1), ('', 1)]


@pytest.mark.parametrize('series', [
    pd.Series([0.1, 0.1 + 0.2, 1e16, 1e300, 10.0, -0.0, 123456789.123, 1.5e-7,
               float('inf'), 1e15, 999999999999999.0, np.nan, -3.0]),
    pd.Series([1, -2, 300], dtype='int64'),
    pd.Series([True, False]),
    pd.Series(['a', None, 10.0, 2.5, np.nan, 7, pd.Timestamp('2024-05-01'),
               pd.Timestamp('2024-05-01 01:02:03'), pd.NaT, [1, 2]], dtype=object),
    pd.Series(pd.to_datetime(['2024-01-01', '2024-01-01 08:30:00', None, '1999-12-31 23:59:59'], format='ISO8601')),
    pd.Series(['x', None], dtype='string'),
    pd.Series([1, None], dtype='Int64'),
    pd.Series([], dtype='float64'),
], ids=['float', 'int', 'bool', 'object', 'datetime', 'string', 'Int64', 'empty'])
def test_display_text_equals_grid_format_cell(series):
    """display_text 是筛选/查找的唯一事实来源：必须与表格模型逐格显示完全一致。"""
    from qtui.pandas_model import _format_cell
    if pd.api.types.is_datetime64_any_dtype(series.dtype):
        expected = [_format_cell(v) for v in series.astype(object)]
    else:
        expected = [_format_cell(v) for v in series.to_numpy(dtype=object)]
    got = filter_engine.display_text(series)
    assert got.tolist() == expected
    assert list(got.index) == list(series.index)


def test_multiple_filters_are_and_and_slice_once():
    df = pd.DataFrame({'a': [1, 2, 3, 4, 5, 6], 'b': ['x', 'y', 'x', 'y', 'x', 'y']},
                      index=[10, 11, 12, 13, 14, 15])
    out, idx = filter_engine.apply_filters(df, [
        {'col': 'a', 'condition': '大于', 'value': '1'},
        {'col': 'b', 'condition': '等于', 'value': 'x'},
        {'col': 'a', 'condition': '小于', 'value': '6'},
    ])
    assert idx == [12, 14]
    assert out['a'].tolist() == [3, 5] and list(out.index) == [0, 1]
    # 出错的条件被跳过、其余仍生效
    out, idx = filter_engine.apply_filters(df, [
        {'col': 'a', 'condition': '大于', 'value': 'abc'},
        {'col': 'b', 'condition': '等于', 'value': 'y'},
    ])
    assert idx == [11, 13, 15] and len(filter_engine.last_errors) == 1


def test_no_filters_returns_independent_copy():
    df = pd.DataFrame({'a': [1, 2]})
    out, idx = filter_engine.apply_filters(df, [])
    assert idx == [0, 1]
    out.iloc[0, 0] = 99
    assert df.iloc[0, 0] == 1


def test_equals_on_object_numeric_column():
    df = pd.DataFrame({'v': pd.Series([10.0, '20', None], dtype=object)})
    _, idx = filter_engine.apply_filters(df, [{'col': 'v', 'condition': '等于', 'value': '20'}])
    assert idx == [1]
    _, idx = filter_engine.apply_filters(df, [{'col': 'v', 'condition': '不等于', 'value': '20'}])
    assert idx == [0, 2]
