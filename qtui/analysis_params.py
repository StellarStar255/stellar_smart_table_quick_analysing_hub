# -*- coding: utf-8 -*-
"""Python 分析预设的「参数块」解析/回填，以及列名相关的友好报错提示。

预设约定：代码开头有一段参数块，形如::

    # ===== 参数：把引号里的列名改成你的 =====
    ROW_DIM = '行维度'     # 透视表的行
    TOP_N = 10             # 取前几行
    # =====================================

两条 ``# ====`` 围栏之间、形如 ``大写常量 = 字面量 [# 说明]`` 的行都算参数。
分析窗口据此渲染表单；用户改表单时只重写对应行的字面量，注释与其余代码
原样保留，所以表单和代码编辑器可以双向同步。

纯逻辑模块，不依赖 Qt，便于单测。
"""

import ast
import difflib
import re

_FENCE_RE = re.compile(r'^\s*#\s*={3,}')
_ASSIGN_RE = re.compile(
    r'^(?P<indent>\s*)(?P<name>[A-Z][A-Z0-9_]*)\s*=\s*'
    r'(?P<value>.+?)(?P<comment>\s+#.*)?\s*$')

# 参数类型（决定表单控件）
KIND_COLUMN = "column"     # 单个列名：下拉选列
KIND_COLUMNS = "columns"   # 列名列表或 None：多选
KIND_NUMBER = "number"     # int / float
KIND_BOOL = "bool"
KIND_TEXT = "text"         # 其他字符串
KIND_OTHER = "other"       # 其余字面量（原样编辑）


class Param:
    __slots__ = ("name", "value", "comment", "line_no", "kind")

    def __init__(self, name, value, comment, line_no, kind):
        self.name = name
        self.value = value
        self.comment = comment      # 去掉 "#" 后的说明文字，可能为空
        self.line_no = line_no      # 0-based 行号
        self.kind = kind

    def __repr__(self):
        return f"Param({self.name}={self.value!r}, {self.kind})"


def _kind_of(name, value):
    if name.endswith("_COLS"):
        return KIND_COLUMNS
    if isinstance(value, bool):
        return KIND_BOOL
    if isinstance(value, (int, float)):
        return KIND_NUMBER
    if isinstance(value, str):
        if name.endswith(("_COL", "_DIM")):
            return KIND_COLUMN
        return KIND_TEXT
    return KIND_OTHER


def _split_value(text):
    """把「值 # 注释」拆开：注释是第一个不在引号里的 # 之后的部分。"""
    quote = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#":
            return text[:i].rstrip(), text[i:]
    return text.rstrip(), ""


def _parse_line(line, line_no):
    m = _ASSIGN_RE.match(line)
    if not m:
        return None
    # 正则的 comment 分组对含 # 的字符串不可靠，按引号重新切一次
    rest = line[len(m.group("indent")) + len(m.group("name")):]
    rest = rest.split("=", 1)[1]
    value_text, comment = _split_value(rest.strip())
    try:
        value = ast.literal_eval(value_text)
    except (ValueError, SyntaxError):
        return None
    comment_text = comment.lstrip("#").strip()
    return Param(m.group("name"), value, comment_text, line_no,
                 _kind_of(m.group("name"), value))


def _block_range(lines):
    """返回参数块 (起始行, 结束行) 的开区间；找不到围栏返回 None。

    只认代码开头的围栏：第一条围栏之前只允许空行和注释。
    """
    start = None
    for i, line in enumerate(lines):
        if _FENCE_RE.match(line):
            start = i
            break
        if line.strip() and not line.lstrip().startswith("#"):
            return None
    if start is None:
        return None
    for j in range(start + 1, len(lines)):
        if _FENCE_RE.match(lines[j]):
            return start + 1, j
    return None


def parse_params(code):
    """解析参数块，返回 [Param]（保持出现顺序）。没有参数块返回 []。"""
    lines = code.split("\n")
    rng = _block_range(lines)
    if rng is None:
        return []
    params = []
    for i in range(*rng):
        p = _parse_line(lines[i], i)
        if p is not None:
            params.append(p)
    return params


def set_param(code, name, value):
    """把参数 ``name`` 的字面量改成 ``value``，其余内容原样保留。

    找不到该参数时原样返回。
    """
    lines = code.split("\n")
    for p in parse_params(code):
        if p.name != name:
            continue
        line = lines[p.line_no]
        m = _ASSIGN_RE.match(line)
        indent = m.group("indent")
        rest = line[len(indent) + len(name):].split("=", 1)[1]
        _old, comment = _split_value(rest.strip())
        new_line = f"{indent}{name} = {value!r}"
        if comment:
            # 保留原来值与 # 之间的空格数，注释尽量不跳位
            new_line += " " * _comment_gap(line) + comment
        lines[p.line_no] = new_line
        break
    return "\n".join(lines)


def _comment_gap(line):
    """原行里值与 # 之间有几个空格（用于回填时保持对齐）。"""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and i > 0:
            j = i
            while j > 0 and line[j - 1] == " ":
                j -= 1
            return i - j
    return 1


# ---------------------------------------------------------------------------
# 列名相关的友好报错
# ---------------------------------------------------------------------------

# 单引号与双引号分开匹配：KeyError 的 str() 会把参数再 repr 一层，
# 如 "['x'] not in index"，混用一个正则会抓到 "[' 这种碎片
_QUOTED_RES = (re.compile(r"'([^']+)'"), re.compile(r'"([^"]+)"'))
_PANDAS_MISSING_RE = re.compile(
    r"not in index|do not exist|not found in axis|not in list|"
    r"None of \[.*\] are in the", re.IGNORECASE)


def _candidate_names(exc):
    names = []
    for a in getattr(exc, "args", ()):
        if isinstance(a, str):
            names.append(a)
        elif isinstance(a, (list, tuple)):
            names.extend(x for x in a if isinstance(x, str))
    text = str(exc)
    for rx in _QUOTED_RES:
        names.extend(rx.findall(text))
    seen, out = set(), []
    for n in names:
        n = n.strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def column_hint(exc, columns, tr=lambda s: s, max_list=30):
    """用户代码因列名不存在而报错时，生成一段提示；不相关的异常返回 None。

    典型触发：``KeyError: '分组列'``、``KeyError: "['x'] not in index"``。
    """
    if not (isinstance(exc, KeyError) or _PANDAS_MISSING_RE.search(str(exc))):
        return None
    cols = [str(c) for c in columns]
    if not cols:
        return None
    col_set = set(cols)
    missing = [n for n in _candidate_names(exc)
               if n not in col_set and not _PANDAS_MISSING_RE.search(n)
               and len(n) <= 100]
    if not missing:
        return None
    lines = []
    for n in missing[:5]:
        close = difflib.get_close_matches(n, cols, n=3, cutoff=0.4)
        msg = tr("列 '{}' 不存在").format(n)
        if close:
            msg += tr("，你是不是想要 {}？").format(
                " / ".join(f"'{c}'" for c in close))
        lines.append(msg)
    shown = cols[:max_list]
    listing = ", ".join(f"'{c}'" for c in shown)
    if len(cols) > max_list:
        listing += tr(" …（共 {} 列）").format(len(cols))
    lines.append(tr("当前可用列: {}").format(listing))
    return "\n".join(lines)
