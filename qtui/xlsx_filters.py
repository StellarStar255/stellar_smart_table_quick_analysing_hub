# -*- coding: utf-8 -*-
"""筛选条件写进 xlsx 本身，随文件走（换电脑、发给别人、用 Excel 打开都在）。

存两份：
1. Excel 标准的 AutoFilter（+ 隐藏不满足条件的行）——Excel / WPS 打开能看到同样的筛选；
2. 本应用筛选条件的精确副本，存在文档自定义属性（docProps/custom.xml）里——
   AutoFilter 表达不了的组合（同列超过 2 个自定义条件等）也不丢。

精确副本旁边记一份"写入时 AutoFilter 的签名"。别人在 Excel 里改了筛选再保存，
自定义属性会被原样保留、但 AutoFilter 变了——签名对不上时以文件里的 AutoFilter
为准（精确副本已过期）。

读取直接扫 zip 里的 XML，不用 openpyxl 整本加载（大文件打开不能因此变慢）。
"""

import json
import re
import zipfile
import xml.etree.ElementTree as ET

PROP_PREFIX = "SmartTableHub.filters"
_PROP_CHUNK = 240          # Excel 界面里文本属性最长 255 字符，按块拆开存
_FORMAT_VERSION = 1

# 应用条件 -> Excel customFilter 运算符（值里的通配符另行转义/拼接）
_CUSTOM_OPS = {"等于": "equal", "不等于": "notEqual",
               "大于": "greaterThan", "小于": "lessThan"}


def _escape(text):
    """Excel 筛选值里 * ? ~ 是通配符，字面量要用 ~ 转义。"""
    return re.sub(r"([*?~])", r"~\1", str(text))


def _unescape(text):
    return re.sub(r"~([*?~])", r"\1", str(text))


def _custom_for(condition, value):
    """一个自定义条件 -> (运算符, 值)；不支持的返回 None。"""
    if condition in ("大于", "小于"):
        return _CUSTOM_OPS[condition], str(value)
    if condition in ("等于", "不等于"):
        return _CUSTOM_OPS[condition], _escape(value)
    if condition == "包含":
        return "equal", "*{}*".format(_escape(value))
    if condition == "开头是":
        return "equal", "{}*".format(_escape(value))
    if condition == "结尾是":
        return "equal", "*{}".format(_escape(value))
    if condition == "不为空":
        return "notEqual", " "        # Excel「非空白」的写法
    return None


def _normalize(spec):
    """规格规范化成"实际写进文件的样子"：列表筛选优先、自定义最多 2 个。

    写入与读取都过这一步，签名才可比。
    """
    out = {}
    for cid, entry in spec.items():
        values = entry.get("values")
        custom = [list(c) for c in entry.get("custom") or []]
        if values is not None:
            if not values and not entry.get("blank"):
                continue
            out[str(cid)] = {"values": sorted(set(values)),
                             "blank": bool(entry.get("blank"))}
        elif custom:
            custom = custom[:2]
            out[str(cid)] = {"custom": custom,
                             "and": len(custom) > 1 and bool(entry.get("and", True))}
    return out


def resolve_columns(filters, columns):
    """来自 Excel AutoFilter 的条件只有列号（col_index），按表头换成列名；越界的丢掉。"""
    names = [str(c) for c in columns]
    out = []
    for f in filters:
        f = dict(f)
        if "col_index" in f:
            idx = f.pop("col_index")
            if not isinstance(idx, int) or not 0 <= idx < len(names):
                continue
            f["col"] = names[idx]
        out.append(f)
    return out


def autofilter_spec(filters, columns):
    """应用的筛选条件 -> 规范化的 AutoFilter 规格 {"列号": {...}}。"""
    names = [str(c) for c in columns]
    spec = {}
    for f in filters:
        if f.get("col") not in names:
            continue
        entry = spec.setdefault(names.index(f["col"]),
                                {"values": None, "blank": False, "custom": [], "and": True})
        cond, value = f.get("condition"), f.get("value")
        if cond == "值在列表中":
            vals = [str(v) for v in (value or [])]
            entry["values"] = [v for v in vals if v != ""]
            entry["blank"] = "" in vals
        elif cond == "为空":
            if entry["values"] is None:
                entry["values"], entry["blank"] = [], True
        else:
            custom = _custom_for(cond, value)
            if custom is not None:
                entry["custom"].append(custom)
    return _normalize(spec)


