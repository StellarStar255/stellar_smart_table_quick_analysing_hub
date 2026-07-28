#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轻量级中英文国际化。

用法：
    from qtui.i18n import tr
    label = tr("打开文件")          # zh 环境原样返回；en 环境返回英文
    msg = tr("已删除 {} 行").format(n)   # 带参数的字符串用 {} 占位

翻译字典见 qtui/translations.py（key 为中文原文，value 为英文）。
语言偏好存于 QSettings；未设置时跟随系统语言。
切换语言需重启应用生效（启动时确定语言，避免运行时全量刷新 UI）。
"""

from PyQt6.QtCore import QLocale, QSettings

from qtui.translations import TRANSLATIONS

LANG_ZH = "zh"
LANG_EN = "en"


def _settings():
    return QSettings("SmartTableHub", "SmartTableHubQt")


def current_language():
    lang = _settings().value("ui/language")
    if lang in (LANG_ZH, LANG_EN):
        return lang
    return LANG_ZH if QLocale.system().name().startswith("zh") else LANG_EN


def set_language(lang):
    _settings().setValue("ui/language", lang)


# 启动时确定，进程生命周期内不变
_ACTIVE = current_language()


def tr(text):
    """中文原文 → 当前语言文本。缺失翻译时原样返回中文。"""
    if _ACTIVE == LANG_EN:
        return TRANSLATIONS.get(text, text)
    return text
