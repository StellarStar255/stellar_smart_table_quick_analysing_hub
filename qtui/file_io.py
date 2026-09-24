# -*- coding: utf-8 -*-
"""
文件读写层 - 从 mixins/file_mixin.py 提取的纯数据逻辑（pandas + openpyxl）。

不涉及任何 GUI；主窗口通过后台线程调用这些函数。
"""

import datetime
import json
import os
import re
import stat
import tempfile

import numpy as np
import pandas as pd

from qtui.i18n import tr

RECENT_FILES_PATH = os.path.expanduser("~/.smart_table_hub/recent_files.json")
MAX_RECENT_FILES = 10

# 尝试顺序：utf-8-sig 兼容有/无 BOM 的 utf-8；gb18030 是 gbk 的超集但几乎
# 对任何字节对都不报错，必须排在 big5 之后；latin1 对任何字节都不报错，
# 只能放最后兜底。
CSV_ENCODINGS = ("utf-8-sig", "gbk", "big5", "gb18030", "utf-16", "latin1")

# 新建 CSV 的默认编码：带 BOM 的 utf-8，Windows 上的 Excel 才能正确识别中文
DEFAULT_CSV_ENCODING = "utf-8-sig"

# 编码探测只解码文件开头这么多字节（而不是用每种编码整文件 read_csv 一遍）
_ENCODING_SAMPLE_BYTES = 4 << 20

# 纯文本表格格式（分隔符文本）：.csv 逗号；.tsv/.txt 制表符
_TEXT_EXTS = (".csv", ".tsv", ".txt")
_TAB_EXTS = (".tsv", ".txt")

# 字段原文是否带前导零（"007"、"-007"、"+007"）；"0"、"0.5"、"10.0" 都不算
_LEADING_ZERO_RE = r"^\s*[-+]?0\d"


def is_text_format(path) -> bool:
    """路径是否为分隔符文本表格（.csv / .tsv / .txt），大小写不敏感。"""
    return str(path).lower().endswith(_TEXT_EXTS)


def _sep_for_ext(path) -> str:
    """仅按扩展名推断分隔符：.tsv/.txt 制表符，其余逗号。"""
    return "\t" if str(path).lower().endswith(_TAB_EXTS) else ","

# Excel 对 sheet 名的限制
SHEET_NAME_MAX_LEN = 31
_SHEET_NAME_BAD_CHARS = set('\\/?*[]:')


# ---------- 加载 ----------

def load_workbook_lazy(file_path):
    """打开 Excel 文件，返回 (ExcelFile, sheet_names)。不加载任何 sheet 数据。

    优先用 calamine 引擎（Rust 实现，大文件比 openpyxl 快数倍），
    不可用或打开失败时回退到 pandas 默认引擎。
    """
    try:
        excel_file = pd.ExcelFile(file_path, engine="calamine")
    except Exception:
        excel_file = pd.ExcelFile(file_path)
    return excel_file, list(excel_file.sheet_names)


def read_sheet(excel_file: pd.ExcelFile, sheet_name: str) -> pd.DataFrame:
    """读取单个 sheet。

    先按单元格原始类型读入（dtype=object），再让 pandas 只推断整列同类型的
    列：数字列仍是 float64/int64、日期列仍是 datetime64，而 Excel 里以文本
    格式存放的 "007"（工号/邮编/编码）保持字符串，不会被静默改成 7。
    重复表头 pandas 已自动改名（A、A.1 …）。
    """
    df = excel_file.parse(sheet_name, dtype=object).infer_objects()
    df.columns = [str(c) for c in df.columns]
    return df


def _read_full_ragged_csv(file_path, enc, sep):
    """整文件按最大行宽原样读入：不丢弃任何行（含元数据前言与空行），
    列名用位置字母占位。

    世界银行等导出的 CSV 前几行比数据区窄，pandas 按首行推断列数会
    解析失败。这里只负责"完整载入"；是否把某行提升为表头由用户在
    界面上自行决定（绝不静默删行）。
    """
    import csv as _csv
    width = 0
    with open(file_path, "r", encoding=enc, newline="") as f:
        for row in _csv.reader(f, delimiter=sep):
            width = max(width, len(row))
    if width == 0:
        return None
    from core.formula_engine import FormulaEngine
    names = [FormulaEngine.col_index_to_letter(i) for i in range(width)]
    kwargs = dict(encoding=enc, sep=sep, header=None, names=names,
                  skip_blank_lines=False)
    df = pd.read_csv(file_path, **kwargs)
    return _preserve_leading_zeros(df, file_path, kwargs)


def _sniff_bom(file_path):
    """按文件头 BOM 判断编码；无 BOM 返回 None。"""
    try:
        with open(file_path, "rb") as f:
            head = f.read(4)
    except OSError:
        return None
    # 4 字节的 UTF-32 BOM 必须先判：FF FE 00 00 的前两字节与 UTF-16 LE 相同
    if head.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return "utf-32"
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    return None


