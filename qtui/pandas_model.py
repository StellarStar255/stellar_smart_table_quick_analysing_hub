# -*- coding: utf-8 -*-
"""
PandasTableModel - 基于 QAbstractTableModel 的 pandas DataFrame 模型

与 Tkinter Treeview 不同，Qt 的 Model/View 架构是虚拟化的：
视图只请求可见单元格的数据，因此无论多少行都不需要分批渲染或列分页。
"""

import bisect
import datetime
from collections import deque

from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex, pyqtSignal
from PyQt6.QtGui import QColor, QFont
import pandas as pd
import numpy as np

from core.formula_engine import FormulaEngine
from qtui.i18n import tr

FORMULA_TEXT_COLOR = "#4a9edb"  # 公式单元格文字颜色（Excel 风格的蓝）

# 预计算常量（data() 每帧调用数千次，避免重复构造枚举/颜色对象）
_ALIGN_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
_HIGHLIGHT_BRUSH = QColor(74, 158, 219, 46)   # 当前行整行淡色高亮
_FORMULA_BRUSH = QColor(FORMULA_TEXT_COLOR)
def _has_leading_zero_text(series) -> bool:
    """列中是否有 "007"、"02134" 这类带前导零的文本（工号/邮编/编码）。

    转成数值会丢前导零——属于静默修改用户数据，这类列必须保持文本。
    """
    # pandas 3 起文本列默认是 str dtype（不再是 object），两种都要认
    if not (pd.api.types.is_object_dtype(series.dtype)
            or pd.api.types.is_string_dtype(series.dtype)):
        return False
    text = series.dropna().astype(str).str.strip()
    if text.empty:
        return False
    return bool(text.str.match(r'^[-+]?0\d').any())


def to_numeric_or_keep(series):
    """整列可转数值才转，否则原样返回。

    等价旧版 pd.to_numeric(errors="ignore")——该参数 pandas 2.2 起废弃、
    3.x 移除（传入会抛 ValueError），统一用此实现兼容各版本。
    含前导零文本的列不转（否则 "007" 变 7）。
    """
    if _has_leading_zero_text(series):
        return series
    try:
        return pd.to_numeric(series)
    except (ValueError, TypeError):
        return series


def _format_cell(value) -> str:
    """单元格值 -> 显示文本。必须对任何对象都不抛异常（data() 每帧调用）。"""
    if type(value) is str:      # 最常见情况直接返回
        return value
    if value is None:
        return ""
    if isinstance(value, float):
        if value != value:
            return ""
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return repr(float(value))   # 1e+300 而不是 301 位整数（np.float64 也走这里）
    if isinstance(value, (list, tuple, np.ndarray)):
        return str(value)
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return str(value)
    if isinstance(value, np.floating):
        return _format_cell(float(value))
    if isinstance(value, datetime.datetime):   # 含 pd.Timestamp
        if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
            return value.date().isoformat()
        return value.strftime('%Y-%m-%d %H:%M:%S')
    return str(value)


_HEADER_ROW_BRUSH = QColor(230, 126, 34)  # 表头行底色（橙色）
_HEADER_TEXT_BRUSH = QColor(255, 255, 255)  # 默认橙底配白字（用户偏好）
_HEADER_ROW_FONT = QFont()
_HEADER_ROW_FONT.setBold(True)


