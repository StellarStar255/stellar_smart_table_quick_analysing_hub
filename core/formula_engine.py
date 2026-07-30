"""
公式引擎模块 - Excel 公式解析和计算
解耦版本：通过参数传入DataFrame而不是依赖GUI对象
"""
import ast
import datetime
import fnmatch
import re
from typing import Any, Tuple, List, Set, Optional
import pandas as pd

from qtui.i18n import tr


def _excel_wildcard_match(text: str, pattern: str) -> bool:
    """Excel 通配符匹配：仅 * 和 ? 有特殊含义，[ 按字面处理。

    fnmatch 会把 [seq] 当字符类，Excel 不会——把 [ 转义成 [[] 消除差异。
    """
    return fnmatch.fnmatchcase(text, pattern.replace('[', '[[]'))


class _IfCallLowering(ast.NodeTransformer):
    """把 _if(...) 调用降为 Python 条件表达式，恢复分支惰性求值。

    IF 若作为普通函数调用，两个分支都会先被求值，
    =IF(B1=0, 0, A1/B1) 这类防错写法会先触发 #DIV/0!。
    """

    def visit_Call(self, node):
        self.generic_visit(node)
        if (isinstance(node.func, ast.Name) and node.func.id == '_if'
                and not node.keywords and len(node.args) in (2, 3)):
            orelse = (node.args[2] if len(node.args) == 3
                      else ast.Constant(value=False))
            return ast.IfExp(test=node.args[0], body=node.args[1], orelse=orelse)
        return node


class FormulaNameError(ValueError):
    """公式使用了不支持的函数名 -> #NAME?"""


class FormulaNumError(ValueError):
    """数值域错误（如负数开方）-> #NUM!"""


class FormulaNAError(ValueError):
    """查找无匹配（VLOOKUP/MATCH 等）-> #N/A"""


class FormulaRefError(ValueError):
    """引用越界（如 VLOOKUP 列号超出表格）-> #REF!"""