def _detect_encodings(file_path):
    """返回按可能性排序的候选编码元组。

    有 BOM 直接定；否则只解码文件开头 _ENCODING_SAMPLE_BYTES 字节
    （增量解码器容忍尾部被截断的多字节序列），淘汰解不开的编码——
    比用每种编码把整个文件 read_csv 一遍便宜得多。顺序仍是 CSV_ENCODINGS。
    """
    bom_enc = _sniff_bom(file_path)
    if bom_enc:
        return (bom_enc,)
    try:
        with open(file_path, "rb") as f:
            sample = f.read(_ENCODING_SAMPLE_BYTES)
    except OSError:
        return CSV_ENCODINGS
    import codecs
    final = len(sample) < _ENCODING_SAMPLE_BYTES
    good = []
    for enc in CSV_ENCODINGS:
        try:
            codecs.getincrementaldecoder(enc)().decode(sample, final)
        except (UnicodeError, LookupError):
            continue
        good.append(enc)
    return tuple(good) or CSV_ENCODINGS


def _csv_may_have_leading_zeros(file_path, sep):
    """字节级预扫：文件里是否出现"分隔符/引号/换行 + 0 + 数字"的字段开头。

    绝大多数 CSV 没有前导零字段，预扫（memchr 速度）比二次解析便宜得多；
    只有命中时才对数值列做逐列核对。
    """
    sep_b = sep.encode("ascii", "replace") if sep else b","
    pat = re.compile(rb'[' + re.escape(sep_b) + rb'"\r\n][-+]?0[0-9]')
    try:
        with open(file_path, "rb") as f:
            first = f.read(3)
            if first[:1] == b"0" and first[1:2].isdigit():
                return True
            if first[:1] in (b"-", b"+") and first[1:2] == b"0" and first[2:3].isdigit():
                return True
            f.seek(0)
            tail = b""
            while True:
                chunk = f.read(1 << 22)
                if not chunk:
                    return False
                buf = tail + chunk
                if pat.search(buf):
                    return True
                tail = buf[-4:]
    except OSError:
        return True


def _preserve_leading_zeros(df, file_path, read_kwargs):
    """把被 pandas 推断成数字、但原文带前导零的列（邮编/工号/编码）还原为文本。

    做法：只对"整数值"列（int 列，或非空值全为整数的 float 列）用 dtype=str
    重读，原文匹配 ^\\s*[-+]?0\\d（"007"/"-007"）才判定为前导零，整列保持
    原文；"10.0"、"0"、"0.5" 都不算（旧的"原文比数字位数长"启发式会把
    "10.0" 误判成前导零，把整个浮点列变成文本）。
    不改动其它列，pandas 的快速推断路径保持不变。
    """
    if df is None or len(df) == 0 or len(df.columns) == 0:
        return df
    enc = read_kwargs.get("encoding") or ""
    if not enc.lower().startswith(("utf-16", "utf-32")):   # 宽字符编码无法字节预扫
        if not _csv_may_have_leading_zeros(file_path, read_kwargs.get("sep") or ","):
            return df
    cand = []
    for i in range(len(df.columns)):
        s = df.iloc[:, i]
        if pd.api.types.is_bool_dtype(s):
            continue
        if pd.api.types.is_integer_dtype(s):
            cand.append(i)
        elif pd.api.types.is_float_dtype(s):
            v = s.to_numpy()
            nn = v[~np.isnan(v)]
            if len(nn) and np.all(nn == np.floor(nn)):
                cand.append(i)
    if not cand:
        return df
    try:
        raw = pd.read_csv(file_path, usecols=cand, dtype=str, **read_kwargs)
    except Exception:
        return df
    if len(raw) != len(df):
        return df
    for pos, i in enumerate(cand):
        rs = raw.iloc[:, pos]
        if rs.str.match(_LEADING_ZERO_RE, na=False).any():
            df.isetitem(i, rs.to_numpy())
    return df


def read_csv_any_encoding(file_path, delimiter=None) -> pd.DataFrame:
    """按 BOM → utf-8 → gbk → big5 → gb18030 → utf-16 → latin1 顺序尝试读取 CSV/TSV。

    列数不一致（前言元数据行比数据行窄）导致解析失败时，改为整文件
    原样载入（首行也作为数据、列名用位置字母），由用户决定表头。
    空文件返回空 DataFrame。检测到的编码记录在 df.attrs["source_encoding"]，
    分隔符记录在 df.attrs["source_sep"]，保存时可按原样写回。
    编码先用文件开头样本探测（_detect_encodings），正常只解析一次。
    """
    last_err = None
    sep = delimiter
    if sep is None:
        sep = _sep_for_ext(file_path)
    for enc in _detect_encodings(file_path):
        kwargs = dict(encoding=enc, sep=sep)
        try:
            df = pd.read_csv(file_path, **kwargs)
            df = _preserve_leading_zeros(df, file_path, kwargs)
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except UnicodeError as e:  # utf-16 缺 BOM 等
            last_err = e
            continue
        except pd.errors.EmptyDataError:
            df = pd.DataFrame()
        except pd.errors.ParserError as e:
            last_err = e
            try:
                df = _read_full_ragged_csv(file_path, enc, sep)
            except (UnicodeDecodeError, UnicodeError, pd.errors.ParserError) as e2:
                last_err = e2
                continue
            if df is None:
                df = pd.DataFrame()
        df.attrs["source_encoding"] = _source_encoding_name(file_path, enc)
        df.attrs["source_sep"] = sep
        return df
    raise last_err


