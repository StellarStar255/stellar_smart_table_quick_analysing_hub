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


def apply_filters(original_df: pd.DataFrame, active_filters: list):
    """从原始数据依次应用所有筛选条件。

    返回 (filtered_df, filtered_to_original_idx)：
    filtered_df 已 reset_index；idx 列表把筛选后行位置映射回原始索引，
    以便编辑时能同步更新 original_df。
    """
    filtered_df = original_df.copy()

    for filter_info in active_filters:
        col = filter_info['col']
        condition = filter_info['condition']
        value = filter_info['value']
        if col not in filtered_df.columns:
            continue
        try:
            if condition == '等于':
                filtered_df = filtered_df[filtered_df[col].astype(str) == value]
            elif condition == '包含':
                filtered_df = filtered_df[filtered_df[col].astype(str).str.contains(value, case=False, na=False)]
            elif condition == '大于':
                filtered_df = filtered_df[pd.to_numeric(filtered_df[col], errors='coerce') > float(value)]
            elif condition == '小于':
                filtered_df = filtered_df[pd.to_numeric(filtered_df[col], errors='coerce') < float(value)]
            elif condition == '不等于':
                filtered_df = filtered_df[filtered_df[col].astype(str) != value]
            elif condition == '值在列表中':
                filtered_df = filtered_df[filtered_df[col].astype(str).isin(value)]
            elif condition == '开头是':
                filtered_df = filtered_df[filtered_df[col].astype(str).str.startswith(value, na=False)]
            elif condition == '结尾是':
                filtered_df = filtered_df[filtered_df[col].astype(str).str.endswith(value, na=False)]
            elif condition == '为空':
                filtered_df = filtered_df[filtered_df[col].isna() | (filtered_df[col].astype(str).str.strip() == '')]
            elif condition == '不为空':
                filtered_df = filtered_df[filtered_df[col].notna() & (filtered_df[col].astype(str).str.strip() != '')]
        except Exception as e:
            print(f"应用筛选条件失败: {e}")
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
