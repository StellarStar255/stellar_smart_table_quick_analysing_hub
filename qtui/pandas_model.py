# -*- coding: utf-8 -*-
"""
PandasTableModel - 基于 QAbstractTableModel 的 pandas DataFrame 模型

与 Tkinter Treeview 不同，Qt 的 Model/View 架构是虚拟化的：
视图只请求可见单元格的数据，因此无论多少行都不需要分批渲染或列分页。
"""

from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex, QVariant, pyqtSignal
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
_HEADER_ROW_BRUSH = QColor(127, 127, 127, 42)  # 表头行底色（浅/深主题都可读）
_HEADER_ROW_FONT = QFont()
_HEADER_ROW_FONT.setBold(True)


class PandasTableModel(QAbstractTableModel):
    """把 pandas DataFrame 直接暴露给 QTableView 的可编辑模型。"""

    # 列重命名成功（col, 旧名, 新名）——宿主窗口据此同步筛选条件/original_df 等
    columnRenamed = pyqtSignal(int, str, str)
    # 列重命名失败（提示文本）——表头行内联编辑没有对话框，宿主用状态栏反馈
    renameFailed = pyqtSignal(str)

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
            info = (series.to_numpy(), pd.api.types.is_numeric_dtype(series.dtype))
            self._col_arrays[col] = info
        return info

    def _cell_value(self, row, col):
        return self._col_info(col)[0][row]

    def _invalidate_values(self):
        self._col_arrays.clear()

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
            value = self._col_info(index.column())[0][row]
            if type(value) is str:      # 最常见情况直接返回
                return value
            if pd.isna(value):
                return ""
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            return str(value)
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

        self._push_undo((row, col, old, self._df.iat[row, col], old_formula, new_formula))
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])
        self._recalc_dependents(key)
        self.modified = True
        return True

    # ---------- 公式 ----------

    def _evaluate(self, formula):
        # 引擎的 `df or self._df` 写法不接受显式传入 DataFrame，
        # 统一用 set_dataframe 注入
        self._engine.set_dataframe(self._df)
        return self._engine.evaluate(formula)

    def shift_formula(self, formula, row_delta, col_delta):
        """复制/填充公式时按偏移平移相对引用（委托给引擎）。"""
        return self._engine.shift_formula(formula, row_delta, col_delta)

    def _register_deps(self, formula_cell):
        """登记公式对其他单元格的依赖（引擎返回列名，转为列位置）。"""
        self._unregister_deps(formula_cell)
        formula = self.formulas.get(formula_cell)
        if not formula:
            return
        col_pos = {str(c): i for i, c in enumerate(self._df.columns)}
        self._engine.set_dataframe(self._df)
        for row, col_name in self._engine.extract_dependencies(formula):
            col = col_pos.get(str(col_name))
            if col is not None:
                self._dependents.setdefault((row, col), set()).add(formula_cell)

    def _unregister_deps(self, formula_cell):
        for deps in self._dependents.values():
            deps.discard(formula_cell)

    def _rebuild_all_deps(self):
        self._dependents.clear()
        for key in list(self.formulas):
            self._register_deps(key)

    def _recalc_dependents(self, changed_cell, _visited=None):
        """被引用单元格变化后，重算依赖它的公式（防环）。"""
        visited = _visited if _visited is not None else set()
        for fcell in list(self._dependents.get(changed_cell, ())):
            if fcell in visited:
                continue
            visited.add(fcell)
            formula = self.formulas.get(fcell)
            if not formula:
                continue
            result = self._evaluate(formula)
            self._set_cell(fcell[0], fcell[1], result)
            idx = self.index(fcell[0] + self.HEADER_ROWS, fcell[1])
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
            self._recalc_dependents(fcell, visited)

    def evaluate_all_formulas(self, keep_cached_on_error=False):
        """重算全部公式并写入 df（载入公式后调用）。

        #NAME?/#ERROR 表示引擎无法求值（不支持的函数/语法），保留 df 中
        来自 Excel 的缓存计算值不覆盖；其余错误（#DIV/0!、#N/A、#REF! 等）
        是真实的计算结果，必须写入——否则结构变更后单元格会显示并保存
        过期的旧值。

        keep_cached_on_error: 文件加载路径置 True——文件里的缓存值是
        Excel 的计算结果，任何错误都不覆盖它（旧版应用保存的公式坐标
        偏一行，会算出 #VALUE! 等假错误，覆盖等于损坏用户数据）。
        """
        for key, formula in self.formulas.items():
            result = self._evaluate(formula)
            if keep_cached_on_error:
                if not FormulaEngine.is_error(result):
                    self._set_cell(key[0], key[1], result)
            elif result not in ("#NAME?", "#ERROR"):
                self._set_cell(key[0], key[1], result)
        self._rebuild_all_deps()

    def _coerce(self, value, col):
        """尽量保持列的数值类型；无法转换时整列转为 object。"""
        text = str(value)
        if text == "":
            return np.nan
        dtype = self._df.dtypes.iloc[col]
        if pd.api.types.is_numeric_dtype(dtype):
            try:
                num = float(text)
                if num.is_integer() and pd.api.types.is_integer_dtype(dtype):
                    return int(num)
                return num
            except ValueError:
                # 数值列写入文本：整列退化为 object
                colname = self._df.columns[col]
                self._df[colname] = self._df[colname].astype(object)
                return text
        return text

    def _set_cell(self, row, col, value):
        self._invalidate_values()
        # 字符串写入数值列时先把整列转为 object，避免 pandas 弃用警告
        if isinstance(value, str) and self._df.dtypes.iloc[col] != object:
            colname = self._df.columns[col]
            self._df[colname] = self._df[colname].astype(object)
        try:
            self._df.iat[row, col] = value
        except (ValueError, TypeError):
            colname = self._df.columns[col]
            self._df[colname] = self._df[colname].astype(object)
            self._df.iat[row, col] = value

    # ---------- 撤销 / 重做 ----------

    def _push_undo(self, record):
        self._undo_stack.append(record)
        if len(self._undo_stack) > self._undo_limit:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo(self):
        if not self._undo_stack:
            return False
        record = self._undo_stack.pop()
        if record[0] == "__rename__":
            _, col, old_name, new_name = record
            self._rename_column_impl(col, old_name)
            self._redo_stack.append(record)
            return True
        row, col, old, new, old_formula, new_formula = record
        self._apply_formula_state(row, col, old_formula)
        self._set_cell(row, col, old)
        self._redo_stack.append(record)
        idx = self.index(row + self.HEADER_ROWS, col)
        self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
        self._recalc_dependents((row, col))
        self.modified = True
        return True

    def redo(self):
        if not self._redo_stack:
            return False
        record = self._redo_stack.pop()
        if record[0] == "__rename__":
            _, col, old_name, new_name = record
            self._rename_column_impl(col, new_name)
            self._undo_stack.append(record)
            return True
        row, col, old, new, old_formula, new_formula = record
        self._apply_formula_state(row, col, new_formula)
        self._set_cell(row, col, new)
        self._undo_stack.append(record)
        idx = self.index(row + self.HEADER_ROWS, col)
        self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
        self._recalc_dependents((row, col))
        self.modified = True
        return True

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
        self._df = df
        self.structure_version += 1
        self.highlight_row = -1
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.formulas = dict(formulas) if formulas else {}
        self._dependents.clear()
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
        color_hex 为 None 时清除）。"""
        if color_hex:
            self.cell_colors[(row, col)] = color_hex
        else:
            self.cell_colors.pop((row, col), None)
        idx = self.index(row + self.HEADER_ROWS, col)
        self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.BackgroundRole])

    # ---------- 结构操作 ----------

    def insert_row(self, position: int):
        self._invalidate_values()
        # position 为数据行坐标，视图中偏移一行（表头行）
        view_pos = position + self.HEADER_ROWS
        self.beginInsertRows(QModelIndex(), view_pos, view_pos)
        empty = pd.DataFrame([[np.nan] * len(self._df.columns)], columns=self._df.columns)
        self._df = pd.concat(
            [self._df.iloc[:position], empty, self._df.iloc[position:]]
        ).reset_index(drop=True)
        self._shift_keys(row_start=position, row_delta=1)
        self.endInsertRows()
        self.modified = True
        self.structure_version += 1
        self._undo_stack.clear()
        self._redo_stack.clear()

    def remove_rows(self, positions):
        """按显示位置批量删除行。positions 为升序去重列表。"""
        if not positions:
            return
        self._invalidate_values()
        self.beginResetModel()
        self._df = self._df.drop(self._df.index[positions]).reset_index(drop=True)
        self._remove_keys(rows=set(positions))
        self.endResetModel()
        self.modified = True
        self.structure_version += 1
        self._undo_stack.clear()
        self._redo_stack.clear()

    def insert_column(self, position: int, name: str = None):
        if name is None:
            name = self._unique_col_name(tr("新列"))
        self._invalidate_values()
        self.beginInsertColumns(QModelIndex(), position, position)
        self._df.insert(position, name, np.nan)
        self._shift_keys(col_start=position, col_delta=1)
        self.endInsertColumns()
        self.modified = True
        self.structure_version += 1
        # 列位置整体平移，旧撤销记录会写错列（行操作同理已清）
        self._undo_stack.clear()
        self._redo_stack.clear()

    def remove_columns(self, positions):
        if not positions:
            return
        self._invalidate_values()
        self.beginResetModel()
        cols = [self._df.columns[p] for p in positions]
        self._df = self._df.drop(columns=cols)
        self._remove_keys(cols=set(positions))
        self.endResetModel()
        self.modified = True
        self.structure_version += 1
        self._undo_stack.clear()
        self._redo_stack.clear()

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
        if self.formulas and (row_map or col_map):
            self.formulas = {
                key: self._engine.adjust_formula_refs(f, row_map, col_map)
                for key, f in self.formulas.items()
            }
        self.evaluate_all_formulas()

    def _remove_keys(self, rows=None, cols=None):
        """删除行/列后丢弃对应键、压缩其余键位置，并重写公式引用。

        引用了被删行/列的公式会变成 #REF!（区域端点被删则收缩）。
        """
        rows = rows or set()
        cols = cols or set()
        sorted_rows = sorted(rows)
        sorted_cols = sorted(cols)

        def remap(d):
            out = {}
            for (r, c), v in d.items():
                if r in rows or c in cols:
                    continue
                r -= sum(1 for x in sorted_rows if x < r)
                c -= sum(1 for x in sorted_cols if x < c)
                out[(r, c)] = v
            return out
        self.formulas = remap(self.formulas)
        self.cell_colors = remap(self.cell_colors)

        def make_map(deleted, sorted_del):
            def mapper(i):
                if i in deleted:
                    return None
                return i - sum(1 for x in sorted_del if x < i)
            return mapper

        row_map = make_map(rows, sorted_rows) if rows else None
        col_map = make_map(cols, sorted_cols) if cols else None
        if self.formulas and (row_map or col_map):
            self.formulas = {
                key: self._engine.adjust_formula_refs(f, row_map, col_map)
                for key, f in self.formulas.items()
            }
        self.evaluate_all_formulas()

    def promote_row_to_header(self, data_row: int):
        """把指定数据行提升为表头：该行值成为列名，其上方行连同该行移除。

        用于真实表头不在首行的文件（如世界银行导出的 CSV 前几行是元数据）。
        空值列名用位置字母代替，重名自动加后缀。
        """
        if not 0 <= data_row < len(self._df):
            return False
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
        # 先移除表头行及其上方行（公式/颜色/撤销/结构版本统一处理）
        self.remove_rows(list(range(data_row + 1)))
        self.beginResetModel()
        self._invalidate_values()
        self._df.columns = names
        # 原样载入的文件所有列都是文本，提升表头后重新推断数值列
        try:
            self._df = self._df.apply(pd.to_numeric, errors="ignore")
        except TypeError:
            pass  # 新版 pandas 移除 errors="ignore" 时逐列回退
        if self._df.dtypes.eq(object).all():
            for c in self._df.columns:
                converted = pd.to_numeric(self._df[c], errors="coerce")
                if converted.notna().sum() >= self._df[c].notna().sum():
                    self._df[c] = converted
        self.endResetModel()
        self.modified = True
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

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if column < 0 or column >= len(self._df.columns):
            return
        colname = self._df.columns[column]
        positions = self._df[colname].sort_values(
            ascending=(order == Qt.SortOrder.AscendingOrder),
            kind="mergesort",
            na_position="last",
        ).index
        return self.reorder_rows(positions)

    def reorder_rows(self, positions):
        """按新行序重排（positions[i] = 新第 i 行对应的旧行号）。

        公式单元格、公式内的行引用、背景色都跟随数据移动。区域引用
        不重写：覆盖全部行的区域（整列聚合）成员不变、安全保留并重算；
        含部分区域的公式在重排后成员会变成无关行，冻结为静态值
        （返回冻结数量，供调用方提示）。结构变化，撤销栈清空。
        """
        positions = list(positions)
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
        self._df = self._df.iloc[positions].reset_index(drop=True)
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
        self.evaluate_all_formulas()
        self.endResetModel()
        self.modified = True
        self.structure_version += 1
        self._undo_stack.clear()
        self._redo_stack.clear()
        return frozen