def _source_encoding_name(file_path, enc):
    """utf-8-sig 能解码无 BOM 的 utf-8；记录时区分有无 BOM，保存才能原样写回。"""
    if enc == "utf-8-sig" and _sniff_bom(file_path) != "utf-8-sig":
        return "utf-8"
    return enc


def _xlsx_has_formulas(file_path):
    """直接扫 xlsx 压缩包里 worksheet XML 的 <f> 公式标签。

    C 速度的字节扫描，远快于 openpyxl 逐格解析；绝大多数纯数据文件
    没有公式，可借此完全跳过公式扫描。
    """
    import zipfile
    try:
        with zipfile.ZipFile(file_path) as z:
            for name in z.namelist():
                if not (name.startswith("xl/worksheets/") and name.endswith(".xml")):
                    continue
                with z.open(name) as f:
                    tail = b""
                    while True:
                        chunk = f.read(1 << 20)
                        if not chunk:
                            break
                        buf = tail + chunk
                        if b"<f>" in buf or b"<f " in buf:
                            return True
                        tail = buf[-3:]  # 防止标签跨块边界
    except Exception:
        return True  # 无法判断时保守走完整扫描
    return False


def _formula_text(v):
    """单元格值 -> 公式文本（"=..."）；不是公式返回 None。

    普通公式是以 "=" 开头的 str；数组公式（Ctrl+Shift+Enter / 动态数组）
    openpyxl 读成 ArrayFormula 对象，文本在 .text 里。模拟运算表
    （DataTableFormula）没有可显示的文本，返回 None。
    """
    if isinstance(v, str):
        return v if len(v) > 1 and v.startswith("=") else None
    text = getattr(v, "text", None)     # openpyxl.worksheet.formula.ArrayFormula
    if isinstance(text, str) and text:
        return text if text.startswith("=") else "=" + text
    return None



def read_sheet_formulas(file_path, sheet_name) -> dict:
    """用 openpyxl 扫描 sheet 中的公式，返回 {(row, col): "=..."}。

    pandas 读到的是公式的缓存计算值，公式文本必须用 data_only=False 另读。
    数组公式（ArrayFormula）取其 .text。仅支持 .xlsx；读取失败返回空 dict。
    """
    if not str(file_path).lower().endswith(".xlsx"):
        return {}
    if not _xlsx_has_formulas(file_path):
        return {}
    formulas = {}
    try:
        from openpyxl import load_workbook
        wb = load_workbook(file_path, read_only=True, data_only=False)
        if sheet_name not in wb.sheetnames:
            wb.close()
            return {}
        ws = wb[sheet_name]
        for row in ws.iter_rows(min_row=2):  # 第 1 行是表头
            for cell in row:
                text = _formula_text(cell.value)
                if text is not None:
                    formulas[(cell.row - 2, cell.column - 1)] = text
        wb.close()
    except Exception as e:
        print(f"读取公式失败: {e}")
    return formulas


def _xlsx_has_custom_fills(file_path) -> bool:
    """styles.xml 里默认只有 none+gray125 两个 fill，更多说明存在自定义填充。

    字节级预检，避免为绝大多数无背景色的文件做整表样式扫描。
    """
    import zipfile
    try:
        with zipfile.ZipFile(file_path) as z:
            with z.open("xl/styles.xml") as f:
                return f.read().count(b"<fill>") > 2
    except (OSError, KeyError, zipfile.BadZipFile, TypeError,
            ValueError, AttributeError):
        return False


# 主题色索引 -> 主题 XML 里的颜色名（ECMA-376：0/1 是 lt1/dk1，2/3 是 lt2/dk2）
_THEME_SLOTS = ("lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3",
                "accent4", "accent5", "accent6", "hlink", "folHlink")
_DRAWINGML_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _theme_palette(wb):
    """从工作簿主题 XML 解析 12 个主题色 ['RRGGBB' | None, ...]；没有/解析失败返回 None。"""
    raw = getattr(wb, "loaded_theme", None)
    if not raw:
        return None
    try:
        from xml.etree import ElementTree as ET
        root = ET.fromstring(raw)
        scheme = root.find(".//" + _DRAWINGML_NS + "clrScheme")
        if scheme is None:
            return None
        named = {}
        for child in scheme:
            tag = child.tag.split("}")[-1]
            clr = child.find(_DRAWINGML_NS + "srgbClr")
            val = clr.get("val") if clr is not None else None
            if val is None:
                clr = child.find(_DRAWINGML_NS + "sysClr")
                val = clr.get("lastClr") if clr is not None else None
            if val and re.fullmatch(r"[0-9A-Fa-f]{6}", val[-6:]):
                named[tag] = val[-6:].upper()
        return [named.get(k) for k in _THEME_SLOTS]
    except Exception:
        return None