class PandasTableModel(QAbstractTableModel):
    """把 pandas DataFrame 直接暴露给 QTableView 的可编辑模型。"""

    # 列重命名成功（col, 旧名, 新名）——宿主窗口据此同步筛选条件/original_df 等
    columnRenamed = pyqtSignal(int, str, str)
    # 列重命名失败（提示文本）——表头行内联编辑没有对话框，宿主用状态栏反馈
    renameFailed = pyqtSignal(str)
    # 背景色变化 [(数据行, 列, 生效颜色或 None), ...]——含撤销/重做，
    # 宿主据此维护筛选期间的原始行坐标颜色底账
    cellColorsChanged = pyqtSignal(list)
    # 记入了一条新的可撤销操作（不含撤销/重做回放）——宿主据此作废自己的重做记录
    historyPushed = pyqtSignal()

    def __init__(self, df: pd.DataFrame = None, parent=None):
        super().__init__(parent)
        self._df = df if df is not None else pd.DataFrame()
        self.modified = False
        # 撤销/重做栈：存 (row, col, old_value, new_value)
        self._undo_stack = []
        self._redo_stack = []
        self._undo_limit = 500
        # 单元格背景色: (row, col) -> hex 颜色字符串
        self.cell_colors = {}
        # 公式: (row, col) -> "=..."；df 中存计算结果
        self.formulas = {}
        self._dependents = {}   # (row, col) -> 依赖它的公式单元格集合
        # 正向索引：公式单元格 -> 它依赖的键集合。注销依赖只按此索引
        # 精确清除，避免线性扫描 _dependents（大量公式时是 O(N²) 热点）
        self._formula_deps = {}
        # 区域依赖只记边界：公式单元格 -> [(r0, r1, c0, c1), ...]。整列引用
        # 展开成几十万个键是清除筛选/打开文件时的主要卡顿来源
        self._formula_ranges = {}
        # 区域反向索引：边界 -> 引用该区域的公式集合，再按列分桶
        # （列 -> 触及该列的边界集合）。整列填充的公式共享同一边界，
        # 查"谁依赖这个格"只需检查所在列的少数几个边界，而不是扫全部公式
        self._range_formulas = {}
        self._range_cols = {}
        self._engine = FormulaEngine()
        # 行列结构版本号：任何插入/删除/重排/整表替换都会递增，
        # 供公式剪贴板等按位置缓存的状态判断是否已失效
        self.structure_version = 0
        # 当前行整行高亮（选中单元格所在行）
        self.highlight_row = -1
        # 列级 numpy 数组缓存：data() 每帧调用数千次，df.iat 每次都要
        # 构造 Series 包装，是大数据量下绘制卡顿的主因。所有写入必须
        # 经由 _invalidate_values() 使缓存失效。
        self._col_arrays = {}

    # ---------- 基本接口 ----------

    # 视图第 0 行是虚拟表头行（显示/编辑列名），数据行在视图中从 1 起。
    # 内部一切键（formulas/cell_colors/undo/依赖表）仍用 0 基数据行坐标，
    # 只在视图边界（data/setData/信号发射）做 ±1 换算。
    HEADER_ROWS = 1

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._df) + self.HEADER_ROWS

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._df.columns)

    def _col_info(self, col):
        """列级缓存：(numpy 数组, 是否数值 dtype)。绕开 df.iat 的 Series 构造开销。"""
        info = self._col_arrays.get(col)
        if info is None:
            series = self._df.iloc[:, col]
            dtype = series.dtype
            if pd.api.types.is_datetime64_any_dtype(dtype) or pd.api.types.is_timedelta64_dtype(dtype):
                # to_numpy() 给出 numpy.datetime64，str() 是 "2026-01-15T00:00:00.000000000"；
                # 取对象数组（Timestamp/NaT）交给 _format_cell 格式化
                arr = series.astype(object).to_numpy()
            else:
                arr = series.to_numpy()
            info = (arr, pd.api.types.is_numeric_dtype(dtype))
            self._col_arrays[col] = info
        return info

    @property
    def engine(self):
        """公式引擎（宿主重写挂起公式的引用时要用）。"""
        return self._engine

    def _invalidate_values(self, col=None):
        """使显示缓存失效；单格写入只需失效所在列，避免每次编辑重建整表缓存。"""
        if col is None:
            self._col_arrays.clear()
        else:
            self._col_arrays.pop(col, None)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if index.row() == 0:
            return self._header_row_data(index.column(), role)
        row = index.row() - self.HEADER_ROWS   # 数据行坐标
        if role == Qt.ItemDataRole.DisplayRole or role == Qt.ItemDataRole.EditRole:
            if role == Qt.ItemDataRole.EditRole:
                # 编辑公式单元格时显示公式本身
                formula = self.formulas.get((row, index.column()))
                if formula:
                    return formula
            return _format_cell(self._col_info(index.column())[0][row])
        if role == Qt.ItemDataRole.BackgroundRole:
            color = self.cell_colors.get((row, index.column()))
            if color:
                return QColor(color)
            if index.row() == self.highlight_row:
                return _HIGHLIGHT_BRUSH
            return None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            arr, numeric = self._col_info(index.column())
            if numeric:
                return _ALIGN_RIGHT
            value = arr[row]
            if isinstance(value, (int, float, np.integer, np.floating)):
                # NaN 靠左（与原行为一致），其余数字靠右
                return _ALIGN_LEFT if value != value else _ALIGN_RIGHT
            return _ALIGN_LEFT
        if role == Qt.ItemDataRole.ForegroundRole:
            if (row, index.column()) in self.formulas:
                return _FORMULA_BRUSH
            return None
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.formulas.get((row, index.column()))
        return None

    def _header_row_data(self, col, role):
        """视图第 0 行：列名（可编辑重命名），加粗并以底色区分。

        默认列名就是位置字母（新建表的 A/B/C...），此时显示为空——
        字母坐标已由固定列头提供，重复显示像脏数据；起过名才显示。
        """
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col < len(self._df.columns):
                name = str(self._df.columns[col])
                if name == FormulaEngine.col_index_to_letter(col):
                    return ""
                return name
            return ""
        if role == Qt.ItemDataRole.BackgroundRole:
            # 表头行也支持自定义背景色（键为 (-1, col)）
            color = self.cell_colors.get((-1, col))
            if color:
                return QColor(color)
            return _HEADER_ROW_BRUSH
        if role == Qt.ItemDataRole.FontRole:
            return _HEADER_ROW_FONT
        if role == Qt.ItemDataRole.ForegroundRole:
            # 默认橙底固定配白字；自定义底色返回 None，
            # 由绘制委托按底色亮度自动选黑/白字
            if (-1, col) not in self.cell_colors:
                return _HEADER_TEXT_BRUSH
            return None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_LEFT
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            # 固定字母坐标（Excel 风格），列名显示在视图第 1 行
            return FormulaEngine.col_index_to_letter(section)
        # 行号从 1 开始；第 1 行是表头行，数据从 2 起（与 Excel/公式引用一致）
        return str(section + 1)

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return (Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEditable)

    # ---------- 编辑 ----------

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        if index.row() == 0:
            # 表头行：编辑即重命名列
            return self.rename_column(index.column(), str(value).strip())
        row, col = index.row() - self.HEADER_ROWS, index.column()
        key = (row, col)
        old = self._df.iat[row, col]
        old_dtype = self._df.iloc[:, col].dtype
        old_formula = self.formulas.get(key)
        text = str(value)

        if text.startswith("=") and len(text) > 1:
            # 公式：存公式文本，df 中写入计算结果
            if old_formula == text:
                return False
            new_formula = text
            result = self._evaluate(text)
            self.formulas[key] = text
            self._register_deps(key)
            self._set_cell(row, col, result)
        else:
            new_formula = None
            new = self._coerce(value, col)
            old_str = "" if pd.isna(old) else str(old)
            new_str = "" if new is None or (isinstance(new, float) and pd.isna(new)) else str(new)
            if old_str == new_str and old_formula is None:
                return False
            if old_formula:
                self.formulas.pop(key, None)
                self._unregister_deps(key)
            self._set_cell(row, col, new)

        self._push_undo((row, col, old, self._df.iat[row, col], old_formula, new_formula,
                         old_dtype))
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])
        self._recalc_dependents(key)
        self.modified = True
        return True

    def set_cells(self, entries):
        """批量写入单元格（粘贴/填充），语义等同对每条逐个调用 setData(EditRole)。

        entries: (view_row, view_col, value) 的可迭代对象，坐标与 setData 收到的
        index 一致：视图第 0 行是表头行（写入即重命名列，重名/空名与 setData
        一样拒绝），数据行从 HEADER_ROWS 起；"=" 开头的字符串成为公式，数值
        强转规则与 setData 相同；越界坐标忽略。与逐格 setData 的区别：
        整批只记一条撤销记录（撤销一次全部恢复、重做一次全部重写，含表头重命名）、
        只发一次外接矩形的 dataChanged、依赖登记与公式重算在最后只做一次
        （公式按依赖拓扑序求值，结果与逐格写入一致）。返回是否有任何改动。
        """
        nrows, ncols = len(self._df), len(self._df.columns)
        records = []      # 单格记录，格式同 setData 的撤销记录
        renames = []      # (col, 旧名, 新名)
        keys = set()
        new_formulas = set()
        for view_row, col, value in entries:
            if not (0 <= col < ncols):
                continue
            if view_row == 0:
                old_name = str(self._df.columns[col])
                new_name = str(value).strip()
                if not new_name or new_name == old_name:
                    continue
                if new_name in self._df.columns:
                    self.renameFailed.emit(tr("列名无效或已存在"))
                    continue
                self._rename_column_impl(col, new_name)
                renames.append((col, old_name, new_name))
                continue
            row = view_row - self.HEADER_ROWS
            if not (0 <= row < nrows):
                continue
            key = (row, col)
            old = self._df.iat[row, col]
            old_dtype = self._df.iloc[:, col].dtype
            old_formula = self.formulas.get(key)
            text = str(value)
            if text.startswith("=") and len(text) > 1:
                if old_formula == text:
                    continue
                # 公式值不在此处求：最后把本批全部公式连同依赖闭包按拓扑序算一遍，
                # 结果与逐格立即求值一致，且每个公式只算一次
                new_formula = text
                self.formulas[key] = text
                new_formulas.add(key)
            else:
                new_formula = None
                new = self._coerce(value, col)
                old_str = "" if pd.isna(old) else str(old)
                new_str = ("" if new is None or (isinstance(new, float) and pd.isna(new))
                           else str(new))
                if old_str == new_str and old_formula is None:
                    continue
                if old_formula:
                    self.formulas.pop(key, None)
                    self._unregister_deps(key)
                    new_formulas.discard(key)
                self._set_cell(row, col, new)
            keys.add(key)
            records.append([row, col, old, None, old_formula, new_formula, old_dtype])
        if not records and not renames:
            return False
        if keys:
            for key in new_formulas:
                self._register_deps(key)
            recalc = new_formulas | self._closure_of(keys)
            self._recalc_cells(recalc, emit=False)
            for rec in records:
                rec[3] = self._df.iat[rec[0], rec[1]]
            self._emit_cells_changed(keys | recalc)
        self._push_undo(("__cells__", [tuple(r) for r in records], renames))
        self.modified = True
        return True

    def _replay_cells(self, record, forward):
        """撤销/重做一条 "__cells__" 批量记录：整批回放后重算一次、发一次信号。"""
        _, records, renames = record
        if forward:
            for rec in records:
                self._apply_cell_record(rec, forward=True)
            for col, _old_name, new_name in renames:
                self._rename_column_impl(col, new_name)
        else:
            for rec in reversed(records):
                self._apply_cell_record(rec, forward=False)
            for col, old_name, _new_name in reversed(renames):
                self._rename_column_impl(col, old_name)
        keys = {(rec[0], rec[1]) for rec in records}
        if keys:
            recalc = {k for k in keys if k in self.formulas} | self._closure_of(keys)
            self._recalc_cells(recalc, emit=False)
            self._emit_cells_changed(keys | recalc)
        self.modified = True

    def clear_cells(self, cells):
        """批量清空数据单元格（Delete/Backspace）。

        cells 为数据坐标 (row, col) 的可迭代对象。按列向量化写入：
        只发一次 dataChanged、只记一条撤销记录（整片清空一次撤销即可恢复），
        百万格级别的选区也不会逐格触发视图刷新与依赖重算。返回清空的单元格数。
        """
        nrows, ncols = len(self._df), len(self._df.columns)
        by_col = {}
        for row, col in cells:
            if 0 <= row < nrows and 0 <= col < ncols:
                by_col.setdefault(col, []).append(row)
        # 公式按列分桶一次，避免每列都扫全部公式
        formulas_by_col = {}
        for (r, c), f in self.formulas.items():
            if c in by_col:
                formulas_by_col.setdefault(c, {})[r] = f
        entries = []
        for col, rows in by_col.items():
            rows = np.array(sorted(set(rows)), dtype=np.intp)
            series = self._df.iloc[:, col]
            old_vals = series.to_numpy(dtype=object)[rows]
            col_formulas = formulas_by_col.get(col, {})
            if len(col_formulas) <= len(rows):
                row_set = set(rows.tolist())
                formulas = {r: f for r, f in col_formulas.items() if r in row_set}
            else:
                formulas = {int(r): col_formulas[r] for r in rows.tolist() if r in col_formulas}
            blank = pd.isna(old_vals) | (old_vals == "")
            if formulas:
                blank &= ~np.isin(rows, list(formulas))
            rows, old_vals = rows[~blank], old_vals[~blank]
            if len(rows) == 0:
                continue
            entries.append((col, rows, old_vals, series.dtype, formulas))
        if not entries:
            return 0
        self._apply_clear_entries(entries)
        self._push_undo(("__batch__", entries))
        self._after_batch(entries)
        return int(sum(len(e[1]) for e in entries))

    def _apply_clear_entries(self, entries):
        for col, rows, _old, _dtype, formulas in entries:
            for r in formulas:
                self.formulas.pop((r, col), None)
                self._unregister_deps((r, col))
            series = self._df.iloc[:, col]
            if pd.api.types.is_integer_dtype(series.dtype) or pd.api.types.is_bool_dtype(series.dtype):
                self._df.isetitem(col, series.astype(float))   # 整数列放不下 NaN
            self._df.iloc[rows, col] = np.nan
            self._invalidate_values(col)

    def _restore_clear_entries(self, entries):
        for col, rows, old_vals, old_dtype, formulas in reversed(entries):
            # 让 numpy 推断出紧凑 dtype（全 float/int 时不再是 object），
            # 避免 pandas 对 object 数组写入数值列的弃用警告；混合类型则先整列转 object
            try:
                vals = np.asarray(old_vals.tolist())
            except (ValueError, TypeError):
                vals = old_vals
            if vals.dtype == object and self._df.iloc[:, col].dtype != object:
                self._column_to_object(col)
            try:
                self._df.iloc[rows, col] = vals
            except (ValueError, TypeError):
                self._column_to_object(col)
                self._df.iloc[rows, col] = old_vals
            self._invalidate_values(col)
            self._restore_dtype(col, old_dtype)
            for r, f in formulas.items():
                self._apply_formula_state(r, col, f)

    def _after_batch(self, entries):
        keys = set()
        for col, rows, *_ in entries:
            keys.update((int(r), col) for r in rows)
        # 所有受影响的公式合并成一个闭包按依赖序算一遍，
        # 而不是每个被清的格各触发一轮（整列公式会被重算上千次）
        closure = self._closure_of(keys)
        self._recalc_cells(closure, emit=False)
        self._emit_cells_changed(keys | closure)
        self.modified = True

    def _emit_cells_changed(self, keys, role=Qt.ItemDataRole.DisplayRole):
        """对一片数据单元格（(数据行, 列)，-1 为表头行）发一次外接矩形的 dataChanged。"""
        if not keys:
            return
        rows = [r for r, _ in keys]
        cols = [c for _, c in keys]
        self.dataChanged.emit(
            self.index(min(rows) + self.HEADER_ROWS, min(cols)),
            self.index(max(rows) + self.HEADER_ROWS, max(cols)),
            [role])

    def _apply_cell_record(self, record, forward):
        """回放一条单元格编辑记录（forward=False 撤销 / True 重做），不动撤销栈。"""
        row, col, old, new, old_formula, new_formula, old_dtype = record
        if forward:
            self._apply_formula_state(row, col, new_formula)
            self._set_cell(row, col, new)
        else:
            self._apply_formula_state(row, col, old_formula)
            self._set_cell(row, col, old)
            self._restore_dtype(col, old_dtype)

    # ---------- 公式 ----------

    def _evaluate(self, formula):
        # 结构操作会替换 self._df 对象，求值前统一把当前表注入引擎
        self._engine.set_dataframe(self._df)
        return self._engine.evaluate(formula)

    def shift_formula(self, formula, row_delta, col_delta):
        """复制/填充公式时按偏移平移相对引用（委托给引擎）。"""
        return self._engine.shift_formula(formula, row_delta, col_delta)

    def _register_deps(self, formula_cell):
        """登记公式对其他单元格的依赖（引擎按位置返回 (row, col)，区域已裁剪到表格范围）。"""
        self._unregister_deps(formula_cell)
        formula = self.formulas.get(formula_cell)
        if not formula:
            return
        self._engine.set_dataframe(self._df)
        keys, ranges = self._engine.extract_dependency_spec(formula)
        for key in keys:
            self._dependents.setdefault(key, set()).add(formula_cell)
        if keys:
            self._formula_deps[formula_cell] = keys
        if ranges:
            self._formula_ranges[formula_cell] = ranges
            for bounds in ranges:
                fcells = self._range_formulas.get(bounds)
                if fcells is None:
                    fcells = self._range_formulas[bounds] = set()
                    for c in range(bounds[2], bounds[3] + 1):
                        self._range_cols.setdefault(c, set()).add(bounds)
                fcells.add(formula_cell)

    def _unregister_deps(self, formula_cell):
        for key in self._formula_deps.pop(formula_cell, ()):
            deps = self._dependents.get(key)
            if deps is not None:
                deps.discard(formula_cell)
                if not deps:
                    del self._dependents[key]
        for bounds in self._formula_ranges.pop(formula_cell, ()):
            fcells = self._range_formulas.get(bounds)
            if fcells is None:
                continue
            fcells.discard(formula_cell)
            if not fcells:
                del self._range_formulas[bounds]
                for c in range(bounds[2], bounds[3] + 1):
                    col_bounds = self._range_cols.get(c)
                    if col_bounds is not None:
                        col_bounds.discard(bounds)
                        if not col_bounds:
                            del self._range_cols[c]

    def _clear_dep_index(self):
        self._dependents.clear()
        self._formula_deps.clear()
        self._formula_ranges.clear()
        self._range_formulas.clear()
        self._range_cols.clear()

    def _rebuild_all_deps(self):
        self._clear_dep_index()
        for key in list(self.formulas):
            self._register_deps(key)

    def _dependents_of(self, cell):
        """直接依赖 cell 的公式单元格：单格登记的 + 区域包含它的。

        区域部分只看 cell 所在列的边界桶：O(该列不同区域数)，与公式总数无关。
        """
        out = set(self._dependents.get(cell, ()))
        col_bounds = self._range_cols.get(cell[1])
        if col_bounds:
            r = cell[0]
            for bounds in col_bounds:
                if bounds[0] <= r <= bounds[1]:
                    out.update(self._range_formulas[bounds])
        return out

    def _closure_of(self, seeds):
        """依赖任一 seed 的全部公式单元格（传递闭包，迭代 BFS）。"""
        closure = set()
        queue = deque(seeds)
        while queue:
            cell = queue.popleft()
            for fcell in self._dependents_of(cell):
                if fcell not in closure and fcell in self.formulas:
                    closure.add(fcell)
                    queue.append(fcell)
        return closure

    @staticmethod
    def _cells_in_bounds(rows_by_col, bounds):
        """集合内落在边界里的单元格。rows_by_col: 列 -> 升序行号列表（二分查找）。"""
        r0, r1, c0, c1 = bounds
        out = set()
        for c, rows in rows_by_col.items():
            if c0 <= c <= c1:
                lo, hi = bisect.bisect_left(rows, r0), bisect.bisect_right(rows, r1)
                out.update((r, c) for r in rows[lo:hi])
        return out

    def _evaluation_order(self, cells):
        """对公式单元格集合做拓扑排序（被依赖者在前）。返回 (有序列表, 环上单元格集合)。

        Kahn 算法，只看集合内部的边；自引用/互引用的单元格入度永远不为零，
        留在环集合里。依赖顺序错误会让 B2=A2*2、C2=A2+B2 这类菱形依赖
        用旧 B2 算 C2（集合迭代顺序随机，错误不可复现）。

        区域边：同一组区域边界（整列填充的公式完全相同）落在集合内的成员
        只算一次并缓存，按列二分定位而不是逐格比对——否则千行区域公式是 O(N²)。
        """
        cells = set(cells)
        rows_by_col = {}
        for r, c in cells:
            rows_by_col.setdefault(c, []).append(r)
        for rows in rows_by_col.values():
            rows.sort()
        members_cache = {}
        indeg = {}
        for cell in cells:
            ranges = self._formula_ranges.get(cell)
            members = ()
            if ranges:
                key = tuple(ranges)
                members = members_cache.get(key)
                if members is None:
                    members = set()
                    for bounds in ranges:
                        members |= self._cells_in_bounds(rows_by_col, bounds)
                    members_cache[key] = members
            # 去重：同一依赖既被单格引用又落在区域里只算一条边，
            # 与下方按 _dependents_of（集合）递减的口径一致
            n = len(members)
            for dep in self._formula_deps.get(cell, ()):
                if dep in cells and dep not in members:
                    n += 1
            indeg[cell] = n
        queue = deque(sorted(c for c in cells if indeg[c] == 0))
        order = []
        while queue:
            cell = queue.popleft()
            order.append(cell)
            for fcell in self._dependents_of(cell):
                if fcell in cells:
                    indeg[fcell] -= 1
                    if indeg[fcell] == 0:
                        queue.append(fcell)
        return order, cells.difference(order)

    def _recalc_dependents(self, changed_cell):
        """被引用单元格变化后，按依赖顺序重算所有（传递）依赖它的公式。

        迭代实现：千行以上的连锁公式（=A2+1 填充到底）递归会栈溢出。
        环上的公式写入 #CIRC!。
        """
        self._recalc_cells(self._closure_of([changed_cell]))

    def _recalc_cells(self, closure, emit=True, keep_unsupported=False):
        """按依赖顺序重算 closure 中的公式单元格。

        emit: 结束后对整片单元格发一次外接矩形的 dataChanged（结构操作在
        begin/endResetModel 或 begin/endInsert* 之间调用时传 False，视图会整体刷新）。
        keep_unsupported: #NAME?/#ERROR（引擎不支持的函数/语法）不覆盖现有值，
        与 evaluate_all_formulas 的口径一致。
        """
        if not closure:
            return
        order, cyclic = self._evaluation_order(closure)
        for fcell in order:
            result = self._evaluate(self.formulas[fcell])
            if keep_unsupported and result in ("#NAME?", "#ERROR"):
                continue
            self._set_cell(fcell[0], fcell[1], result)
        for fcell in cyclic:
            self._set_cell(fcell[0], fcell[1], "#CIRC!")
        if emit:
            self._emit_cells_changed(closure)

    def evaluate_all_formulas(self, keep_cached_on_error=False):
        """按依赖顺序重算全部公式并写入 df（载入公式/结构变更后调用）。

        先重建依赖索引再拓扑排序求值——按字典插入顺序求值会让引用了
        其他公式的公式读到过期值（例如删行后 C2=B2*2 用旧的 B2）。

        #NAME?/#ERROR 表示引擎无法求值（不支持的函数/语法），保留 df 中
        来自 Excel 的缓存计算值不覆盖；其余错误（#DIV/0!、#N/A、#REF! 等）
        是真实的计算结果，必须写入——否则结构变更后单元格会显示并保存
        过期的旧值。

        keep_cached_on_error: 文件加载路径置 True——文件里的缓存值是
        Excel 的计算结果，任何错误都不覆盖它（旧版应用保存的公式坐标
        偏一行，会算出 #VALUE! 等假错误，覆盖等于损坏用户数据）。
        """
        self._rebuild_all_deps()
        if not self.formulas:
            return
        order, cyclic = self._evaluation_order(self.formulas)
        for key in order:
            result = self._evaluate(self.formulas[key])
            if keep_cached_on_error:
                if not FormulaEngine.is_error(result):
                    self._set_cell(key[0], key[1], result)
            elif result not in ("#NAME?", "#ERROR"):
                self._set_cell(key[0], key[1], result)
        if not keep_cached_on_error:
            for key in cyclic:
                self._set_cell(key[0], key[1], "#CIRC!")

    def clear_formulas(self):
        """丢弃全部公式（df 中保留当前计算值），依赖索引一并清空。"""
        self.formulas.clear()
        self._clear_dep_index()

    def _coerce(self, value, col):
        """尽量保持列的数值类型；无法转换时整列转为 object。"""
        text = str(value)
        if text == "":
            return np.nan
        dtype = self._df.iloc[:, col].dtype   # 不用 df.dtypes：每次构造整表 Series
        if pd.api.types.is_numeric_dtype(dtype):
            try:
                num = float(text)
                if num.is_integer() and pd.api.types.is_integer_dtype(dtype):
                    return int(num)
                return num
            except ValueError:
                # 数值列写入文本：整列退化为 object
                self._column_to_object(col)
                return text
        return text

    def _column_to_object(self, col):
        # 按位置改列（isetitem），列名重复时 df[name] 会同时改到多列
        self._df.isetitem(col, self._df.iloc[:, col].astype(object))

    def _set_cell(self, row, col, value):
        self._invalidate_values(col)
        # 字符串写入数值列时先把整列转为 object，避免 pandas 弃用警告
        if isinstance(value, str) and self._df.iloc[:, col].dtype != object:
            self._column_to_object(col)
        try:
            self._df.iat[row, col] = value
        except (ValueError, TypeError):
            self._column_to_object(col)
            self._df.iat[row, col] = value

    def _restore_dtype(self, col, dtype):
        """撤销后尝试把列恢复到编辑前的 dtype（文本写入曾把数值列退化为 object）。"""
        series = self._df.iloc[:, col]
        if series.dtype == dtype or dtype == object:
            return
        try:
            restored = series.astype(dtype)
        except (ValueError, TypeError):
            return
        self._df.isetitem(col, restored)
        self._invalidate_values(col)

    # ---------- 撤销 / 重做 ----------

    def _push_undo(self, record):
        self._undo_stack.append(record)
        if len(self._undo_stack) > self._undo_limit:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self.historyPushed.emit()

    def take_history(self):
        """当前撤销/重做栈的副本 (undo, redo)。

        筛选切换会整表替换视图（set_dataframe 清空历史），宿主先把这一段
        视图上的历史取走保存，撤销筛选、回到同一视图时再用 set_history 放回。
        """
        return list(self._undo_stack), list(self._redo_stack)

    def set_history(self, undo, redo):
        self._undo_stack = list(undo)
        self._redo_stack = list(redo)

    # 撤销记录按首元素分派：带标记的批量/结构记录 -> 对应回放函数，
    # 否则是单格编辑记录 (row, col, old, new, old_formula, new_formula, old_dtype)
    def _replay_rename(self, record, forward):
        _, col, old_name, new_name = record
        self._rename_column_impl(col, new_name if forward else old_name)

    def _replay_clear(self, record, forward):
        if forward:
            self._apply_clear_entries(record[1])
        else:
            self._restore_clear_entries(record[1])
        self._after_batch(record[1])

    def _replay_cell(self, record, forward):
        row, col = record[0], record[1]
        self._apply_cell_record(record, forward)
        idx = self.index(row + self.HEADER_ROWS, col)
        self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
        self._recalc_dependents((row, col))
        self.modified = True

    _REPLAYERS = {
        "__rename__": "_replay_rename",
        "__color__": "_replay_colors",
        "__batch__": "_replay_clear",
        "__struct__": "_replay_struct",
        "__cells__": "_replay_cells",
    }

    def _replay(self, record, forward):
        """回放一条撤销记录（forward=False 撤销 / True 重做），不动撤销栈。"""
        getattr(self, self._REPLAYERS.get(record[0], "_replay_cell"))(record, forward)

    def undo(self):
        if not self._undo_stack:
            return False
        record = self._undo_stack.pop()
        self._replay(record, forward=False)
        self._redo_stack.append(record)
        return True

    def redo(self):
        if not self._redo_stack:
            return False
        record = self._redo_stack.pop()
        self._replay(record, forward=True)
        self._undo_stack.append(record)
        return True

    def clear_history(self):
        """清空撤销/重做栈。

        筛选状态下的结构操作由宿主同步 original_df 等底账，模型这边的
        撤销记录无法把那些底账一起回退，宿主做完后调用此方法作废历史。
        """
        self._undo_stack.clear()
        self._redo_stack.clear()

    def _apply_formula_state(self, row, col, formula):
        key = (row, col)
        if formula:
            self.formulas[key] = formula
            self._register_deps(key)
        elif key in self.formulas:
            self.formulas.pop(key, None)
            self._unregister_deps(key)

    # ---------- DataFrame 存取 ----------

    @property
    def df(self) -> pd.DataFrame:
        return self._df

    def set_dataframe(self, df: pd.DataFrame, mark_modified=False, formulas=None,
                      from_file=False):
        self.beginResetModel()
        self._invalidate_values()
        index = df.index
        if not (isinstance(index, pd.RangeIndex) and index.start == 0 and index.step == 1):
            # 模型全程假定行标签 == 行位置（排序返回标签当位置用、删行按标签 drop），
            # 筛选/切片得到的非连续索引直接进来会错位
            df = df.reset_index(drop=True)
        self._df = df
        self.structure_version += 1
        self.highlight_row = -1
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.formulas = dict(formulas) if formulas else {}
        self._clear_dep_index()
        if self.formulas:
            # 纯公式行读回来是空行会被 pandas 裁掉，把表格补齐到公式覆盖的范围
            need_rows = max(r for r, _ in self.formulas) + 1 - len(self._df)
            need_cols = max(c for _, c in self.formulas) + 1 - len(self._df.columns)
            if need_cols > 0:
                for i in range(need_cols):
                    name = self._unique_col_name(
                        FormulaEngine.col_index_to_letter(len(self._df.columns)))
                    self._df[name] = np.nan
            if need_rows > 0:
                pad = pd.DataFrame(np.full((need_rows, len(self._df.columns)), np.nan),
                                   columns=self._df.columns)
                self._df = pd.concat([self._df, pad]).reset_index(drop=True)
            self.evaluate_all_formulas(keep_cached_on_error=from_file)
        self.endResetModel()
        if mark_modified:
            self.modified = True

    def set_highlight_row(self, row):
        """设置当前整行高亮的行（视图行号），刷新新旧两行。"""
        old = self.highlight_row
        if old == row:
            return
        self.highlight_row = row
        ncols = len(self._df.columns)
        if ncols == 0:
            return
        for r in (old, row):
            if 0 <= r < len(self._df) + self.HEADER_ROWS:
                self.dataChanged.emit(self.index(r, 0), self.index(r, ncols - 1),
                                      [Qt.ItemDataRole.BackgroundRole])

    def set_cell_color(self, row, col, color_hex):
        """设置/清除单元格背景色（row 为 0 基数据行，-1 表示表头行；
        color_hex 为 None 时清除）。不入撤销栈，批量入栈用 apply_cell_colors。"""
        self._store_cell_color(row, col, color_hex)
        idx = self.index(row + self.HEADER_ROWS, col)
        self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.BackgroundRole])

    def _store_cell_color(self, row, col, color_hex):
        if color_hex:
            self.cell_colors[(row, col)] = color_hex
        else:
            self.cell_colors.pop((row, col), None)

    def apply_cell_colors(self, cells, color_hex):
        """批量设置/清除背景色并记入撤销栈（一次操作 = 一条撤销记录）。

        cells 为 (数据行, 列) 列表，行 -1 表示表头行。整片只发一次
        BackgroundRole 的 dataChanged（大选区逐格发信号会让视图刷新上万次）。
        """
        changes = []
        for row, col in cells:
            old = self.cell_colors.get((row, col))
            if old == (color_hex or None):
                continue
            changes.append((row, col, old, color_hex))
            self._store_cell_color(row, col, color_hex)
        if changes:
            self._emit_cells_changed([(r, c) for r, c, _o, _n in changes],
                                     Qt.ItemDataRole.BackgroundRole)
            self._push_undo(("__color__", changes))
            self.modified = True
            self.cellColorsChanged.emit(
                [(r, c, new) for r, c, _old, new in changes])
        return bool(changes)

    def _replay_colors(self, record, forward):
        """撤销/重做一条 "__color__" 记录：整片写回旧色/新色，只发一次信号。"""
        changes = record[1]
        applied = []
        for row, col, old, new in changes:
            color = new if forward else old
            self._store_cell_color(row, col, color)
            applied.append((row, col, color))
        self._emit_cells_changed([(r, c) for r, c, _o, _n in changes],
                                 Qt.ItemDataRole.BackgroundRole)
        self.modified = True
        self.cellColorsChanged.emit(applied)

    # ---------- 结构操作 ----------

    # ---------- 结构操作（增删行列 / 重排 / 设为表头），全部可撤销 ----------
    #
    # 每个操作分三层：
    #   _do_*      纯数据层，只改 self._df，不发信号、不动底账；
    #   公共方法   发模型信号、平移/清理公式与颜色底账、记一条 "__struct__" 撤销记录；
    #   _replay_struct  撤销/重做：反向/正向重放 _do_*，再整体恢复操作前/后的
    #                  底账快照（公式表、颜色表、各列 dtype）。
    # 底账用快照而不是"再反向平移一次"：插入/删除对公式引用的改写并非严格
    # 互逆（删行会把引用变成 #REF!），快照能保证撤销后和操作前逐字节一致。
    # 公式数量通常远小于单元格数，快照开销可忽略；被删的行列数据随记录保存。

    def _snapshot(self):
        return (dict(self.formulas), dict(self.cell_colors), list(self._df.dtypes))

    def _restore_snapshot(self, snap, evaluate=True):
        formulas, colors, dtypes = snap
        self.formulas = dict(formulas)
        self.cell_colors = dict(colors)
        if len(dtypes) == len(self._df.columns):
            for col, dtype in enumerate(dtypes):
                self._restore_dtype(col, dtype)
        if evaluate:
            self.evaluate_all_formulas()
        else:
            self._rebuild_all_deps()

    def _finish_structure(self):
        self.modified = True
        self.structure_version += 1

    def _do_insert_row(self, position):
        empty = pd.DataFrame([[np.nan] * len(self._df.columns)], columns=self._df.columns)
        self._df = pd.concat(
            [self._df.iloc[:position], empty, self._df.iloc[position:]]
        ).reset_index(drop=True)

    def _do_remove_rows(self, positions):
        removed = self._df.iloc[positions].copy()
        self._df = self._df.drop(self._df.index[positions]).reset_index(drop=True)
        return removed

    def _do_reinsert_rows(self, positions, removed):
        """把 _do_remove_rows 删掉的行按原位置放回去。"""
        total = len(self._df) + len(removed)
        kept = self._df.copy()
        kept.index = np.setdiff1d(np.arange(total), np.asarray(positions, dtype=int))
        removed = removed.copy()
        removed.index = np.asarray(positions, dtype=int)
        self._df = pd.concat([kept, removed]).sort_index().reset_index(drop=True)

    def _do_insert_column(self, position, name, values=np.nan):
        self._df.insert(position, name, values)

    def _do_remove_columns(self, positions):
        """删除列，返回 [(位置, 列名, 值数组)] 供撤销放回。"""
        removed = [(p, self._df.columns[p], self._df.iloc[:, p].to_numpy(copy=True))
                   for p in positions]
        self._df = self._df.drop(columns=[name for _p, name, _v in removed])
        return removed

    def _do_reinsert_columns(self, removed):
        for pos, name, values in sorted(removed, key=lambda t: t[0]):
            self._df.insert(pos, name, values)

    def _do_reorder(self, positions):
        self._df = self._df.iloc[list(positions)].reset_index(drop=True)

    def _do_reorder_columns(self, order):
        self._df = self._df.iloc[:, list(order)]

    def insert_row(self, position: int):
        before = self._snapshot()
        self._invalidate_values()
        # position 为数据行坐标，视图中偏移一行（表头行）
        view_pos = position + self.HEADER_ROWS
        self.beginInsertRows(QModelIndex(), view_pos, view_pos)
        self._do_insert_row(position)
        self._shift_keys(row_start=position, row_delta=1)
        self.endInsertRows()
        self._finish_structure()
        self._push_undo(("__struct__", "insert_row", position, before, self._snapshot()))

    def remove_rows(self, positions, record=True):
        """按显示位置批量删除行。positions 为升序去重列表。"""
        positions = sorted(set(positions))
        if not positions:
            return
        before = self._snapshot()
        self._invalidate_values()
        self.beginResetModel()
        removed = self._do_remove_rows(positions)
        self._remove_keys(rows=set(positions))
        self.endResetModel()
        self._finish_structure()
        if record:
            self._push_undo(("__struct__", "remove_rows", positions, removed,
                             before, self._snapshot()))

    def insert_column(self, position: int, name: str = None):
        if name is None:
            name = self._unique_col_name(tr("新列"))
        before = self._snapshot()
        self._invalidate_values()
        self.beginInsertColumns(QModelIndex(), position, position)
        self._do_insert_column(position, name)
        self._shift_keys(col_start=position, col_delta=1)
        self.endInsertColumns()
        self._finish_structure()
        self._push_undo(("__struct__", "insert_column", position, name,
                         before, self._snapshot()))

    def remove_columns(self, positions):
        positions = sorted(set(positions))
        if not positions:
            return
        before = self._snapshot()
        self._invalidate_values()
        self.beginResetModel()
        removed = self._do_remove_columns(positions)
        self._remove_keys(cols=set(positions))
        self.endResetModel()
        self._finish_structure()
        self._push_undo(("__struct__", "remove_columns", positions, removed,
                         before, self._snapshot()))

    @staticmethod
    def column_move_order(ncols, cols, target):
        """把 cols 整体挪到"间隙" target（0..ncols，指原第 target 列之前）后的新列序。

        返回 order：新第 i 列 = 原第 order[i] 列；无变化时返回 None。
        """
        cols = sorted({c for c in cols if 0 <= c < ncols})
        if not cols:
            return None
        moving = set(cols)
        rest = [c for c in range(ncols) if c not in moving]
        k = max(0, min(target, ncols)) - sum(1 for c in cols if c < target)
        order = rest[:k] + cols + rest[k:]
        return None if order == list(range(ncols)) else order

    def _apply_column_order(self, order):
        """按列序重排 df，并把公式/背景色的键与公式内的列引用跟着搬走。"""
        new_pos = {old: new for new, old in enumerate(order)}
        self._do_reorder_columns(order)
        self.formulas = {(r, new_pos[c]): v for (r, c), v in self.formulas.items()}
        self.cell_colors = {(r, new_pos[c]): v for (r, c), v in self.cell_colors.items()}
        self._rewrite_formulas(None, lambda c: new_pos.get(c, c))

    def move_columns(self, cols, target):
        """把 cols（列号集合）整体移到间隙 target 处（拖拽列头重排）。

        公式引用随列移动（与 Excel 剪切插入列一致），可撤销。
        返回新列序 order（新第 i 列 = 原第 order[i] 列）；未移动返回 None。
        """
        order = self.column_move_order(len(self._df.columns), cols, target)
        if order is None:
            return None
        before = self._snapshot()
        self._invalidate_values()
        self.beginResetModel()
        self._apply_column_order(order)
        self.endResetModel()
        self._finish_structure()
        self._push_undo(("__struct__", "move_columns", order, before, self._snapshot()))
        return order

    def _replay_struct(self, record, forward):
        """撤销（forward=False）/重做（forward=True）一条结构记录。"""
        kind, args = record[1], record[2:]
        before, after = args[-2], args[-1]
        self._invalidate_values()
        self.beginResetModel()
        if kind == "insert_row":
            (position,) = args[:-2]
            if forward:
                self._do_insert_row(position)
            else:
                self._do_remove_rows([position])
        elif kind == "remove_rows":
            positions, removed = args[:-2]
            if forward:
                self._do_remove_rows(positions)
            else:
                self._do_reinsert_rows(positions, removed)
        elif kind == "insert_column":
            position, name = args[:-2]
            if forward:
                self._do_insert_column(position, name)
            else:
                self._do_remove_columns([position])
        elif kind == "remove_columns":
            positions, removed = args[:-2]
            if forward:
                self._do_remove_columns(positions)
            else:
                self._do_reinsert_columns(removed)
        elif kind == "reorder":
            (positions,) = args[:-2]
            if forward:
                self._do_reorder(positions)
            else:
                inverse = np.empty(len(positions), dtype=int)
                inverse[np.asarray(positions, dtype=int)] = np.arange(len(positions))
                self._do_reorder(inverse)
        elif kind == "move_columns":
            (order,) = args[:-2]
            if not forward:
                inverse = np.empty(len(order), dtype=int)
                inverse[np.asarray(order, dtype=int)] = np.arange(len(order))
                order = inverse.tolist()
            # 键与公式文本由快照整体恢复，这里只挪数据
            self._do_reorder_columns(order)
        elif kind == "promote":
            old_df, new_df = args[:-2]
            self._df = (new_df if forward else old_df).copy()
        elif kind == "add_columns":
            start, pairs = args[:-2]
            if forward:
                for i, (name, values) in enumerate(pairs):
                    self._do_insert_column(start + i, name, values)
            else:
                self._do_remove_columns(list(range(start, start + len(pairs))))
        elif kind == "append_rows":
            start, n = args[:-2]
            if forward:
                self._do_append_rows(n)
            else:
                self._do_remove_rows(list(range(start, start + n)))
        # 纯行/列重排不改变任何公式的值（引用随行列一起移动），不必重算——
        # 整列填充了公式的表重算一遍要好几秒；其余结构操作照常重算
        self._restore_snapshot(after if forward else before,
                               evaluate=(kind not in ("reorder", "move_columns")))
        self.endResetModel()
        self._finish_structure()

    def _shift_keys(self, row_start=None, row_delta=0, col_start=None, col_delta=0):
        """插入行/列后平移公式/背景色的键，并同步平移公式内的引用。"""
        def shift(d):
            out = {}
            for (r, c), v in d.items():
                if row_start is not None and r >= row_start:
                    r += row_delta
                if col_start is not None and c >= col_start:
                    c += col_delta
                out[(r, c)] = v
            return out
        self.formulas = shift(self.formulas)
        self.cell_colors = shift(self.cell_colors)
        row_map = None
        if row_start is not None and row_delta:
            row_map = lambda i: i + row_delta if i >= row_start else i
        col_map = None
        if col_start is not None and col_delta:
            col_map = lambda i: i + col_delta if i >= col_start else i
        self._rewrite_formulas(row_map, col_map)

    def _rewrite_formulas(self, row_map, col_map):
        """按行/列映射重写公式引用，只重算文本被改写的公式及其依赖闭包。

        增删行列后其余公式引用的单元格位置和内容都没变，值不可能变化——
        整表重算在整列填充公式的表上要好几秒。#NAME?/#ERROR 不覆盖现有值，
        与 evaluate_all_formulas 口径一致。
        """
        changed = set()
        if self.formulas and (row_map or col_map):
            adjust = self._engine.adjust_formula_refs
            rewritten = {}
            for key, f in self.formulas.items():
                new_f = adjust(f, row_map, col_map)
                if new_f != f:
                    changed.add(key)
                rewritten[key] = new_f
            self.formulas = rewritten
        self._rebuild_all_deps()
        if changed:
            self._recalc_cells(changed | self._closure_of(changed),
                               emit=False, keep_unsupported=True)

    def _remove_keys(self, rows=None, cols=None):
        """删除行/列后丢弃对应键、压缩其余键位置，并重写公式引用。

        引用了被删行/列的公式会变成 #REF!（区域端点被删则收缩）。
        """
        rows = rows or set()
        cols = cols or set()
        sorted_rows = sorted(rows)
        sorted_cols = sorted(cols)

        # 二分计数：删 2 万行时线性扫描是 O(删除数 × 键数) 的热点
        def remap(d):
            out = {}
            for (r, c), v in d.items():
                if r in rows or c in cols:
                    continue
                r -= bisect.bisect_left(sorted_rows, r)
                c -= bisect.bisect_left(sorted_cols, c)
                out[(r, c)] = v
            return out
        self.formulas = remap(self.formulas)
        self.cell_colors = remap(self.cell_colors)

        def make_map(deleted, sorted_del):
            def mapper(i):
                if i in deleted:
                    return None
                return i - bisect.bisect_left(sorted_del, i)
            return mapper

        row_map = make_map(rows, sorted_rows) if rows else None
        col_map = make_map(cols, sorted_cols) if cols else None
        self._rewrite_formulas(row_map, col_map)

    def replace_dataframe(self, new_df: pd.DataFrame):
        """整表替换（可撤销）：分析结果回写当前 Sheet。

        旧表的公式/背景色随旧表一起进撤销记录；新表不带公式。
        """
        before = self._snapshot()
        old_df = self._df.copy()
        self._invalidate_values()
        self.beginResetModel()
        self._df = new_df.reset_index(drop=True).copy()
        self.formulas = {}
        self.cell_colors = {}
        self._clear_dep_index()
        self.endResetModel()
        self._finish_structure()
        self._push_undo(("__struct__", "promote", old_df, self._df.copy(),
                         before, self._snapshot()))

    def append_columns(self, pairs):
        """在表尾追加若干列（可撤销）。pairs: [(列名, 值序列)]，长度须与行数一致。"""
        pairs = [(str(n), np.asarray(v, dtype=object) if not isinstance(v, np.ndarray) else v)
                 for n, v in pairs]
        if not pairs:
            return []
        for name, values in pairs:
            if len(values) != len(self._df):
                raise ValueError(
                    tr("列 {} 有 {} 行，与当前表的 {} 行不一致").format(name, len(values), len(self._df)))
        before = self._snapshot()
        start = len(self._df.columns)
        used = set(map(str, self._df.columns))
        final = []
        for name, values in pairs:
            base, n, cand = name, 1, name
            while cand in used:
                cand = f"{base}_{n}"
                n += 1
            used.add(cand)
            final.append((cand, values))
        self._invalidate_values()
        self.beginInsertColumns(QModelIndex(), start, start + len(final) - 1)
        for i, (name, values) in enumerate(final):
            self._do_insert_column(start + i, name, values)
        # 引用曾被裁剪在表格边缘之外的公式（=SUM(B2:Z2)、=D2）现在能读到新列，
        # 重建依赖并重算它们，否则值永远过期
        self._recalc_touching(col_from=start)
        self.endInsertColumns()
        self._finish_structure()
        self._push_undo(("__struct__", "add_columns", start, final,
                         before, self._snapshot()))
        return [n for n, _ in final]

    def _do_append_rows(self, n):
        pad = pd.DataFrame(np.full((n, len(self._df.columns)), np.nan),
                           columns=self._df.columns)
        self._df = pd.concat([self._df, pad]).reset_index(drop=True)

    def append_rows(self, n: int) -> int:
        """在表尾追加 n 个空行（可撤销的结构操作）。返回追加前的行数，即首个新行的数据行号。

        既有单元格位置不变：公式、背景色、撤销栈全部保留（区别于用
        set_dataframe 拼接空行——那会清空撤销栈）。整数列因 NaN 会升成
        float，撤销时恢复原 dtype。引用范围曾被裁剪到表尾的公式会重算。
        """
        start = len(self._df)
        if n <= 0:
            return start
        before = self._snapshot()
        self._invalidate_values()
        view_start = start + self.HEADER_ROWS
        self.beginInsertRows(QModelIndex(), view_start, view_start + n - 1)
        self._do_append_rows(n)
        self._recalc_touching(row_from=start)
        self.endInsertRows()
        self._finish_structure()
        self._push_undo(("__struct__", "append_rows", start, n, before, self._snapshot()))
        return start

    def _recalc_touching(self, row_from=None, col_from=None):
        """表格向右/向下扩展后：重建依赖索引（区域按新边界裁剪），
        重算引用到新增行/列的公式及其依赖闭包。不发信号（调用方在结构信号之间）。"""
        self._rebuild_all_deps()
        if not self.formulas:
            return
        touched = set()
        for fcell, keys in self._formula_deps.items():
            for r, c in keys:
                if ((row_from is not None and r >= row_from)
                        or (col_from is not None and c >= col_from)):
                    touched.add(fcell)
                    break
        for (_r0, r1, _c0, c1), fcells in self._range_formulas.items():
            if ((row_from is not None and r1 >= row_from)
                    or (col_from is not None and c1 >= col_from)):
                touched.update(fcells)
        if touched:
            self._recalc_cells(touched | self._closure_of(touched),
                               emit=False, keep_unsupported=True)

    def promote_row_to_header(self, data_row: int):
        """把指定数据行提升为表头：该行值成为列名，其上方行连同该行移除。

        用于真实表头不在首行的文件（如世界银行导出的 CSV 前几行是元数据）。
        空值列名用位置字母代替，重名自动加后缀。
        """
        if not 0 <= data_row < len(self._df):
            return False
        before = self._snapshot()
        old_df = self._df.copy()
        raw = list(self._df.iloc[data_row])
        names, used = [], set()
        for i, v in enumerate(raw):
            text = "" if pd.isna(v) else str(v).strip()
            if isinstance(v, float) and v.is_integer():
                text = str(int(v))    # 年份类表头 1960.0 -> 1960
            name = text or FormulaEngine.col_index_to_letter(i)
            base, n = name, 1
            while name in used:
                name = f"{base}_{n}"
                n += 1
            used.add(name)
            names.append(name)
        # 先移除表头行及其上方行（公式/颜色/结构版本统一处理；撤销记录由本方法整体记一条）
        self.remove_rows(list(range(data_row + 1)), record=False)
        self.beginResetModel()
        self._invalidate_values()
        self._df.columns = names
        # 原样载入的文件所有列都是文本，提升表头后重新推断数值列
        # （带前导零的编码列保持文本，不丢零）
        self._df = self._df.apply(to_numeric_or_keep)
        if self._df.dtypes.eq(object).all():
            for i in range(len(self._df.columns)):
                series = self._df.iloc[:, i]
                if _has_leading_zero_text(series):
                    continue
                converted = pd.to_numeric(series, errors="coerce")
                if converted.notna().sum() >= series.notna().sum():
                    self._df.isetitem(i, converted)
        self.endResetModel()
        self.modified = True
        self._push_undo(("__struct__", "promote", old_df, self._df.copy(),
                         before, self._snapshot()))
        return True

    def rename_column(self, position: int, new_name: str):
        old_name = str(self._df.columns[position])
        if not new_name or new_name == old_name:
            # 空输入（点开表头行又没输入）静默忽略，不算错误
            return False
        if new_name in self._df.columns:
            self.renameFailed.emit(tr("列名无效或已存在"))
            return False
        self._rename_column_impl(position, new_name)
        # 记入撤销栈（带标记的记录，undo/redo 分别按旧名/新名重放）
        self._push_undo(("__rename__", position, old_name, new_name))
        return True

    def _rename_column_impl(self, position: int, new_name: str):
        """执行重命名并发出联动信号（不入撤销栈，undo/redo 复用）。"""
        old_name = str(self._df.columns[position])
        cols = list(self._df.columns)
        cols[position] = new_name
        self._df.columns = cols
        # 列名显示在视图第 0 行（水平表头是固定字母，无需刷新）
        idx = self.index(0, position)
        self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
        self.modified = True
        # 先发联动信号（宿主同步 original_df/筛选条件的列名），
        # 再重算表头依赖——重算会触发 dataChanged，筛选同步按新列名
        # 写回 original_df，顺序反了会在旧名表上创建幻影重复列
        self.columnRenamed.emit(position, old_name, new_name)
        # 引用表头的公式（如 =A1）依赖键为 (-1, col)，重命名后重算
        self._recalc_dependents((-1, position))

    def _unique_col_name(self, base):
        if base not in self._df.columns:
            return base
        i = 1
        while f"{base}{i}" in self._df.columns:
            i += 1
        return f"{base}{i}"

    @staticmethod
    def _sort_key(series):
        """列 -> 可比较的排序键。

        数值列按数值排；混合列（公式写入 #DIV/0! 后整列 object）数值在前、
        文本在后（同 Excel）——直接 sort_values 会在 str/float 比较时抛 TypeError。
        空值保持为 None，由 na_position 统一排到末尾。
        日期/时间差列原样返回：to_numeric 会把 NaT 变成 int64 最小值排到最前，
        na_position="last" 就失效了。
        """
        if (pd.api.types.is_datetime64_any_dtype(series.dtype)
                or pd.api.types.is_timedelta64_dtype(series.dtype)):
            return series
        keys = pd.to_numeric(series, errors="coerce")
        if keys.notna().sum() < series.notna().sum():
            keys = pd.Series(
                [None if pd.isna(v) else ((0, n) if n == n else (1, str(v)))
                 for v, n in zip(series.tolist(), keys.tolist())],
                index=series.index, dtype=object)
        return keys

    def sort_positions(self, keys):
        """多键排序后的新行序（positions[i] = 新第 i 行对应的旧行号），不改数据。

        keys: [(列号, 是否升序), ...]，按优先级排列；越界列忽略、重复列只取首次。
        稳定排序：键相等的行保持原相对顺序。返回 None 表示没有可用的键。
        """
        ncols = len(self._df.columns)
        seen, ordered = set(), []
        for col, ascending in keys:
            if 0 <= col < ncols and col not in seen:
                seen.add(col)
                ordered.append((col, bool(ascending)))
        if not ordered:
            return None
        frame = pd.DataFrame({i: self._sort_key(self._df.iloc[:, c])
                              for i, (c, _a) in enumerate(ordered)})
        return frame.sort_values(
            by=list(range(len(ordered))),
            ascending=[a for _c, a in ordered],
            kind="mergesort",
            na_position="last",
        ).index.tolist()

    def sort_by_keys(self, keys):
        """按多个键排序（见 sort_positions）。返回被冻结的公式数，无键时 None。"""
        positions = self.sort_positions(keys)
        if positions is None:
            return None
        return self.reorder_rows(positions)

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        return self.sort_by_keys([(column, order == Qt.SortOrder.AscendingOrder)])

    def reorder_rows(self, positions):
        """按新行序重排（positions[i] = 新第 i 行对应的旧行号）。

        公式单元格、公式内的行引用、背景色都跟随数据移动。区域引用
        不重写：覆盖全部行的区域（整列聚合）成员不变、安全保留并重算；
        含部分区域的公式在重排后成员会变成无关行，冻结为静态值
        （返回冻结数量，供调用方提示）。可撤销：撤销按逆排列复原行序，
        并恢复重排前的公式表（被冻结的公式随之复活）。
        """
        positions = list(positions)
        before = self._snapshot()
        self._invalidate_values()
        self.beginResetModel()
        frozen = 0
        if self.formulas:
            nrows = len(self._df)
            kept = {}
            for key, f in self.formulas.items():
                if self._engine.formula_has_partial_ranges(f, nrows):
                    frozen += 1  # 静态值留在 df 中随行移动
                else:
                    kept[key] = f
            self.formulas = kept
        # 旧行位置 -> 新行位置
        row_map = {old: new for new, old in enumerate(positions)}
        self._do_reorder(positions)
        if self.formulas:
            self._engine.set_dataframe(self._df)
            self.formulas = {
                (row_map.get(r, r), c): self._engine.remap_formula_rows(f, row_map)
                for (r, c), f in self.formulas.items()
            }
        if self.cell_colors:
            self.cell_colors = {
                ((row_map[r] if r >= 0 else r), c): v
                for (r, c), v in self.cell_colors.items()
                if r in row_map or r < 0   # 表头行颜色（-1）不随行序移动
            }
        # 公式值随行移动、引用已重写到新行号，结果不变：只重建依赖索引不重算
        # （含部分区域的公式已冻结；整列区域成员不变）
        self._rebuild_all_deps()
        self.endResetModel()
        self._finish_structure()
        self._push_undo(("__struct__", "reorder", positions, before, self._snapshot()))
        return frozen