class FormulaEngine:
    """Excel 公式解析和计算引擎（解耦版本）"""

    # 单元格引用正则：匹配 A1, B2, $A$1 等。
    # 前后守卫排除科学计数法（1E5 的 E5）等字面量片段被误认为引用
    CELL_REF_PATTERN = re.compile(
        r'(?<![\w.])\$?([A-Z]+)\$?(\d+)(?!\w)', re.IGNORECASE)
    # 区域引用正则：匹配 A1:B10
    RANGE_REF_PATTERN = re.compile(
        r'(?<![\w.])\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)(?!\w)',
        re.IGNORECASE)
    # 字符串字面量正则：双引号（支持 Excel 的 "" 转义）或单引号包裹
    STRING_PATTERN = re.compile(r'"(?:""|[^"])*"|\'[^\']*\'')

    @staticmethod
    def _decode_string_literal(literal: str) -> str:
        """去掉引号并处理 Excel 的双引号转义（"" -> "）。"""
        body = literal[1:-1]
        if literal[0] == '"':
            body = body.replace('""', '"')
        return body
    # 字符串占位符：\x00 不会出现在用户输入中，也不会被引用/函数正则误匹配
    _STRING_PLACEHOLDER = '\x00{}\x00'
    _STRING_PLACEHOLDER_PATTERN = re.compile('\x00(\\d+)\x00')

    # 所有可能的错误返回值（对齐 Excel 错误码；#ERROR 为未分类兜底）
    ERROR_VALUES = frozenset(
        {"#ERROR", "#DIV/0!", "#NAME?", "#NUM!", "#VALUE!", "#REF!", "#N/A"}
    )

    @classmethod
    def is_error(cls, value: Any) -> bool:
        """判断求值结果是否为错误值"""
        return isinstance(value, str) and value in cls.ERROR_VALUES

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
        # Excel 语义：第 1 行是表头，数据从第 2 行起（A2 -> 数据行 0，A1 -> -1 表头）
        row_index = row_num - 2

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
        start_row = int(match.group(2)) - 2
        end_col = self.col_letter_to_index(match.group(3).upper())
        end_row = int(match.group(4)) - 2

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
        """获取单元格的值；row_index == -1 表示表头行（返回列名文本，同 Excel）"""
        df = df if df is not None else self._df
        if df is None:
            return 0
        if row_index == -1:
            return str(col_name)
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
            # 日期时间统一转 ISO 字符串：显示干净、可比较（字典序即时间序）、
            # 可直接进 CONCAT/日期函数；否则 Timestamp 拼进表达式是非法语法
            if isinstance(value, datetime.datetime):  # 含 pd.Timestamp
                if (value.hour, value.minute, value.second) == (0, 0, 0):
                    return value.date().isoformat()
                return value.strftime('%Y-%m-%d %H:%M:%S')
            if isinstance(value, datetime.date):
                return value.isoformat()
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
            # 把字符串字面量抽成占位符，避免其中的 "A1"、"SUM(" 等
            # 被当作单元格引用或函数名误替换；求值前再还原。
            # 存入的是 repr 形式：反斜杠不会被当 Python 转义解释，
            # Excel 的 "" 转义也在此处解码
            strings: List[str] = []

            def stash(literal: str) -> str:
                strings.append(literal)
                return self._STRING_PLACEHOLDER.format(len(strings) - 1)

            expr = self.STRING_PATTERN.sub(
                lambda m: stash(repr(self._decode_string_literal(m.group()))),
                expr)

            # 引用越界（如复制平移出表格）直接报 #REF!
            if '#REF!' in expr:
                return "#REF!"

            # Excel 风格比较符转 Python 风格：= -> ==, <> -> !=
            expr = self._normalize_operators(expr)

            # 替换区域引用为嵌套行列表 [[行1...], [行2...]]——查找函数
            # （VLOOKUP/INDEX 等）需要二维结构，聚合函数会递归展平。
            # 文本值占位保护，避免其中的 "A1" 等被后续替换误伤
            def replace_range(match):
                start_col = self.col_letter_to_index(match.group(1).upper())
                start_row = int(match.group(2)) - 2
                end_col = self.col_letter_to_index(match.group(3).upper())
                end_row = int(match.group(4)) - 2
                row_parts = []
                for r in range(start_row, end_row + 1):
                    parts = []
                    for ci in range(start_col, end_col + 1):
                        if df is not None and ci < len(df.columns):
                            col_name = df.columns[ci]
                        else:
                            col_name = self.col_index_to_letter(ci)
                        v = self.get_cell_value(r, col_name, df)
                        if isinstance(v, str):
                            parts.append(stash(repr(v)))
                        else:
                            parts.append(str(v))
                    row_parts.append('[' + ', '.join(parts) + ']')
                return '[' + ', '.join(row_parts) + ']'

            expr = self.RANGE_REF_PATTERN.sub(replace_range, expr)

            # 替换单元格引用为实际值
            def replace_cell(match):
                row, col = self.parse_cell_ref(match.group(), df)
                value = self.get_cell_value(row, col, df)
                if isinstance(value, str):
                    # 文本值同样占位保护，repr 保证引号和转义合法
                    return stash(repr(value))
                return str(value)

            expr = self.CELL_REF_PATTERN.sub(replace_cell, expr)

            # 替换函数名为 Python 表达式
            expr = self._replace_functions(expr)

            # 还原字符串字面量
            expr = self._STRING_PLACEHOLDER_PATTERN.sub(
                lambda m: strings[int(m.group(1))], expr
            )

            # 安全求值
            result = self._safe_eval(expr)

            # 复数（如对负数开偶次方）视为错误，与 Excel 的 #NUM! 一致
            if isinstance(result, complex):
                return "#NUM!"

            # 格式化结果
            if isinstance(result, float):
                if result == int(result):
                    return int(result)
                return round(result, 10)
            return result

        except ZeroDivisionError:
            return "#DIV/0!"
        except FormulaNameError:
            return "#NAME?"
        except FormulaNumError:
            return "#NUM!"
        except FormulaNAError:
            return "#N/A"
        except FormulaRefError:
            return "#REF!"
        except TypeError:
            return "#VALUE!"
        except Exception:
            return "#ERROR"

    # Excel 函数名 -> 求值环境中的实现名（长名在前，避免 CONCAT 抢先匹配 CONCATENATE）
    FUNC_MAP = {
        'CONCATENATE': '_concat', 'CONCAT': '_concat',
        'AVERAGEIF': '_averageif', 'AVERAGE': '_avg',
        'COUNTIF': '_countif', 'COUNTA': '_counta', 'COUNT': '_count',
        'SUMIF': '_sumif', 'SUM': '_sum',
        'LEFT': '_left', 'RIGHT': '_right', 'MID': '_mid',
        'LEN': '_len', 'UPPER': '_upper', 'LOWER': '_lower', 'TRIM': '_trim',
        'MAX': '_max', 'MIN': '_min',
        'IF': '_if', 'AND': '_and', 'OR': '_or', 'NOT': '_not',
        'ABS': 'abs', 'ROUND': '_round',
        'POWER': 'pow', 'SQRT': '_sqrt', 'MOD': '_mod',
        'VLOOKUP': '_vlookup', 'XLOOKUP': '_xlookup',
        'INDEX': '_index', 'MATCH': '_match',
        'TODAY': '_today', 'NOW': '_now',
        'DATEDIF': '_datedif', 'DATE': '_date',
        'YEAR': '_year', 'MONTH': '_month',
        'DAYS': '_days', 'DAY': '_day', 'WEEKDAY': '_weekday',
    }

    _FUNC_NAME_PATTERN = re.compile(
        r'\b(' + '|'.join(FUNC_MAP) + r')\s*\(', re.IGNORECASE
    )
    _BOOL_LITERAL_PATTERN = re.compile(r'\b(TRUE|FALSE)\b', re.IGNORECASE)

    # 重映射用：单独捕获行号前的 $（绝对行引用不随排序移动）
    _REMAP_CELL_PATTERN = re.compile(
        r'(?<![\w.])(\$?[A-Z]+)(\$?)(\d+)(?!\w)', re.IGNORECASE)

    def formula_has_partial_ranges(self, formula: str, nrows: int) -> bool:
        """公式是否含未覆盖全部数据行的区域引用。

        整表重排（排序）后这类区域的成员会变成落在原位置的无关行，
        无法用重写引用表达，调用方应将其冻结为静态值。
        覆盖全部行的区域（如整列聚合）成员不变，可以安全保留。
        """
        if not formula.startswith('='):
            return False
        expr = self.STRING_PATTERN.sub('', formula)
        for m in self.RANGE_REF_PATTERN.finditer(expr):
            r1 = int(m.group(2)) - 2
            r2 = int(m.group(4)) - 2
            if max(r1, r2) < 0:
                continue  # 纯表头行区域（如 A1:C1）：排序不动表头，安全
            # min <= 0 表示从表头行或首个数据行开始，覆盖顶部
            if min(r1, r2) > 0 or max(r1, r2) < nrows - 1:
                return True
        return False

    def remap_formula_rows(self, formula: str, row_map: dict) -> str:
        """按 row_map（旧 0 基行号 -> 新 0 基行号）重写公式中的行引用。

        用于排序后让公式引用跟随数据移动。规则：
        - 字符串字面量跳过
        - 区域引用（A1:B10）保持不变——整列聚合的成员排序后不变，重写反而破坏
        - 绝对行引用（A$1）保持不变
        - 不在 row_map 中的行号保持不变
        """
        if not formula.startswith('='):
            return formula

        strings: List[str] = []

        def stash(m):
            strings.append(m.group())
            return self._STRING_PLACEHOLDER.format(len(strings) - 1)

        expr = self.STRING_PATTERN.sub(stash, formula)
        # 区域引用也占位保护，避免其端点被当作单个引用重写
        expr = self.RANGE_REF_PATTERN.sub(stash, expr)

        def remap(m):
            col_part, row_abs, row_num = m.group(1), m.group(2), int(m.group(3))
            if row_abs:
                return m.group()
            # 文本行号 -> 数据行（-2）；表头行（数据 -1）不在 map 中保持不变
            new_row = row_map.get(row_num - 2)
            if new_row is None:
                return m.group()
            return f'{col_part}{new_row + 2}'

        expr = self._REMAP_CELL_PATTERN.sub(remap, expr)
        return self._STRING_PLACEHOLDER_PATTERN.sub(
            lambda m: strings[int(m.group(1))], expr
        )

    # 平移用：分别捕获列前 $、列字母、行前 $、行号
    _SHIFT_CELL_PATTERN = re.compile(
        r'(?<![\w.])(\$?)([A-Z]+)(\$?)(\d+)(?!\w)', re.IGNORECASE)
    # 区域引用（带 $ 捕获）
    _RANGE_PARTS_PATTERN = re.compile(
        r'(?<![\w.])(\$?)([A-Z]+)(\$?)(\d+)\s*:\s*(\$?)([A-Z]+)(\$?)(\d+)(?!\w)',
        re.IGNORECASE
    )

    def adjust_formula_refs(self, formula: str, row_map=None, col_map=None) -> str:
        """插入/删除行列后重写公式引用。

        row_map/col_map: 旧 0 基索引 -> 新索引的函数，返回 None 表示该
        行/列已删除。与 Excel 一致：
        - 单元格引用被删除 -> #REF!
        - 区域端点被删除时区域向内收缩，整个区域被删除 -> #REF!
        - 绝对引用（$）同样调整——$ 只固定复制填充，不固定结构变化
        - 字符串字面量跳过
        """
        if not formula.startswith('=') or (row_map is None and col_map is None):
            return formula

        strings: List[str] = []

        def stash(text: str) -> str:
            strings.append(text)
            return self._STRING_PLACEHOLDER.format(len(strings) - 1)

        expr = self.STRING_PATTERN.sub(lambda m: stash(m.group()), formula)

        def survive(lo, hi, mapper, from_low):
            # 从区域一端向内找第一个未被删除的索引
            indices = range(lo, hi + 1) if from_low else range(hi, lo - 1, -1)
            for i in indices:
                new = mapper(i)
                if new is not None:
                    return new
            return None

        def adjust_range(m):
            c1a, c1, r1a, r1, c2a, c2, r2a, r2 = m.groups()
            # 文本行号 -> 数据行索引（-2）；表头行为 -1，映射函数会原样保留
            row1, row2 = int(r1) - 2, int(r2) - 2
            col1 = self.col_letter_to_index(c1.upper())
            col2 = self.col_letter_to_index(c2.upper())
            if row_map is not None:
                row1, row2 = (survive(row1, row2, row_map, True),
                              survive(row1, row2, row_map, False))
                if row1 is None:
                    return stash('#REF!')
            if col_map is not None:
                col1, col2 = (survive(col1, col2, col_map, True),
                              survive(col1, col2, col_map, False))
                if col1 is None:
                    return stash('#REF!')
            # 占位保护调整结果，避免下面的单元格替换再动它
            return stash(
                f'{c1a}{self.col_index_to_letter(col1)}{r1a}{row1 + 2}:'
                f'{c2a}{self.col_index_to_letter(col2)}{r2a}{row2 + 2}'
            )

        expr = self._RANGE_PARTS_PATTERN.sub(adjust_range, expr)

        def adjust_cell(m):
            col_abs, col_letter, row_abs, row_num = m.groups()
            row = int(row_num) - 2
            col = self.col_letter_to_index(col_letter.upper())
            new_row = row_map(row) if row_map is not None else row
            new_col = col_map(col) if col_map is not None else col
            if new_row is None or new_col is None:
                return '#REF!'
            return (f'{col_abs}{self.col_index_to_letter(new_col)}'
                    f'{row_abs}{new_row + 2}')

        expr = self._SHIFT_CELL_PATTERN.sub(adjust_cell, expr)
        return self._STRING_PLACEHOLDER_PATTERN.sub(
            lambda m: strings[int(m.group(1))], expr
        )

    def shift_formula(self, formula: str, row_delta: int, col_delta: int) -> str:
        """按偏移量平移公式中的相对引用（复制/填充公式时用）。

        与 Excel 一致：$A$1 全固定、$A1 固定列、A$1 固定行，其余随
        偏移平移；区域引用的两个端点各自平移。平移越界的引用替换为
        #REF!，求值时整条公式报 #REF!。字符串字面量跳过。
        """
        if not formula.startswith('=') or (row_delta == 0 and col_delta == 0):
            return formula

        strings: List[str] = []

        def stash(m):
            strings.append(m.group())
            return self._STRING_PLACEHOLDER.format(len(strings) - 1)

        expr = self.STRING_PATTERN.sub(stash, formula)

        def shift(m):
            col_abs, col_letter, row_abs, row_num = m.groups()
            col_idx = self.col_letter_to_index(col_letter.upper())
            row_idx = int(row_num) - 1
            if not col_abs:
                col_idx += col_delta
            if not row_abs:
                row_idx += row_delta
            if col_idx < 0 or row_idx < 0:
                return '#REF!'
            return (f'{col_abs}{self.col_index_to_letter(col_idx)}'
                    f'{row_abs}{row_idx + 1}')

        expr = self._SHIFT_CELL_PATTERN.sub(shift, expr)
        return self._STRING_PLACEHOLDER_PATTERN.sub(
            lambda m: strings[int(m.group(1))], expr
        )

    @staticmethod
    def _normalize_operators(expr: str) -> str:
        """把 Excel 风格比较符转成 Python 风格：= -> ==, <> -> !=

        逐字符扫描并跳过字符串字面量，不会误改 "a=b" 这类文本；
        已经是 Python 风格的 ==, <=, >=, != 保持原样。
        """
        result = []
        i = 0
        n = len(expr)
        in_str = None  # 当前所在字符串字面量的引号字符
        while i < n:
            ch = expr[i]
            if in_str:
                result.append(ch)
                if ch == in_str:
                    in_str = None
                i += 1
                continue
            if ch in ('"', "'"):
                in_str = ch
                result.append(ch)
                i += 1
                continue
            if expr.startswith('<>', i):
                result.append('!=')
                i += 2
                continue
            if ch == '=':
                prev = result[-1][-1] if result else ''
                next_ch = expr[i + 1] if i + 1 < n else ''
                if prev in ('=', '<', '>', '!') or next_ch == '=':
                    result.append(ch)
                else:
                    result.append('==')
                i += 1
                continue
            result.append(ch)
            i += 1
        return ''.join(result)

    def _replace_functions(self, expr: str) -> str:
        """将 Excel 函数名替换为求值环境中的实现名。

        只改写函数名本身、不用正则切分参数，参数结构交给表达式
        编译器处理，因此嵌套调用（如 IF(SUM(A1:A3)>10, ...)）可以正常工作。
        """
        expr = self._FUNC_NAME_PATTERN.sub(
            lambda m: self.FUNC_MAP[m.group(1).upper()] + '(', expr
        )
        # Excel 布尔字面量 TRUE/FALSE -> Python True/False
        return self._BOOL_LITERAL_PATTERN.sub(
            lambda m: 'True' if m.group(1).upper() == 'TRUE' else 'False', expr
        )

    @staticmethod
    def _criteria_predicate(criteria):
        """把 COUNTIF/SUMIF/AVERAGEIF 的条件转成谓词函数。

        支持：">10"、">=10"、"<5"、"<=5"、"<>x"、"=x"、纯值相等，
        以及文本通配符 * 和 ?（文本比较不区分大小写，与 Excel 一致）。
        """
        def is_num(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool)

        if not isinstance(criteria, str):
            return lambda v: is_num(v) and v == criteria

        m = re.match(r'^(>=|<=|<>|>|<|=)(.*)$', criteria)
        if m:
            op, rest = m.group(1), m.group(2).strip()
            try:
                target = float(rest)
                numeric = True
            except ValueError:
                target = rest.lower()
                numeric = False

            def pred(v):
                if numeric:
                    if not is_num(v):
                        return False
                    a, b = v, target
                else:
                    a, b = str(v).lower(), target
                if op == '=':
                    return a == b
                if op == '<>':
                    return a != b
                if op == '>':
                    return a > b
                if op == '>=':
                    return a >= b
                if op == '<':
                    return a < b
                return a <= b

            return pred

        if '*' in criteria or '?' in criteria:
            pattern = criteria.lower()
            return lambda v: _excel_wildcard_match(str(v).lower(), pattern)

        try:
            num_target = float(criteria)
            return lambda v: ((is_num(v) and v == num_target)
                              or str(v).lower() == criteria.lower())
        except ValueError:
            return lambda v: str(v).lower() == criteria.lower()

    def _safe_eval(self, expr: str) -> Any:
        """安全求值，限制可用函数"""
        def _flatten(args):
            # 递归展平（区域现在是嵌套行列表）
            values = []
            for a in args:
                if isinstance(a, (list, tuple)):
                    values.extend(_flatten(a))
                else:
                    values.append(a)
            return values

        def _is_num(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool)

        def _flat_numeric(args):
            # 展平并只保留数值，供聚合函数使用
            return [v for v in _flatten(args) if _is_num(v)]

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
            # 与 Excel 一致：只数数值
            return len(_flat_numeric(args))

        def _counta(*args):
            # 数全部（含文本）
            return len(_flatten(args))

        def _countif(rng, criteria):
            pred = self._criteria_predicate(criteria)
            return sum(1 for v in _flatten([rng]) if pred(v))

        def _sumif(rng, criteria, sum_rng=None):
            pred = self._criteria_predicate(criteria)
            cond_values = _flatten([rng])
            sum_values = cond_values if sum_rng is None else _flatten([sum_rng])
            return sum(v for cond, v in zip(cond_values, sum_values)
                       if pred(cond) and _is_num(v))

        def _averageif(rng, criteria, avg_rng=None):
            pred = self._criteria_predicate(criteria)
            cond_values = _flatten([rng])
            avg_values = cond_values if avg_rng is None else _flatten([avg_rng])
            matched = [v for cond, v in zip(cond_values, avg_values)
                       if pred(cond) and _is_num(v)]
            if not matched:
                # 与 Excel 一致：无匹配时报 #DIV/0!
                raise ZeroDivisionError("AVERAGEIF: no matching values")
            return sum(matched) / len(matched)

        # ---------- 查找函数 ----------

        def _as_grid(a):
            """统一成行的列表（二维）；标量与一维列表按单行处理。"""
            if not isinstance(a, (list, tuple)):
                return [[a]]
            if not a:
                return [[]]
            if all(isinstance(r, (list, tuple)) for r in a):
                return [list(r) for r in a]
            return [list(a)]

        def _as_vector(a):
            """单行或单列区域转一维向量；二维区域报 #VALUE!。"""
            grid = _as_grid(a)
            if len(grid) == 1:
                return list(grid[0])
            if all(len(r) == 1 for r in grid):
                return [r[0] for r in grid]
            raise TypeError("lookup array must be one row or one column")

        def _norm(v):
            # 文本比较不区分大小写（与 Excel 一致）
            return v.lower() if isinstance(v, str) else v

        def _lookup_eq(target):
            """精确匹配谓词；文本目标含 * ? 时按通配符匹配（同 Excel）。"""
            if isinstance(target, str) and ('*' in target or '?' in target):
                pattern = target.lower()
                return lambda v: _excel_wildcard_match(str(v).lower(), pattern)
            t = _norm(target)
            return lambda v: _norm(v) == t

        def _approx_pick(vec, value, next_smaller):
            """近似匹配：next_smaller 取 <= value 的最大项，否则 >= value 的最小项。

            返回 0 基位置，无候选返回 None。跳过不可比较的类型。
            """
            best_pos, best_val = None, None
            target = _norm(value)
            for i, v in enumerate(vec):
                nv = _norm(v)
                try:
                    if next_smaller:
                        ok = nv <= target
                        better = best_val is None or nv > best_val
                    else:
                        ok = nv >= target
                        better = best_val is None or nv < best_val
                except TypeError:
                    continue
                if ok and better:
                    best_pos, best_val = i, nv
            return best_pos

        def _vlookup(value, table, col_index, range_lookup=True):
            grid = _as_grid(table)
            ci = int(col_index) - 1
            if ci < 0 or any(ci >= len(row) for row in grid):
                raise FormulaRefError("VLOOKUP col_index out of range")
            first_col = [row[0] for row in grid if row]
            if range_lookup:
                pos = _approx_pick(first_col, value, next_smaller=True)
            else:
                eq = _lookup_eq(value)
                pos = next((i for i, v in enumerate(first_col) if eq(v)), None)
            if pos is None:
                raise FormulaNAError("VLOOKUP: no match")
            return grid[pos][ci]

        def _match(value, lookup_array, match_type=1):
            vec = _as_vector(lookup_array)
            match_type = int(match_type)
            if match_type == 0:
                eq = _lookup_eq(value)
                pos = next((i for i, v in enumerate(vec) if eq(v)), None)
            else:
                pos = _approx_pick(vec, value, next_smaller=(match_type > 0))
            if pos is None:
                raise FormulaNAError("MATCH: no match")
            return pos + 1

        def _index(array, row_num, col_num=None):
            grid = _as_grid(array)
            r = int(row_num)
            if col_num is None:
                # 向量式：单行按列取，单列按行取
                if len(grid) == 1:
                    vec = grid[0]
                elif all(len(row) == 1 for row in grid):
                    vec = [row[0] for row in grid]
                else:
                    raise TypeError("INDEX on 2D array needs col_num")
                if not 1 <= r <= len(vec):
                    raise FormulaRefError("INDEX out of range")
                return vec[r - 1]
            c = int(col_num)
            if not (1 <= r <= len(grid) and 1 <= c <= len(grid[r - 1])):
                raise FormulaRefError("INDEX out of range")
            return grid[r - 1][c - 1]

        def _xlookup(value, lookup_array, return_array,
                     if_not_found=None, match_mode=0):
            vec = _as_vector(lookup_array)
            ret = _as_vector(return_array)
            match_mode = int(match_mode)
            if match_mode == 0:
                t = _norm(value)
                pos = next((i for i, v in enumerate(vec) if _norm(v) == t), None)
            elif match_mode == 2:
                eq = _lookup_eq(value)
                pos = next((i for i, v in enumerate(vec) if eq(v)), None)
            else:
                pos = _approx_pick(vec, value, next_smaller=(match_mode < 0))
            if pos is None:
                if if_not_found is not None:
                    return if_not_found
                raise FormulaNAError("XLOOKUP: no match")
            if pos >= len(ret):
                raise FormulaRefError("XLOOKUP: return array too short")
            return ret[pos]

        # ---------- 日期函数 ----------

        def _to_date(value):
            """把单元格值转成 datetime.date。

            支持 ISO/常见格式字符串、datetime/date、Excel 序列号（数值，
            1899-12-30 起算）。无法解释 -> #VALUE!。
            """
            if isinstance(value, datetime.datetime):  # 含 pd.Timestamp
                return value.date()
            if isinstance(value, datetime.date):
                return value
            if _is_num(value):
                try:
                    ts = pd.Timestamp('1899-12-30') + pd.Timedelta(days=float(value))
                    return ts.date()
                except (ValueError, OverflowError):
                    raise FormulaNumError("invalid date serial")
            if isinstance(value, str):
                try:
                    return pd.to_datetime(value).date()
                except (ValueError, TypeError):
                    raise TypeError("cannot parse date: {}".format(value))
            raise TypeError("cannot parse date")

        def _today():
            return datetime.date.today().isoformat()

        def _now():
            return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        def _date(year, month, day):
            # 与 Excel 一致：月/日越界自动进位（13 月 -> 次年 1 月，2 月 30 日 -> 3 月）
            y, m, d = int(year), int(month), int(day)
            y += (m - 1) // 12
            m = (m - 1) % 12 + 1
            try:
                base = datetime.date(y, m, 1)
            except ValueError:
                raise FormulaNumError("invalid date")
            return (base + datetime.timedelta(days=d - 1)).isoformat()

        def _year(value):
            return _to_date(value).year

        def _month(value):
            return _to_date(value).month

        def _day(value):
            return _to_date(value).day

        def _weekday(value, return_type=1):
            wd = _to_date(value).weekday()  # 周一=0
            return_type = int(return_type)
            if return_type == 2:
                return wd + 1        # 周一=1 .. 周日=7
            if return_type == 3:
                return wd            # 周一=0 .. 周日=6
            return (wd + 1) % 7 + 1  # 默认：周日=1 .. 周六=7

        def _days(end, start):
            return (_to_date(end) - _to_date(start)).days

        def _datedif(start, end, unit):
            s, e = _to_date(start), _to_date(end)
            if e < s:
                raise FormulaNumError("DATEDIF: end before start")
            u = str(unit).upper()
            if u == 'D':
                return (e - s).days
            if u == 'M':
                months = (e.year - s.year) * 12 + (e.month - s.month)
                if e.day < s.day:
                    months -= 1
                return months
            if u == 'Y':
                years = e.year - s.year
                if (e.month, e.day) < (s.month, s.day):
                    years -= 1
                return years
            raise TypeError("DATEDIF: unsupported unit {}".format(unit))

        def _and(*args):
            return all(bool(v) for v in _flatten(args))

        def _or(*args):
            return any(bool(v) for v in _flatten(args))

        def _not(value):
            return not value

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

        def _sqrt(value):
            if value < 0:
                # 与 Excel 的 #NUM! 一致，不返回复数
                raise FormulaNumError("SQRT of negative number")
            return value ** 0.5

        allowed_names = {
            'sum': sum, 'max': max, 'min': min, 'len': len,
            'abs': abs, 'round': round, 'int': int, 'float': float,
            'str': str, 'pow': pow,
            'True': True, 'False': False,
            '_avg': _avg, '_concat': _concat, '_count': _count,
            '_counta': _counta, '_countif': _countif,
            '_sumif': _sumif, '_averageif': _averageif,
            '_and': _and, '_or': _or, '_not': _not,
            '_vlookup': _vlookup, '_xlookup': _xlookup,
            '_index': _index, '_match': _match,
            '_today': _today, '_now': _now, '_date': _date,
            '_year': _year, '_month': _month, '_day': _day,
            '_weekday': _weekday, '_days': _days, '_datedif': _datedif,
            '_sum': _sum, '_max': _max, '_min': _min, '_if': _if,
            '_left': _left, '_right': _right, '_mid': _mid,
            '_len': lambda text: len(str(text)),
            '_upper': lambda text: str(text).upper(),
            '_lower': lambda text: str(text).lower(),
            '_trim': lambda text: str(text).strip(),
            '_round': _round,
            '_sqrt': _sqrt,
            '_mod': lambda num, divisor: num % divisor,
        }

        # 经 AST 把 _if 调用降为条件表达式，保证分支惰性求值
        tree = _IfCallLowering().visit(ast.parse(expr, mode='eval'))
        ast.fix_missing_locations(tree)
        code = compile(tree, '<formula>', 'eval')

        for name in code.co_names:
            if name not in allowed_names:
                raise FormulaNameError(tr("不支持的函数: {}").format(name))

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
