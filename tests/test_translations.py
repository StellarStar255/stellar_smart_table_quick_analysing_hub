# -*- coding: utf-8 -*-
"""翻译完整性审计：代码里每个 tr("字面量") 都必须有英文条目，且占位符数量一致。

缺失条目会让英文界面混入中文；占位符不一致会让 .format() 抛异常。
"""
import ast
import os
import re

import pytest

from qtui.translations import TRANSLATIONS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = ("qtui", "core")
SCAN_FILES = ("smart_table_quick_analysing_hub.py",)
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def _iter_source_files():
    for d in SCAN_DIRS:
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, d)):
            for f in files:
                if f.endswith(".py"):
                    yield os.path.join(dirpath, f)
    for f in SCAN_FILES:
        yield os.path.join(ROOT, f)


def collect_tr_literals():
    """返回 [(文件, 行号, 字面量)]，只统计 tr("...") 形式的直接字面量调用。"""
    found = []
    for path in _iter_source_files():
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            if name != "tr" or len(node.args) != 1:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.append((os.path.relpath(path, ROOT), node.lineno, arg.value))
    return found


def test_every_tr_literal_has_english_entry():
    missing = sorted({(f, ln, key) for f, ln, key in collect_tr_literals()
                      if key not in TRANSLATIONS})
    assert not missing, "缺少英文翻译:\n" + "\n".join(
        f"  {f}:{ln}  {key!r}" for f, ln, key in missing)


def test_placeholder_counts_match():
    bad = []
    for key, val in TRANSLATIONS.items():
        if len(_PLACEHOLDER.findall(key)) != len(_PLACEHOLDER.findall(val)):
            bad.append((key, val))
    assert not bad, "占位符数量不一致:\n" + "\n".join(
        f"  {k!r} -> {v!r}" for k, v in bad)


def test_translation_values_are_nonempty_strings():
    empty = [k for k, v in TRANSLATIONS.items() if not isinstance(v, str) or not v.strip()]
    assert not empty, f"空翻译: {empty}"


@pytest.mark.parametrize("key", ["；"])
def test_joiner_keys(key):
    # 拼接用的分隔符也要有英文形式
    assert key in TRANSLATIONS