def spec_signature(spec):
    return json.dumps(spec, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 写入（openpyxl 工作表 / 工作簿）
# ---------------------------------------------------------------------------

def _col_letter(idx):
    from openpyxl.utils import get_column_letter
    return get_column_letter(idx + 1)


def apply_to_worksheet(ws, spec, ncols, nrows, visible_positions,
                       unhide_when_cleared=False):
    """把规格写成 ws 的 AutoFilter，并按 visible_positions 隐藏其余数据行。

    spec 为空 = 去掉筛选；unhide_when_cleared 时顺带取消数据行的隐藏
    （原先的隐藏是旧筛选造成的，只在原来有 AutoFilter 时才这么做，
    不动用户手工隐藏的行）。
    """
    from openpyxl.worksheet.filters import (
        AutoFilter, CustomFilter, CustomFilters, FilterColumn, Filters,
    )
    if not spec or not ncols:
        ws.auto_filter = AutoFilter()
        ws.sheet_properties.filterMode = None
        if unhide_when_cleared:
            for row, dim in list(ws.row_dimensions.items()):
                if 2 <= row <= nrows + 1 and dim.hidden:
                    dim.hidden = False
        return
    columns = []
    for cid in sorted(spec, key=int):
        entry = spec[cid]
        if "values" in entry:
            fc = FilterColumn(colId=int(cid), filters=Filters(
                filter=list(entry["values"]), blank=True if entry["blank"] else None))
        else:
            fc = FilterColumn(colId=int(cid), customFilters=CustomFilters(
                _and=True if entry["and"] else None,
                customFilter=[CustomFilter(operator=op, val=val)
                              for op, val in entry["custom"]]))
        columns.append(fc)
    ws.auto_filter = AutoFilter(
        ref="A1:{}{}".format(_col_letter(ncols - 1), max(nrows, 1) + 1),
        filterColumn=columns)
    # 行隐藏：先撤掉数据区里原有的隐藏，再按这次的可见行设置
    visible = set(visible_positions)
    for row, dim in list(ws.row_dimensions.items()):
        if 2 <= row <= nrows + 1 and dim.hidden:
            dim.hidden = False
    for pos in range(nrows):
        if pos not in visible:
            ws.row_dimensions[pos + 2].hidden = True
    # 有行被筛掉时 Excel 要求 sheetPr filterMode="1"，列头才显示为"已筛选"
    ws.sheet_properties.filterMode = True if len(visible) < nrows else None


def write_properties(wb, sheets_state):
    """把 {sheet 名: {"filters": [...], "sig": ...}} 写进自定义文档属性（覆盖旧值）。"""
    from openpyxl.packaging.custom import StringProperty
    props = wb.custom_doc_props
    for name in [n for n in props.names if n.startswith(PROP_PREFIX)]:
        del props[name]
    state = {n: s for n, s in sheets_state.items() if s.get("filters")}
    if not state:
        return
    text = json.dumps({"v": _FORMAT_VERSION, "sheets": state}, ensure_ascii=False)
    chunks = [text[i:i + _PROP_CHUNK] for i in range(0, len(text), _PROP_CHUNK)]
    props.append(StringProperty(name=PROP_PREFIX + ".count", value=str(len(chunks))))
    for i, chunk in enumerate(chunks):
        props.append(StringProperty(name="{}.{:04d}".format(PROP_PREFIX, i), value=chunk))


def read_properties_from_workbook(wb):
    """openpyxl 工作簿里已有的精确副本 {sheet 名: {...}}（补丁保存时合并用）。"""
    values = {p.name: p.value for p in wb.custom_doc_props
              if p.name.startswith(PROP_PREFIX)}
    return _decode_properties(values)


def _decode_properties(values):
    try:
        count = int(values.get(PROP_PREFIX + ".count", 0))
        text = "".join(values["{}.{:04d}".format(PROP_PREFIX, i)] for i in range(count))
        data = json.loads(text) if text else {}
    except (KeyError, ValueError, TypeError):
        return {}
    sheets = data.get("sheets") if isinstance(data, dict) else None
    return sheets if isinstance(sheets, dict) else {}


# ---------------------------------------------------------------------------
# 读取（直接扫 zip）
# ---------------------------------------------------------------------------

_NS_PREFIX = re.compile(rb"<(/?)[A-Za-z_][\w.-]*:")
# 自闭合 <autoFilter .../> 或带子元素 <autoFilter ...>…</autoFilter>
# （不能用一个非贪婪 .*?/> 通吃：会停在第一个 <filter val="x"/> 上）
_AUTOFILTER = re.compile(
    rb"<(?:[A-Za-z_][\w.-]*:)?autoFilter\b[^>]*?/>"
    rb"|<(?:[A-Za-z_][\w.-]*:)?autoFilter\b[^>]*>.*?</(?:[A-Za-z_][\w.-]*:)?autoFilter>",
    re.S)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _sheet_paths(z):
    """sheet 名 -> zip 内工作表 XML 路径。"""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {}
    for rel in rels:
        target = rel.get("Target", "")
        target = target.lstrip("/") if target.startswith("/") else "xl/" + target
        targets[rel.get("Id")] = target
    out = {}
    for el in wb.iter():
        if _local(el.tag) != "sheet":
            continue
        rid = next((v for k, v in el.attrib.items() if _local(k) == "id"), None)
        if rid in targets:
            out[el.get("name")] = targets[rid]
    return out


def _parse_autofilter(xml_bytes):
    """工作表 XML 里的 AutoFilter -> (规范化规格, 是否有表达不了的条件)；没有返回 (None, False)。"""
    m = _AUTOFILTER.search(xml_bytes)
    if not m:
        return None, False
    frag = _NS_PREFIX.sub(rb"<\1", m.group(0))
    frag = re.sub(rb"\sxmlns(:\w+)?=\"[^\"]*\"", b"", frag)
    frag = re.sub(rb"\s[A-Za-z_][\w.-]*:(\w+=)", rb" \1", frag)
    try:
        root = ET.fromstring(frag)
    except ET.ParseError:
        return None, True
    ref = root.get("ref") or ""
    start = re.match(r"([A-Z]+)(\d+)", ref)
    if not start or start.group(2) != "1":
        return None, bool(len(root))       # 表头不在第 1 行：应用不认这个筛选
    from openpyxl.utils import column_index_from_string
    offset = column_index_from_string(start.group(1)) - 1
    spec, unsupported = {}, False
    for fc in root:
        if _local(fc.tag) != "filterColumn":
            continue
        cid = int(fc.get("colId", 0)) + offset
        entry = {"values": None, "blank": False, "custom": [], "and": False}
        for child in fc:
            kind = _local(child.tag)
            if kind == "filters":
                if any(_local(g.tag) == "dateGroupItem" for g in child):
                    unsupported = True
                entry["values"] = [g.get("val", "") for g in child
                                   if _local(g.tag) == "filter"]
                entry["blank"] = child.get("blank") in ("1", "true")
            elif kind == "customFilters":
                entry["and"] = child.get("and") in ("1", "true")
                entry["custom"] = [[c.get("operator", "equal"), c.get("val", "")]
                                   for c in child if _local(c.tag) == "customFilter"]
            else:
                unsupported = True             # 颜色/前 10 项/动态日期等
        spec[cid] = entry
    return _normalize(spec), unsupported


def spec_to_filters(spec):
    """AutoFilter 规格 -> 应用的筛选条件（列用 col_index，待拿到表头后解析成列名）。

    或（OR）组合、≥/≤ 等应用表达不了的条件跳过，返回 (条件, 跳过数)。
    """
    filters, skipped = [], 0
    for cid, entry in sorted(spec.items(), key=lambda kv: int(kv[0])):
        cid = int(cid)
        if "values" in entry:
            if entry["values"]:
                filters.append({"col_index": cid, "condition": "值在列表中",
                                "value": list(entry["values"]) + ([""] if entry["blank"] else [])})
            elif entry["blank"]:
                filters.append({"col_index": cid, "condition": "为空", "value": ""})
            continue
        custom = entry.get("custom") or []
        if len(custom) > 1 and not entry.get("and"):
            skipped += 1
            continue
        for op, val in custom:
            cond = None
            if op == "notEqual" and val == " ":
                cond, val = "不为空", ""
            elif op == "notEqual":
                cond, val = "不等于", _unescape(val)
            elif op in ("greaterThan", "lessThan"):
                cond = "大于" if op == "greaterThan" else "小于"
            elif op == "equal":
                raw = val
                starts = raw.startswith("*")
                ends = raw.endswith("*") and not raw.endswith("~*")
                inner = raw[1 if starts else 0: len(raw) - (1 if ends else 0)]
                if "*" in re.sub(r"~.", "", inner) or "?" in re.sub(r"~.", "", inner):
                    cond = None                 # 中间还有通配符：应用表达不了
                elif starts and ends and inner:
                    cond = "包含"
                elif ends and inner:
                    cond = "开头是"
                elif starts and inner:
                    cond = "结尾是"
                else:
                    cond = "等于"
                val = _unescape(inner) if cond else val
            if cond is None:
                skipped += 1
                continue
            filters.append({"col_index": cid, "condition": cond, "value": val})
    return filters, skipped


def read_saved_filters(path, sheets=None):
    """读出 xlsx 里保存的筛选 {sheet 名: [条件, ...]}；文件里完全没有筛选信息时返回 None。

    sheets 只扫这些 sheet（打开文件时只扫当前 sheet，其余切过去再扫——扫一张
    4 万行的表要零点几秒）。条件里可能带 col_index（来自 Excel 的 AutoFilter），
    由调用方按表头解析成列名。精确副本的签名与文件当前的 AutoFilter 一致才用
    精确副本，否则以 AutoFilter 为准；精确副本在、AutoFilter 却没了（别人在 Excel
    里清了筛选）记为 []。
    """
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            props = {}
            if "docProps/custom.xml" in names:
                root = ET.fromstring(z.read("docProps/custom.xml"))
                for prop in root:
                    name = prop.get("name", "")
                    if name.startswith(PROP_PREFIX) and len(prop):
                        props[name] = prop[0].text or ""
            exact = _decode_properties(props) if props else {}
            result = {}
            for sheet, xml_path in _sheet_paths(z).items():
                if (sheets is not None and sheet not in sheets) or xml_path not in names:
                    continue
                spec, _unsupported = _parse_autofilter(z.read(xml_path))
                saved = exact.get(sheet)
                if saved and spec is not None and saved.get("sig") == spec_signature(spec):
                    result[sheet] = [dict(f) for f in saved.get("filters") or []]
                elif spec:
                    result[sheet] = spec_to_filters(spec)[0]
                elif saved:
                    result[sheet] = []
            return result if (result or exact) else None
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError, ValueError):
        return None
