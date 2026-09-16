# -*- coding: utf-8 -*-
"""
筛选引擎 - 从 mixins/filter_mixin.py 提取的纯 pandas 逻辑。

筛选条件为 dict: {'col': 列名, 'condition': 条件, 'value': 值, 'display': 可选描述}
多条件之间为 AND 关系，依次应用。条件名与 Tkinter 版完全一致。
"""

import numpy as np
import pandas as pd

from qtui.i18n import tr

# 内部标识符（同时是中文界面文案）；界面显示处用 tr() 翻译，内部值保持不变
CONDITIONS = [
    '等于', '包含', '大于', '小于', '不等于',
    '开头是', '结尾是', '为空', '不为空',
]


# 最近一次 apply_filters 中被跳过的条件及原因 [(filter_info, 错误文本), ...]
# 签名保持不变（宿主只取 (df, idx_map)），出错条件通过这里暴露而不是静默丢弃
last_errors = []


def _numeric_values(series: pd.Series):
    """列可按数值比较时返回其数值形式（Series），否则返回 None。

    数值 dtype 直接返回；object 列里非空值全是数字（如公式结果混入后整列
    退化为 object）也按数值比较。只做一次 to_numeric，结果供比较复用。
    """
    if pd.api.types.is_bool_dtype(series.dtype):
        return None
    if pd.api.types.is_numeric_dtype(series.dtype):
        return series
    if pd.api.types.is_datetime64_any_dtype(series.dtype):
        return None
    numeric = pd.to_numeric(series, errors='coerce')
    if numeric.notna().sum() == 0:
        return None
    if (numeric.isna() & series.notna()).any():
        return None
    return numeric


def _is_numeric_column(series: pd.Series) -> bool:
    return _numeric_values(series) is not None


_scalar_formatter = None


def _format_scalar_func():
    """单值格式化函数：直接复用表格模型的 pandas_model._format_cell
    （单一事实来源）；模型不可导入时（纯 pandas 环境）用等价的本地实现兜底。
    只解析一次，避免 map 时逐元素 import。
    """
    global _scalar_formatter
    if _scalar_formatter is None:
        try:
            from qtui.pandas_model import _format_cell
        except Exception:   # 没装 Qt 时也能用
            _format_cell = _format_cell_fallback
        _scalar_formatter = _format_cell
    return _scalar_formatter


def _format_cell_fallback(value) -> str:
    import datetime
    if type(value) is str:
        return value
    if value is None:
        return ""
    if isinstance(value, float):
        if value != value:
            return ""
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return repr(float(value))
    if isinstance(value, (list, tuple, np.ndarray)):
        return str(value)
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return str(value)
    if isinstance(value, np.floating):
        return _format_cell_fallback(float(value))
    if isinstance(value, datetime.datetime):
        if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
            return value.date().isoformat()
        return value.strftime('%Y-%m-%d %H:%M:%S')
    return str(value)


def display_text(series: pd.Series) -> pd.Series:
    """与表格显示完全一致的文本（筛选/查找的唯一事实来源）。

    整数值的浮点不带 .0（10.0 显示为 10）；日期列显示 2024-01-01，带时间
    才显示 2024-01-01 08:30:00；缺失值为空串。数值/日期列走向量化路径，
    其余逐元素套用表格模型的格式化函数。
    """
    dtype = series.dtype
    index = series.index
    if pd.api.types.is_bool_dtype(dtype) and not isinstance(dtype, pd.BooleanDtype):
        return series.astype(str)
    if pd.api.types.is_float_dtype(dtype) and isinstance(dtype, np.dtype):
        arr = series.to_numpy(dtype=np.float64)
        notna = ~np.isnan(arr)
        with np.errstate(invalid='ignore'):
            is_int = notna & (np.abs(arr) < 1e15) & (arr == np.floor(arr))
        out = arr.astype(str).astype(object)
        out[~notna] = ''
        if is_int.any():
            out[is_int] = arr[is_int].astype(np.int64).astype(str)
        return pd.Series(out, index=index, dtype=object)
    if pd.api.types.is_integer_dtype(dtype) and isinstance(dtype, np.dtype):
        return series.astype(str)
    if pd.api.types.is_datetime64_any_dtype(dtype):
        notna = series.notna().to_numpy()
        out = np.full(len(series), '', dtype=object)
        if notna.any():
            dt = series.dt
            full = dt.strftime('%Y-%m-%d %H:%M:%S').to_numpy(dtype=object)
            midnight = ((dt.hour == 0) & (dt.minute == 0) & (dt.second == 0)
                        & (dt.microsecond == 0) & (dt.nanosecond == 0)).to_numpy()
            short = dt.strftime('%Y-%m-%d').to_numpy(dtype=object)
            out = np.where(midnight, short, full)
            out[~notna] = ''
        return pd.Series(out, index=index, dtype=object)
    if isinstance(dtype, pd.StringDtype):
        return series.astype(object).where(series.notna(), '').astype(str)
    return series.map(_format_scalar_func())


