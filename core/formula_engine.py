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

    def _replace_functions(self, expr: str) -> str:
        """将 Excel 函数替换为 Python 表达式"""
        # CONCAT - 字符串连接（支持多参数）
        expr = re.sub(r'CONCAT\s*\(([^)]+)\)', r'_concat(\1)', expr, flags=re.IGNORECASE)
        # CONCATENATE - 同 CONCAT
        expr = re.sub(r'CONCATENATE\s*\(([^)]+)\)', r'_concat(\1)', expr, flags=re.IGNORECASE)
        # LEFT(text, num) - 取左边字符
        expr = re.sub(r'LEFT\s*\(([^,]+),([^)]+)\)', r'str(\1)[:int(\2)]', expr, flags=re.IGNORECASE)
        # RIGHT(text, num) - 取右边字符
        expr = re.sub(r'RIGHT\s*\(([^,]+),([^)]+)\)', r'str(\1)[-int(\2):]', expr, flags=re.IGNORECASE)
        # MID(text, start, num) - 取中间字符
        expr = re.sub(r'MID\s*\(([^,]+),([^,]+),([^)]+)\)', r'str(\1)[int(\2)-1:int(\2)-1+int(\3)]', expr, flags=re.IGNORECASE)
        # LEN(text) - 字符串长度
        expr = re.sub(r'LEN\s*\(([^)]+)\)', r'len(str(\1))', expr, flags=re.IGNORECASE)
        # UPPER(text) - 转大写
        expr = re.sub(r'UPPER\s*\(([^)]+)\)', r'str(\1).upper()', expr, flags=re.IGNORECASE)
        # LOWER(text) - 转小写
        expr = re.sub(r'LOWER\s*\(([^)]+)\)', r'str(\1).lower()', expr, flags=re.IGNORECASE)
        # TRIM(text) - 去除空格
        expr = re.sub(r'TRIM\s*\(([^)]+)\)', r'str(\1).strip()', expr, flags=re.IGNORECASE)
        # SUM
        expr = re.sub(r'SUM\s*\(([^)]+)\)', r'sum(\1)', expr, flags=re.IGNORECASE)
        # AVERAGE
        expr = re.sub(r'AVERAGE\s*\(([^)]+)\)', r'(_avg(\1))', expr, flags=re.IGNORECASE)
        # MAX
        expr = re.sub(r'MAX\s*\(([^)]+)\)', r'max(\1)', expr, flags=re.IGNORECASE)
        # MIN
        expr = re.sub(r'MIN\s*\(([^)]+)\)', r'min(\1)', expr, flags=re.IGNORECASE)
        # COUNT
        expr = re.sub(r'COUNT\s*\(([^)]+)\)', r'_count(\1)', expr, flags=re.IGNORECASE)
        # IF(condition, true_val, false_val)
        expr = re.sub(r'IF\s*\(([^,]+),([^,]+),([^)]+)\)', r'((\2) if (\1) else (\3))', expr, flags=re.IGNORECASE)
        # ABS
        expr = re.sub(r'ABS\s*\(([^)]+)\)', r'abs(\1)', expr, flags=re.IGNORECASE)
        # ROUND
        expr = re.sub(r'ROUND\s*\(([^,]+),([^)]+)\)', r'round(\1,int(\2))', expr, flags=re.IGNORECASE)
        # POWER(base, exp)
        expr = re.sub(r'POWER\s*\(([^,]+),([^)]+)\)', r'pow(\1,\2)', expr, flags=re.IGNORECASE)
        # SQRT
        expr = re.sub(r'SQRT\s*\(([^)]+)\)', r'(\1)**0.5', expr, flags=re.IGNORECASE)
        # MOD(num, divisor)
        expr = re.sub(r'MOD\s*\(([^,]+),([^)]+)\)', r'((\1)%(\2))', expr, flags=re.IGNORECASE)

        return expr

    def _safe_eval(self, expr: str) -> Any:
        """安全求值，限制可用函数"""
        def _avg(values):
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

        allowed_names = {
            'sum': sum, 'max': max, 'min': min, 'len': len,
            'abs': abs, 'round': round, 'int': int, 'float': float,
            'str': str, 'pow': pow,
            'True': True, 'False': False,
            '_avg': _avg, '_concat': _concat, '_count': _count
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