def _apply_tint(hex6, tint):
    """Excel 的主题色明暗调整（tint ∈ [-1, 1]，按 HLS 亮度缩放）。"""
    if not tint:
        return hex6
    import colorsys
    r, g, b = (int(hex6[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if tint < 0:
        l = l * (1.0 + tint)
    else:
        l = l * (1.0 - tint) + tint
    r, g, b = colorsys.hls_to_rgb(h, min(1.0, max(0.0, l)), s)
    return "%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255))


def _fill_rgb(fill, palette=None):
    """solid 填充 -> '#rrggbb'；无填充或本应用无法解析的填充返回 None。

    rgb 直接取；indexed 按 Excel 默认调色板（COLOR_INDEX，64/65 系统色除外）；
    theme 用工作簿主题表 + tint 解析（没有主题表时返回 None）。
    读取（read_sheet_colors）与就地保存清底色（_write_sheet_values）都用这
    一个函数判断"应用认识的填充"，保证只清掉自己读到过的那些。
    注意 openpyxl 对 theme/indexed 颜色的 .rgb 属性返回的是描述符错误文本，
    不能只看 isinstance(rgb, str)。
    """
    if fill is None or fill.fill_type != "solid":
        return None
    color = fill.fgColor
    if color is None:
        return None
    ctype = getattr(color, "type", None)
    if ctype == "rgb":
        rgb = color.rgb
        if not isinstance(rgb, str) or rgb == "00000000":
            return None
        hex6 = rgb[-6:]
    elif ctype == "indexed":
        from openpyxl.styles.colors import COLOR_INDEX
        idx = color.indexed
        if not isinstance(idx, int) or not 0 <= idx < len(COLOR_INDEX):
            return None
        hex6 = COLOR_INDEX[idx][-6:]
    elif ctype == "theme":
        idx = color.theme
        if (not palette or not isinstance(idx, int)
                or not 0 <= idx < len(palette) or palette[idx] is None):
            return None
        tint = color.tint if isinstance(color.tint, (int, float)) else 0.0
        hex6 = _apply_tint(palette[idx], tint)
    else:
        return None
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", hex6):
        return None
    return "#" + hex6.lower()


def read_sheet_colors(file_path, sheet_name) -> dict:
    """读取 sheet 的单元格背景色，返回 {(数据行, 列): '#rrggbb'}。

    行号 -1 表示表头行（Excel 第 1 行）。rgb / 默认调色板 indexed / 主题色
    （含 tint）都解析为 rgb；仅支持 .xlsx；无自定义填充或读取失败返回空 dict。
    """
    if not str(file_path).lower().endswith(".xlsx"):
        return {}
    if not _xlsx_has_custom_fills(file_path):
        return {}
    colors = {}
    try:
        from openpyxl import load_workbook
        wb = load_workbook(file_path, read_only=True)
        if sheet_name not in wb.sheetnames:
            wb.close()
            return {}
        palette = _theme_palette(wb)
        for row in wb[sheet_name].iter_rows():
            for cell in row:
                rgb = _fill_rgb(cell.fill, palette)
                if rgb is not None:
                    colors[(cell.row - 2, cell.column - 1)] = rgb
        wb.close()
    except Exception as e:
        print(f"读取背景色失败: {e}")
    return colors


def _dedupe_headers(headers):
    seen = {}
    result = []
    for h in headers:
        if h in seen:
            seen[h] += 1
            result.append(f"{h}.{seen[h]}")
        else:
            seen[h] = 0
            result.append(h)
    return result


# ---------- 保存 ----------

# 坐标版本标记（写入 xlsx 文档属性 keywords）：带标记 = 本应用新坐标系
# （第 1 行表头、公式坐标与 Excel 一致）保存，加载时公式结果可放心写入；
# 无标记 = Excel/旧版应用来源，加载时错误结果不覆盖文件缓存值
COORD_MARKER = "SmartTableHub-coord-v2"


def xlsx_has_coord_marker(file_path) -> bool:
    """检查 xlsx 是否带本应用的坐标版本标记（直接扫 docProps/core.xml）。"""
    import zipfile
    try:
        with zipfile.ZipFile(file_path) as z:
            with z.open("docProps/core.xml") as f:
                return COORD_MARKER.encode() in f.read()
    except (OSError, KeyError, zipfile.BadZipFile,
            TypeError, ValueError, AttributeError):
        return False


def check_sheet_name(name, existing_names=()):
    """校验 sheet 名是否符合 Excel 规则；合法返回 None，否则返回错误说明。

    规则：非空、不超过 31 个字符、不含 \\ / ? * [ ] :、
    与 existing_names 不重名（Excel 不区分大小写）。
    """
    if name is None or not str(name).strip():
        return tr("Sheet 名不能为空")
    name = str(name)
    if len(name) > SHEET_NAME_MAX_LEN:
        return tr("Sheet 名不能超过 {} 个字符").format(SHEET_NAME_MAX_LEN)
    bad = [c for c in name if c in _SHEET_NAME_BAD_CHARS]
    if bad:
        return tr("Sheet 名不能包含字符: {}").format(" ".join(sorted(set(bad))))
    lowered = name.lower()
    for other in existing_names:
        if str(other).lower() == lowered:
            return tr("已存在同名 Sheet: {}").format(other)
    return None


def _replace_file(tmp_path, file_path):
    """用临时文件原子替换目标；保留目标原有权限位（新文件用 0644）。"""
    try:
        mode = stat.S_IMODE(os.stat(file_path).st_mode)
    except OSError:
        mode = 0o644
    try:
        os.chmod(tmp_path, mode)
    except OSError:
        pass
    # os.replace 在 POSIX 与 Windows 上都是原子替换；失败时直接报错，
    # 绝不退化成"先清空目标再拷贝"的非原子写法
    os.replace(tmp_path, file_path)


def save_workbook(file_path, sheets: dict, sheet_order=None, formulas=None,
                  progress_cb=None, cell_colors=None, filters=None):
    """把 {sheet名: DataFrame} 全量写入 xlsx。

    formulas: 可选 {sheet名: {(row, col): "=..."}}，公式覆盖写入对应单元格，
    Excel 打开时仍是可计算的公式（df 中已存计算结果，作为兜底值先写入）。
    cell_colors: 可选 {sheet名: {(数据行, 列): '#rrggbb'}}，行 -1 为表头行，
    写成真实的单元格填充（Excel 中同样可见）。
    progress_cb: 可选 (sheet名, 序号从1起, 总数) -> None，逐 sheet 汇报进度。
    filters: 可选 {sheet名: {"filters": [...], "visible": [数据行位置] 或 None}}，
    写成 Excel AutoFilter + 隐藏行，并在自定义属性里存精确副本（见 xlsx_filters）。
    先写临时文件再原子替换，避免写一半损坏原文件（与旧版后台保存策略一致）。
    sheet 名不合法（过长/非法字符/重名）时抛 ValueError，而不是静默截断
    导致两个 sheet 互相覆盖。
    """
    from openpyxl.styles import PatternFill
    order = sheet_order or list(sheets.keys())
    order = [n for n in order if n in sheets]
    for i, name in enumerate(order):
        err = check_sheet_name(name, order[:i])
        if err:
            raise ValueError(tr("Sheet 名 \"{}\" 不合法：{}").format(name, err))
    formulas = formulas or {}
    cell_colors = cell_colors or {}
    filters = filters or {}
    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", dir=os.path.dirname(file_path) or ".")
    os.close(fd)
    try:
        fill_cache = {}
        with pd.ExcelWriter(tmp_path, engine="openpyxl") as writer:
            for i, name in enumerate(order):
                if progress_cb:
                    progress_cb(name, i + 1, len(order))
                sheets[name].to_excel(writer, sheet_name=name, index=False)
                for (row, col), formula in formulas.get(name, {}).items():
                    # +2: 跳过表头行且 openpyxl 从 1 开始计数
                    writer.sheets[name].cell(row=row + 2, column=col + 1,
                                             value=formula)
                for (row, col), color in cell_colors.get(name, {}).items():
                    argb = "FF" + str(color).lstrip("#").upper()
                    fill = fill_cache.get(argb)
                    if fill is None:
                        fill = PatternFill(start_color=argb, end_color=argb,
                                           fill_type="solid")
                        fill_cache[argb] = fill
                    excel_row = row + 2 if row >= 0 else 1   # -1 = 表头行
                    writer.sheets[name].cell(
                        row=excel_row, column=col + 1).fill = fill
                if name in filters:
                    _write_sheet_filters(writer.sheets[name], sheets[name],
                                         filters[name])
            _write_filter_properties(writer.book, sheets, filters, order, {})
            # 坐标版本标记：本应用保存的文件加载时公式结果可放心写入
            writer.book.properties.keywords = COORD_MARKER
        _replace_file(tmp_path, file_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


# openpyxl 读不回来、就地保存会丢的部件（图表/图片/条件格式/数据验证等
# 它是能保留的，下面这些不行）。扫到就在保存前提示用户。
_LOSSY_PARTS = (
    ("xl/slicers/", "切片器"),
    ("xl/slicerCaches/", "切片器"),
    ("xl/timelines/", "日程表"),
    ("xl/ctrlProps/", "窗体控件"),
    ("xl/activeX/", "ActiveX 控件"),
    ("xl/threadedComments/", "新版批注（讨论）"),
    ("xl/richData/", "单元格内图片/富数据"),
    ("customXml/", "自定义 XML"),
    ("vbaProject.bin", "宏（VBA）"),
)


def xlsx_lossy_parts(file_path):
    """扫描 xlsx，返回就地保存时会丢失的功能名（已翻译、去重、保持顺序）。"""
    import zipfile
    found = []
    try:
        with zipfile.ZipFile(file_path) as z:
            names = z.namelist()
    except Exception:
        return []
    for part, label in _LOSSY_PARTS:
        if label in found:
            continue
        if any(part in n for n in names):
            found.append(label)
    return [tr(label) for label in found]


def _xl_value(v):
    """numpy/pandas 标量 -> openpyxl 能写的 Python 类型；缺失值 -> None。"""
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, (str, bool, int, float)):
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if isinstance(v, np.datetime64):
        return pd.Timestamp(v).to_pydatetime()
    if isinstance(v, np.generic):
        return v.item()
    return v if isinstance(v, (datetime.datetime, datetime.date,
                               datetime.time)) else str(v)


def _fill_for(color, cache):
    from openpyxl.styles import PatternFill
    argb = "FF" + str(color).lstrip("#").upper()
    fill = cache.get(argb)
    if fill is None:
        fill = PatternFill(start_color=argb, end_color=argb, fill_type="solid")
        cache[argb] = fill
    return fill


def _sync_merged_ranges(ws, max_row, max_col):
    """delete_rows / delete_cols 不会更新合并区域：把超出新范围的合并区裁掉或删掉。

    局限：openpyxl 的 delete_rows/delete_cols 同样不会同步条件格式、数据验证
    的作用区域和公式引用——它们只是引用了已不存在的区域（Excel 打开时忽略
    越界部分，文件不会损坏），这里只处理会让 Excel 报"文件已损坏"的合并区。
    """
    from openpyxl.worksheet.cell_range import CellRange
    for r in list(ws.merged_cells.ranges):
        if r.max_row <= max_row and r.max_col <= max_col:
            continue
        ws.merged_cells.remove(r)
        if r.min_row > max_row or r.min_col > max_col:
            continue                                  # 整个区域都在删掉的范围里
        new_max_row = min(r.max_row, max_row)
        new_max_col = min(r.max_col, max_col)
        if (new_max_row, new_max_col) != (r.min_row, r.min_col):   # 剩一格就不算合并
            ws.merged_cells.add(CellRange(min_col=r.min_col, min_row=r.min_row,
                                          max_col=new_max_col, max_row=new_max_row))


def _write_sheet_values(ws, df, clear_stale_fills, colors=None, formulas=None,
                        palette=None):
    """把 DataFrame 整块覆盖到 sheet（第 1 行表头），并删掉多余的行列。

    - 表头行原本是公式的单元格不覆盖，否则用户的公式会被写死成静态文本；
    - 数组公式（ArrayFormula）在应用的公式表 formulas 里仍存在时不用缓存值
      覆盖（由 patch_workbook 的公式写入负责）；formulas 为 None（调用方没有
      公式信息）时一律保留。模拟运算表（DataTableFormula）应用不解析，一律保留；
    - 合并区域里非左上角的占位格（MergedCell）在 Excel 里本来就没有值，跳过；
      若应用里该格有内容则计入 skipped_merged；
    - clear_stale_fills=True 时清掉数据区里应用认识的旧底色（rgb/默认调色板/
      主题色，见 _fill_rgb）；颜色没变的格子保留原填充对象（主题色不会被改写成
      rgb），应用从没读到过的填充原样保留。
    返回 {"kept_header_formulas": n, "skipped_merged": m}。
    """
    from openpyxl.cell.cell import MergedCell
    from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula
    colors = colors or {}
    kept = 0
    skipped_merged = 0
    ncols = len(df.columns)
    nrows = len(df.index)
    if ncols:
        header_cells = next(ws.iter_rows(min_row=1, max_row=1, max_col=ncols))
        for cell, name in zip(header_cells, df.columns):
            if isinstance(cell, MergedCell):
                if not str(name).startswith("Unnamed"):
                    skipped_merged += 1
                continue
            if _formula_text(cell.value) is not None:
                kept += 1
                continue
            cell.value = str(name)
    if nrows and ncols:
        rows = ws.iter_rows(min_row=2, max_row=nrows + 1, max_col=ncols)
        for i, (cells, values) in enumerate(
                zip(rows, df.itertuples(index=False, name=None))):
            for j, (cell, v) in enumerate(zip(cells, values)):
                if isinstance(cell, MergedCell):
                    if _xl_value(v) is not None:
                        skipped_merged += 1
                    continue
                cur = cell.value
                if isinstance(cur, DataTableFormula):
                    continue
                if isinstance(cur, ArrayFormula) and (
                        formulas is None or (i, j) in formulas):
                    continue
                # 不能用 ws.cell(..., value=...)：openpyxl 对 None 是跳过不写，
                # 旧值会留在格子里，用户删掉的内容就删不掉
                cell.value = _xl_value(v)
    if clear_stale_fills and ncols:
        from openpyxl.styles import PatternFill
        none_fill = PatternFill()
        for row in ws.iter_rows(min_row=1, max_row=nrows + 1, max_col=ncols):
            for cell in row:
                fill = cell.fill
                if fill is None or fill.fill_type is None:
                    continue
                known = _fill_rgb(fill, palette)
                if known is None:
                    continue          # 应用从没读到过的填充，原样保留
                if colors.get((cell.row - 2, cell.column - 1)) == known:
                    continue          # 颜色没变：保留原填充（主题色仍是主题色）
                cell.fill = none_fill
    # 多余的行列真删掉（表变短/变窄时不留空壳）
    if ws.max_row > nrows + 1:
        ws.delete_rows(nrows + 2, ws.max_row - (nrows + 1))
    if ws.max_column > ncols:
        ws.delete_cols(ncols + 1, ws.max_column - ncols)
    _sync_merged_ranges(ws, nrows + 1, ncols)
    return {"kept_header_formulas": kept, "skipped_merged": skipped_merged}


def _apply_fills(ws, colors, cache, palette=None):
    from openpyxl.cell.cell import MergedCell
    for (row, col), color in colors.items():
        excel_row = row + 2 if row >= 0 else 1   # -1 = 表头行
        cell = ws.cell(row=excel_row, column=col + 1)
        if isinstance(cell, MergedCell):
            continue
        if _fill_rgb(cell.fill, palette) == color:
            continue                              # 已是这个颜色：不改写原填充
        cell.fill = _fill_for(color, cache)


def _write_formulas(ws, formulas):
    """把应用的公式写回 sheet；原本是数组公式的单元格仍写成数组公式。"""
    from openpyxl.cell.cell import MergedCell
    from openpyxl.worksheet.formula import ArrayFormula
    for (row, col), formula in formulas.items():
        # +2: 跳过表头行且 openpyxl 从 1 开始计数
        cell = ws.cell(row=row + 2, column=col + 1)
        if isinstance(cell, MergedCell):
            continue
        cur = cell.value
        if isinstance(cur, ArrayFormula):
            if cur.text != formula:
                cell.value = ArrayFormula(cur.ref, formula)
            continue
        cell.value = formula


def patch_workbook(src_path, dest_path, sheets: dict, sheet_order,
                   formulas=None, cell_colors=None, progress_cb=None,
                   filters=None, filter_frames=None):
    """在原工作簿基础上就地更新数据后另存，保留 pandas 重建会丢掉的一切
    （透视表、条件格式、数据验证、数字格式、列宽、合并单元格、图表、图片…）。

    sheets: {sheet名: DataFrame}，只写这些 sheet；不在里面的 sheet 原样保留。
    sheet_order: 保存后完整的 sheet 顺序；不在其中的 sheet 视为用户删除。
    formulas / cell_colors: 与 save_workbook 同义，只对 sheets 里的 sheet 生效。
    filters: 与 save_workbook 同义，但只放本次筛选有变化的 sheet（空条件 = 去掉筛选）；
    其余 sheet 的 AutoFilter / 隐藏行原样保留。filter_frames 为这些 sheet 的完整数据
    （算行数与表头用，可以包含 sheets 以外的 sheet）。
    返回 {"kept_header_formulas": n, "skipped_merged_cells": m}：
    m 是落在合并区域占位格上、Excel 里无法存放而没写入的非空值个数。
    先写临时文件再原子替换，写一半失败不会损坏任何一个文件。
    """
    from openpyxl import load_workbook
    order = list(sheet_order)
    for i, name in enumerate(order):
        err = check_sheet_name(name, order[:i])
        if err:
            raise ValueError(tr("Sheet 名 \"{}\" 不合法：{}").format(name, err))
    formulas = formulas or {}
    cell_colors = cell_colors or {}
    filters = filters or {}
    filter_frames = filter_frames or {}
    # 原文件本来就没有自定义底色时，不必为"清除底色"去扫整个数据区
    clear_stale_fills = _xlsx_has_custom_fills(src_path)
    keep_vba = (str(src_path).lower().endswith((".xlsm", ".xltm"))
                and str(dest_path).lower().endswith((".xlsm", ".xltm")))
    wb = load_workbook(src_path, data_only=False, keep_vba=keep_vba)
    palette = _theme_palette(wb) if clear_stale_fills else None
    kept_header_formulas = 0
    skipped_merged = 0
    fd, tmp_path = tempfile.mkstemp(suffix=os.path.splitext(dest_path)[1] or ".xlsx",
                                    dir=os.path.dirname(dest_path) or ".")
    os.close(fd)
    try:
        for name in list(wb.sheetnames):
            if name not in order:
                del wb[name]                      # 应用里删掉的 sheet
        fill_cache = {}
        for i, name in enumerate(order):
            if progress_cb:
                progress_cb(name, i + 1, len(order))
            df = sheets.get(name)
            if df is None:
                continue                          # 没改动过：整张原样保留
            ws = wb[name] if name in wb.sheetnames else wb.create_sheet(title=name)
            sheet_colors = cell_colors.get(name, {})
            stats = _write_sheet_values(ws, df, clear_stale_fills,
                                        colors=sheet_colors,
                                        formulas=formulas.get(name),
                                        palette=palette)
            kept_header_formulas += stats["kept_header_formulas"]
            skipped_merged += stats["skipped_merged"]
            _write_formulas(ws, formulas.get(name, {}))
            _apply_fills(ws, sheet_colors, fill_cache, palette)
        for name, state in filters.items():
            df = sheets.get(name, filter_frames.get(name))
            if df is None or name not in wb.sheetnames:
                continue
            ws = wb[name]
            _write_sheet_filters(ws, df, state,
                                 unhide_when_cleared=bool(ws.auto_filter.ref))
        from qtui import xlsx_filters
        existing = xlsx_filters.read_properties_from_workbook(wb)
        _write_filter_properties(
            wb, {**filter_frames, **sheets}, filters, order,
            {n: s for n, s in existing.items() if n in order and n not in filters})
        wb._sheets = [wb[n] for n in order if n in wb.sheetnames]
        wb.properties.keywords = COORD_MARKER
        wb.save(tmp_path)
        wb.close()
        _replace_file(tmp_path, dest_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    return {"kept_header_formulas": kept_header_formulas,
            "skipped_merged_cells": skipped_merged}


def _write_sheet_filters(ws, df, state, unhide_when_cleared=False):
    """把一个 sheet 的筛选写成 AutoFilter + 隐藏行（visible 为 None 时按条件现算）。"""
    from qtui import filter_engine, xlsx_filters
    conditions = xlsx_filters.resolve_columns(state.get("filters") or [], df.columns)
    spec = xlsx_filters.autofilter_spec(conditions, df.columns)
    visible = state.get("visible")
    if spec and visible is None:
        _filtered, idx_map = filter_engine.apply_filters(df, conditions)
        index = df.index
        visible = [index.get_loc(label) for label in idx_map]
    xlsx_filters.apply_to_worksheet(ws, spec, len(df.columns), len(df.index),
                                    visible or [], unhide_when_cleared)


def _write_filter_properties(wb, frames, filters, order, keep):
    """自定义属性里的精确副本：本次写的 sheet 用新条件，keep 里的原样保留。"""
    from qtui import xlsx_filters
    state = dict(keep)
    for name, st in filters.items():
        df = frames.get(name)
        if name not in order or df is None:
            continue
        conditions = xlsx_filters.resolve_columns(st.get("filters") or [], df.columns)
        if conditions:
            state[name] = {"filters": conditions, "sig": xlsx_filters.spec_signature(
                xlsx_filters.autofilter_spec(conditions, df.columns))}
        else:
            state.pop(name, None)
    xlsx_filters.write_properties(wb, state)


def csv_separator(file_path, df: pd.DataFrame = None, sep=None) -> str:
    """决定写分隔符文本时用的分隔符。

    显式 sep 优先；否则 .tsv 一定是制表符；.csv 用读取时记录的
    df.attrs["source_sep"]（如用户自定义的 ";"），但从 .tsv/.txt 导出成
    .csv 时不把制表符带进 .csv；其余（.txt）按记录值，没有则按扩展名推断。
    """
    if sep:
        return sep
    ext = os.path.splitext(str(file_path))[1].lower()
    src = (df.attrs.get("source_sep") if df is not None else None) or None
    if ext == ".tsv":
        return "\t"
    if ext == ".csv" and src == "\t":
        return ","
    return src or _sep_for_ext(file_path)


def save_csv(file_path, df: pd.DataFrame, encoding=None, sep=None):
    """写 CSV/TSV/TXT：先写同目录临时文件再原子替换，写一半失败不会损坏原文件。

    encoding 未指定时依次取：df.attrs["source_encoding"]（读取时记录的
    原文件编码，按原样写回）→ utf-8-sig（新文件；带 BOM，Windows Excel
    才能正确显示中文）。sep 未指定时见 csv_separator（.tsv/.txt 用制表符）。
    """
    enc = encoding or df.attrs.get("source_encoding") or DEFAULT_CSV_ENCODING
    sep = csv_separator(file_path, df, sep)
    suffix = os.path.splitext(str(file_path))[1] or ".csv"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, dir=os.path.dirname(file_path) or ".")
    os.close(fd)
    try:
        df.to_csv(tmp_path, index=False, encoding=enc, sep=sep)
        _replace_file(tmp_path, file_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


# ---------- 最近文件 ----------

def load_recent_files():
    """返回 (recent_files 列表, auto_save 布尔)。"""
    try:
        with open(RECENT_FILES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("recent_files", []), data.get("auto_save", False)
    except (OSError, json.JSONDecodeError):
        return [], False


def save_recent_files(recent_files, auto_save):
    os.makedirs(os.path.dirname(RECENT_FILES_PATH), exist_ok=True)
    with open(RECENT_FILES_PATH, "w", encoding="utf-8") as f:
        json.dump({"recent_files": recent_files, "auto_save": auto_save},
                  f, ensure_ascii=False, indent=2)


def add_recent_file(recent_files, path):
    path = os.path.abspath(path)
    files = [p for p in recent_files if p != path]
    files.insert(0, path)
    return files[:MAX_RECENT_FILES]