_display_text = display_text     # 旧名保留


def value_counts(series: pd.Series):
    """列里的去重值及出现次数，按显示文本给出：[(文本, 次数), ...]。

    数值列按数值排序（"10" 排在 "9" 后面），其余按文本排序；空值归为 ""。
    """
    texts = display_text(series)
    counts = texts.value_counts()
    items = list(counts.items())
    if _is_numeric_column(series):
        def key(item):
            try:
                return (0, float(item[0]), "")
            except ValueError:
                return (1, 0.0, item[0])     # 空串/非数值排在数值后面
    else:
        def key(item):
            return (item[0] == "", item[0])  # 空白排最后
    items.sort(key=key)
    return items


def _equals_mask(series: pd.Series, value) -> pd.Series:
    """"等于"：数值列按数值比较（10 匹配 10.0），否则按显示文本比较。"""
    numeric = _numeric_values(series)
    if numeric is not None:
        try:
            target = float(str(value).strip())
        except ValueError:
            target = None
        if target is not None:
            return numeric == target
    return display_text(series) == str(value)


def _to_float(value, condition):
    try:
        return float(str(value).strip())
    except ValueError:
        raise ValueError('{}: "{}"'.format(tr('大于/小于 条件需要数值'), value))


def _condition_mask(series: pd.Series, condition, value):
    """单个条件在原始列上的布尔掩码；未知条件返回 None。"""
    if condition == '等于':
        return _equals_mask(series, value)
    if condition == '不等于':
        return ~_equals_mask(series, value)
    if condition == '包含':
        # 用户输入按字面匹配，不是正则（"a.b" 不该匹配 "axb"，"c(1" 不该报错）；
        # 文本用显示文本（缺失值为空串），None/NaN 不会因 "nan"/"None" 被误匹配
        return display_text(series).str.contains(str(value), case=False,
                                                 regex=False, na=False)
    if condition == '大于':
        return pd.to_numeric(series, errors='coerce') > _to_float(value, condition)
    if condition == '小于':
        return pd.to_numeric(series, errors='coerce') < _to_float(value, condition)
    if condition == '值在列表中':
        # 按"显示文本"匹配，与列头筛选弹层里勾选的文本一致
        # （10.0 显示为 10，空值显示为空串）
        return display_text(series).isin(list(value))
    if condition == '开头是':
        return display_text(series).str.startswith(str(value), na=False)
    if condition == '结尾是':
        return display_text(series).str.endswith(str(value), na=False)
    if condition == '为空':
        return display_text(series).str.strip() == ''
    if condition == '不为空':
        return display_text(series).str.strip() != ''
    return None


def apply_filters(original_df: pd.DataFrame, active_filters: list):
    """从原始数据依次应用所有筛选条件。

    返回 (filtered_df, filtered_to_original_idx)：
    filtered_df 已 reset_index；idx 列表把筛选后行位置映射回原始索引，
    以便编辑时能同步更新 original_df。

    所有条件先在原始列上算出掩码、按 AND 合并，最后只切片一次
    （不再逐条件复制整表）。无法应用的条件（如"大于 abc"）跳过并记入
    模块级 last_errors，宿主可据此提示用户，而不是让筛选标签显示着却没生效。
    """
    last_errors.clear()
    mask = None

    for filter_info in active_filters:
        col = filter_info['col']
        condition = filter_info['condition']
        value = filter_info['value']
        if col not in original_df.columns:
            continue
        series = original_df[col]
        try:
            m = _condition_mask(series, condition, value)
        except Exception as e:
            last_errors.append((filter_info, str(e)))
            continue
        if m is None:
            continue
        m = np.asarray(m, dtype=bool)
        mask = m if mask is None else (mask & m)

    if mask is None:
        filtered_df = original_df.copy()
    else:
        filtered_df = original_df[mask]
    idx_map = list(filtered_df.index)
    return filtered_df.reset_index(drop=True), idx_map


def describe_filter(filter_info) -> str:
    """生成筛选标签的显示文本。"""
    if filter_info.get('display'):
        return filter_info['display']
    col = filter_info['col']
    condition = filter_info['condition']
    value = filter_info['value']
    if condition == '值在列表中':
        n = len(value)
        preview = ', '.join(str(v) for v in list(value)[:3])
        if n > 3:
            preview += tr('... ({}项)').format(n)
        return f"{col}: {preview}"
    if condition in ('为空', '不为空'):
        return f"{col} {tr(condition)}"
    return f"{col} {tr(condition)} {value}"
