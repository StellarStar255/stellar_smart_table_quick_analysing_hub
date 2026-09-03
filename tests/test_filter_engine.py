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
