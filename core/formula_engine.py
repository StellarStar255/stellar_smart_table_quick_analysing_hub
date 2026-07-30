"""
公式引擎模块 - Excel 公式解析和计算
解耦版本：通过参数传入DataFrame而不是依赖GUI对象
"""
import re
from typing import Any, Tuple, List, Set, Optional
import pandas as pd

from qtui.i18n import tr


class FormulaEngine:
    """Excel 公式解析和计算引擎（解耦版本）"""

    # 单元格引用正则：匹配 A1, B2, $A$1 等
    CELL_REF_PATTERN = re.compile(r'\$?([A-Z]+)\$?(\d+)', re.IGNORECASE)
    # 区域引用正则：匹配 A1:B10
    RANGE_REF_PATTERN = re.compile(r'\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)', re.IGNORECASE)

    def __init__(self, df: Optional[pd.DataFrame] = None):
        """
        初始化公式引擎

        Args:
            df: 可选的DataFrame，也可以在evaluate时传入
        """
        self._df = df

    def set_dataframe(self, df: pd.DataFrame):
        """设置数据源DataFrame"""
        self._df = df

    @property
    def df(self) -> Optional[pd.DataFrame]:
        """获取当前DataFrame"""
        return self._df

    @staticmethod
    def col_letter_to_index(col_letter: str) -> int:
        """列字母转0基索引：A->0, B->1, Z->25, AA->26"""
        result = 0
        for char in col_letter.upper():
            result = result * 26 + (ord(char) - ord('A') + 1)
        return result - 1

    @staticmethod
    def col_index_to_letter(col_index: int) -> str:
        """0基索引转列字母：0->A, 1->B, 25->Z, 26->AA"""
        result = ""
        col_index += 1
        while col_index > 0:
            col_index, remainder = divmod(col_index - 1, 26)
            result = chr(ord('A') + remainder) + result
        return result

    def parse_cell_ref(self, ref: str, df: Optional[pd.DataFrame] = None) -> Tuple[int, str]:
        """解析单元格引用，返回 (row_index, col_name)"""
        df = df if df is not None else self._df
        match = self.CELL_REF_PATTERN.match(ref)
        if not match:
            raise ValueError(tr("无效的单元格引用: {}").format(ref))

        col_letter = match.group(1).upper()
        row_num = int(match.group(2))

        col_index = self.col_letter_to_index(col_letter)
        row_index = row_num - 1

        if df is not None and col_index < len(df.columns):
            col_name = df.columns[col_index]
        else:
            col_name = col_letter

        return (row_index, col_name)

    def parse_range_ref(self, range_ref: str, df: Optional[pd.DataFrame] = None) -> List[Tuple[int, str]]:
        """解析区域引用，返回所有单元格坐标列表"""
        df = df if df is not None else self._df
        match = self.RANGE_REF_PATTERN.match(range_ref)
        if not match:
            raise ValueError(tr("无效的区域引用: {}").format(range_ref))

        start_col = self.col_letter_to_index(match.group(1).upper())
        start_row = int(match.group(2)) - 1
        end_col = self.col_letter_to_index(match.group(3).upper())
        end_row = int(match.group(4)) - 1

        cells = []
        for row in range(start_row, end_row + 1):
            for col in range(start_col, end_col + 1):
                if df is not None and col < len(df.columns):
                    col_name = df.columns[col]
                else:
                    col_name = self.col_index_to_letter(col)
                cells.append((row, col_name))

        return cells

    def extract_dependencies(self, formula: str, df: Optional[pd.DataFrame] = None) -> Set[Tuple[int, str]]:
        """从公式中提取所有被引用的单元格"""
        df = df if df is not None else self._df
        dependencies = set()

        # 先处理区域引用
        for match in self.RANGE_REF_PATTERN.finditer(formula):
            try:
                range_cells = self.parse_range_ref(match.group(), df)
                dependencies.update(range_cells)
            except ValueError:
                pass

        # 移除区域引用后处理单个单元格引用
        formula_without_ranges = self.RANGE_REF_PATTERN.sub('', formula)
        for match in self.CELL_REF_PATTERN.finditer(formula_without_ranges):
            try:
                cell = self.parse_cell_ref(match.group(), df)
                dependencies.add(cell)
            except ValueError:
                pass

        return dependencies

    def get_cell_value(self, row_index: int, col_name: str, df: Optional[pd.DataFrame] = None) -> Any:
        """获取单元格的值"""
        df = df if df is not None else self._df
        if df is None:
            return 0
        if row_index < 0 or row_index >= len(df):
            return 0
        if col_name not in df.columns:
            return 0

        value = df.at[row_index, col_name]

        if pd.isna(value):
            return 0

        try:
            return float(value)
        except (ValueError, TypeError):
            return value

    def evaluate(self, formula: str, df: Optional[pd.DataFrame] = None) -> Any:
        """
        计算公式

        Args:
            formula: 公式字符串，如 "=A1+B1"
            df: 数据源DataFrame，不指定则使用初始化时设置的

        Returns:
            计算结果
        """
        df = df if df is not None else self._df
        if not formula.startswith('='):
            return formula

        expr = formula[1:]

        try:
            # 替换区域引用为值列表
            def replace_range(match):
                cells = self.parse_range_ref(match.group(), df)
                values = [self.get_cell_value(r, c, df) for r, c in cells]
                numeric_values = [v for v in values if isinstance(v, (int, float))]
                return str(numeric_values)

            expr = self.RANGE_REF_PATTERN.sub(replace_range, expr)

            # 替换单元格引用为实际值
            def replace_cell(match):
                row, col = self.parse_cell_ref(match.group(), df)
                value = self.get_cell_value(row, col, df)
                if isinstance(value, str):
                    return f'"{value}"'
                return str(value)

            expr = self.CELL_REF_PATTERN.sub(replace_cell, expr)

            # 替换函数名为 Python 表达式
            expr = self._replace_functions(expr)

            # 安全求值
            result = self._safe_eval(expr)

            # 格式化结果
            if isinstance(result, float):
                if result == int(result):
                    return int(result)
                return round(result, 10)
            return result

        except Exception:
            return "#ERROR"

    # Excel 函数名 -> 求值环境中的实现名（长名在前，避免 CONCAT 抢先匹配 CONCATENATE）
    FUNC_MAP = {
        'CONCATENATE': '_concat', 'CONCAT': '_concat',
        'AVERAGE': '_avg', 'COUNT': '_count',
        'LEFT': '_left', 'RIGHT': '_right', 'MID': '_mid',
        'LEN': '_len', 'UPPER': '_upper', 'LOWER': '_lower', 'TRIM': '_trim',
        'SUM': '_sum', 'MAX': '_max', 'MIN': '_min',
        'IF': '_if', 'ABS': 'abs', 'ROUND': '_round',
        'POWER': 'pow', 'SQRT': '_sqrt', 'MOD': '_mod',
    }

    _FUNC_NAME_PATTERN = re.compile(
        r'\b(' + '|'.join(FUNC_MAP) + r')\s*\(', re.IGNORECASE
    )

    def _replace_functions(self, expr: str) -> str:
        """将 Excel 函数名替换为求值环境中的实现名。

        只改写函数名本身、不用正则切分参数，参数结构交给表达式
        编译器处理，因此嵌套调用（如 IF(SUM(A1:A3)>10, ...)）可以正常工作。
        """
        return self._FUNC_NAME_PATTERN.sub(
            lambda m: self.FUNC_MAP[m.group(1).upper()] + '(', expr
        )

    def _safe_eval(self, expr: str) -> Any:
        """安全求值，限制可用函数"""
        def _flat_numeric(args):
            # 展平区域产生的列表并只保留数值，供聚合函数使用
            values = []
            for a in args:
                items = a if isinstance(a, (list, tuple)) else [a]
                values.extend(
                    v for v in items
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                )
            return values

        def _sum(*args):
            return sum(_flat_numeric(args))

        def _max(*args):
            values = _flat_numeric(args)
            return max(values) if values else 0

        def _min(*args):
            values = _flat_numeric(args)
            return min(values) if values else 0

        def _avg(*args):
            values = _flat_numeric(args)
            if not values:
                return 0
            return sum(values) / len(values)

        def _concat(*args):
            # 连接时，整数不显示小数点
            parts = []
            for a in args:
                if isinstance(a, float) and a == int(a):
                    parts.append(str(int(a)))
                else:
                    parts.append(str(a))
            return ''.join(parts)

        def _count(*args):
            # 处理列表或多个参数
            total = 0
            for a in args:
                if isinstance(a, (list, tuple)):
                    total += len(a)
                else:
                    total += 1
            return total

        def _if(condition, true_val, false_val):
            return true_val if condition else false_val

        def _left(text, num=1):
            return str(text)[:int(num)]

        def _right(text, num=1):
            num = int(num)
            return str(text)[-num:] if num > 0 else ''

        def _mid(text, start, num):
            start = int(start)
            return str(text)[start - 1:start - 1 + int(num)]

        def _round(value, digits=0):
            return round(value, int(digits))

        allowed_names = {
            'sum': sum, 'max': max, 'min': min, 'len': len,
            'abs': abs, 'round': round, 'int': int, 'float': float,
            'str': str, 'pow': pow,
            'True': True, 'False': False,
            '_avg': _avg, '_concat': _concat, '_count': _count,
            '_sum': _sum, '_max': _max, '_min': _min, '_if': _if,
            '_left': _left, '_right': _right, '_mid': _mid,
            '_len': lambda text: len(str(text)),
            '_upper': lambda text: str(text).upper(),
            '_lower': lambda text: str(text).lower(),
            '_trim': lambda text: str(text).strip(),
            '_round': _round,
            '_sqrt': lambda value: value ** 0.5,
            '_mod': lambda num, divisor: num % divisor,
        }

        code = compile(expr, '<formula>', 'eval')

        for name in code.co_names:
            if name not in allowed_names:
                raise ValueError(tr("不支持的函数: {}").format(name))

        return eval(code, {"__builtins__": {}}, allowed_names)


# 兼容旧版本的包装类（接受gui参数）
class FormulaEngineCompat(FormulaEngine):
    """兼容旧版本的公式引擎包装类"""

    def __init__(self, gui):
        """
        初始化，接受GUI对象以保持兼容性

        Args:
            gui: 主窗口GUI实例，通过gui.df访问数据
        """
        super().__init__()
        self._gui = gui

    @property
    def df(self):
        """从GUI获取DataFrame"""
        return self._gui.df if self._gui else None

    @property
    def gui(self):
        """保持对gui的兼容访问"""
        return self._gui
