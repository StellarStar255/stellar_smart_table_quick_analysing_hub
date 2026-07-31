# -*- coding: utf-8 -*-
"""
文件读写层 - 从 mixins/file_mixin.py 提取的纯数据逻辑（pandas + openpyxl）。

不涉及任何 GUI；主窗口通过后台线程调用这些函数。
"""

import json
import os
import shutil
import tempfile

import pandas as pd

RECENT_FILES_PATH = os.path.expanduser("~/.smart_table_hub/recent_files.json")
MAX_RECENT_FILES = 10

CSV_ENCODINGS = ("utf-8", "gbk", "gb2312", "latin1")


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
    """读取单个 sheet，并对重复表头去重（与旧版行为一致）。"""
    df = excel_file.parse(sheet_name)
    df.columns = _dedupe_headers([str(c) for c in df.columns])
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
    return pd.read_csv(file_path, encoding=enc, sep=sep, header=None,
                       names=names, skip_blank_lines=False)


def read_csv_any_encoding(file_path, delimiter=None) -> pd.DataFrame:
    """按 utf-8 → gbk → gb2312 → latin1 顺序尝试读取 CSV/TSV。

    列数不一致（前言元数据行比数据行窄）导致解析失败时，改为整文件
    原样载入（首行也作为数据、列名用位置字母），由用户决定表头。
    """
    last_err = None
    sep = delimiter
    if sep is None:
        sep = "\t" if file_path.lower().endswith((".tsv", ".txt")) else ","
    for enc in CSV_ENCODINGS:
        try:
            return pd.read_csv(file_path, encoding=enc, sep=sep)
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except pd.errors.ParserError as e:
            last_err = e
            try:
                df = _read_full_ragged_csv(file_path, enc, sep)
                if df is not None:
                    return df
            except (UnicodeDecodeError, pd.errors.ParserError) as e2:
                last_err = e2
            continue
    raise last_err


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


def read_sheet_formulas(file_path, sheet_name) -> dict:
    """用 openpyxl 扫描 sheet 中的公式，返回 {(row, col): "=..."}。

    pandas 读到的是公式的缓存计算值，公式文本必须用 data_only=False 另读。
    仅支持 .xlsx；读取失败返回空 dict。
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
                v = cell.value
                if isinstance(v, str) and len(v) > 1 and v.startswith("="):
                    formulas[(cell.row - 2, cell.column - 1)] = v
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


def read_sheet_colors(file_path, sheet_name) -> dict:
    """读取 sheet 的单元格背景色，返回 {(数据行, 列): '#rrggbb'}。

    行号 -1 表示表头行（Excel 第 1 行）。仅支持 .xlsx；
    无自定义填充或读取失败返回空 dict。
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
        for row in wb[sheet_name].iter_rows():
            for cell in row:
                fill = cell.fill
                if fill is None or fill.fill_type != "solid":
                    continue
                rgb = getattr(fill.fgColor, "rgb", None)
                if not isinstance(rgb, str) or rgb == "00000000":
                    continue
                colors[(cell.row - 2, cell.column - 1)] = "#" + rgb[-6:].lower()
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


def save_workbook(file_path, sheets: dict, sheet_order=None, formulas=None,
                  progress_cb=None, cell_colors=None):
    """把 {sheet名: DataFrame} 全量写入 xlsx。

    formulas: 可选 {sheet名: {(row, col): "=..."}}，公式覆盖写入对应单元格，
    Excel 打开时仍是可计算的公式（df 中已存计算结果，作为兜底值先写入）。
    cell_colors: 可选 {sheet名: {(数据行, 列): '#rrggbb'}}，行 -1 为表头行，
    写成真实的单元格填充（Excel 中同样可见）。
    progress_cb: 可选 (sheet名, 序号从1起, 总数) -> None，逐 sheet 汇报进度。
    先写临时文件再原子替换，避免写一半损坏原文件（与旧版后台保存策略一致）。
    """
    from openpyxl.styles import PatternFill
    order = sheet_order or list(sheets.keys())
    order = [n for n in order if n in sheets]
    formulas = formulas or {}
    cell_colors = cell_colors or {}
    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", dir=os.path.dirname(file_path) or ".")
    os.close(fd)
    try:
        fill_cache = {}
        with pd.ExcelWriter(tmp_path, engine="openpyxl") as writer:
            for i, name in enumerate(order):
                if progress_cb:
                    progress_cb(name, i + 1, len(order))
                safe_name = name[:31]
                sheets[name].to_excel(writer, sheet_name=safe_name, index=False)
                for (row, col), formula in formulas.get(name, {}).items():
                    # +2: 跳过表头行且 openpyxl 从 1 开始计数
                    writer.sheets[safe_name].cell(row=row + 2, column=col + 1,
                                                  value=formula)
                for (row, col), color in cell_colors.get(name, {}).items():
                    argb = "FF" + str(color).lstrip("#").upper()
                    fill = fill_cache.get(argb)
                    if fill is None:
                        fill = PatternFill(start_color=argb, end_color=argb,
                                           fill_type="solid")
                        fill_cache[argb] = fill
                    excel_row = row + 2 if row >= 0 else 1   # -1 = 表头行
                    writer.sheets[safe_name].cell(
                        row=excel_row, column=col + 1).fill = fill
            # 坐标版本标记：本应用保存的文件加载时公式结果可放心写入
            writer.book.properties.keywords = COORD_MARKER
        shutil.move(tmp_path, file_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def save_csv(file_path, df: pd.DataFrame):
    df.to_csv(file_path, index=False, encoding="utf-8")


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
