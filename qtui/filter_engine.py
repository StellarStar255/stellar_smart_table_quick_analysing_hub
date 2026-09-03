# -*- coding: utf-8 -*-
"""
筛选引擎 - 从 mixins/filter_mixin.py 提取的纯 pandas 逻辑。

筛选条件为 dict: {'col': 列名, 'condition': 条件, 'value': 值, 'display': 可选描述}
多条件之间为 AND 关系，依次应用。条件名与 Tkinter 版完全一致。
"""

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


def _is_numeric_column(series: pd.Series) -> bool:
    if pd.api.types.is_bool_dtype(series.dtype):
        return False
    if pd.api.types.is_numeric_dtype(series.dtype):
        return True
    # object 列里全是数字（如公式结果混入后整列退化为 object）也按数值比较
    non_null = series.dropna()
    if non_null.empty:
        return False
    return pd.to_numeric(non_null, errors='coerce').notna().all()


def display_text(series: pd.Series) -> pd.Series:
    """与表格显示一致的文本：整数值的浮点不带 .0（10.0 显示为 10）；缺失值为空串。"""
    def fmt(v):
        if isinstance(v, float):
            if v != v:
                return ''
            if v.is_integer() and abs(v) < 1e15:
                return str(int(v))
        return '' if v is None else str(v)
    return series.map(fmt)


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
    if _is_numeric_column(series):
        try:
            target = float(str(value).strip())
        except ValueError:
            target = None
        if target is not None:
            return pd.to_numeric(series, errors='coerce') == target
    return display_text(series) == str(value)


def _to_float(value, condition):
    try:
        return float(str(value).strip())
    except ValueError:
        raise ValueError('{}: "{}"'.format(tr('大于/小于 条件需要数值'), value))


def apply_filters(original_df: pd.DataFrame, active_filters: list):
    """从原始数据依次应用所有筛选条件。

    返回 (filtered_df, filtered_to_original_idx)：
    filtered_df 已 reset_index；idx 列表把筛选后行位置映射回原始索引，
    以便编辑时能同步更新 original_df。

    无法应用的条件（如"大于 abc"）跳过并记入模块级 last_errors，
    宿主可据此提示用户，而不是让筛选标签显示着却没生效。
    """
    filtered_df = original_df.copy()
    last_errors.clear()

    for filter_info in active_filters:
        col = filter_info['col']
        condition = filter_info['condition']
        value = filter_info['value']
        if col not in filtered_df.columns:
            continue
        series = filtered_df[col]
        try:
            if condition == '等于':
                filtered_df = filtered_df[_equals_mask(series, value)]
            elif condition == '不等于':
                filtered_df = filtered_df[~_equals_mask(series, value)]
            elif condition == '包含':
                # 用户输入按字面匹配，不是正则（"a.b" 不该匹配 "axb"，"c(1" 不该报错）
                filtered_df = filtered_df[series.astype(str).str.contains(
                    str(value), case=False, regex=False, na=False)]
            elif condition == '大于':
                filtered_df = filtered_df[pd.to_numeric(series, errors='coerce') > _to_float(value, condition)]
            elif condition == '小于':
                filtered_df = filtered_df[pd.to_numeric(series, errors='coerce') < _to_float(value, condition)]
            elif condition == '值在列表中':
                # 按"显示文本"匹配，与列头筛选弹层里勾选的文本一致
                # （10.0 显示为 10，空值显示为空串）
                filtered_df = filtered_df[display_text(series).isin(list(value))]
            elif condition == '开头是':
                filtered_df = filtered_df[series.astype(str).str.startswith(str(value), na=False)]
            elif condition == '结尾是':
                filtered_df = filtered_df[series.astype(str).str.endswith(str(value), na=False)]
            elif condition == '为空':
                filtered_df = filtered_df[series.isna() | (series.astype(str).str.strip() == '')]
            elif condition == '不为空':
                filtered_df = filtered_df[series.notna() & (series.astype(str).str.strip() != '')]
        except Exception as e:
            last_errors.append((filter_info, str(e)))
            continue

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
