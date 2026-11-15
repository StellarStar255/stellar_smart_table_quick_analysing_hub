"""Formula catalogue and non-evaluating, cursor-aware formula analysis.

The editor never reads cells or recalculates a workbook here. Source spans always
refer to the original input, including Excel escaped strings and Unicode text.
"""
import ast
import difflib
import re
from dataclasses import dataclass, field

from .formula_engine import FormulaEngine
from qtui.i18n import current_language


def local(zh, en):
    return en if current_language() == 'en' else zh


# Parameter keys are shared by signatures and their explanations.
PARAMS = {
    'number': ('数值', 'number', '数字、单元格或区域。', 'A number, cell or range.'),
    'range': ('区域', 'range', '可直接在表格中点选或拖选区域。', 'Click or drag cells in the table.'),
    'criteria': ('条件', 'criteria', '例如 ">=60"、"苹果"；文本条件需加双引号。', 'For example ">=60" or "Apple"; quote text criteria.'),
    'sum_range': ('求和区域', 'sum_range', '与条件区域对应的数值区域；省略时使用条件区域。', 'Values corresponding to the criteria range; defaults to that range.'),
    'average_range': ('平均区域', 'average_range', '与条件区域对应的数值区域；省略时使用条件区域。', 'Values corresponding to the criteria range; defaults to that range.'),
    'condition': ('条件', 'condition', '例如 B2>=60，结果应为 TRUE 或 FALSE。', 'For example B2>=60, yielding TRUE or FALSE.'),
    'true_value': ('成立时的值', 'value_if_true', '条件满足时返回的数字、文本或公式。', 'Number, text or formula returned when true.'),
    'false_value': ('不成立时的值', 'value_if_false', '条件不满足时返回的数字、文本或公式。本应用需要填写。', 'Value returned when false; required in this app.'),
    'value': ('值', 'value', '要判断或计算的值、引用或公式。', 'A value, reference or expression.'),
    'fallback': ('出错时的值', 'value_if_error', '原公式出错时返回的值，例如 0 或 "未找到"。', 'Value returned on error, such as 0 or "Not found".'),
    'text': ('文本', 'text', '文本单元格，或使用双引号包裹的文本。', 'A text cell or text enclosed in double quotes.'),
    'length': ('字符数', 'num_chars', '要提取的字符数；LEFT/RIGHT 默认 1。', 'Number of characters; LEFT/RIGHT default to 1.'),
    'start': ('开始位置', 'start_num', '从第几个字符开始，第一个字符为 1。', 'First character position, starting at 1.'),
    'digits': ('小数位数', 'num_digits', '默认 0；负数表示向十位、百位等取整。', 'Defaults to 0; negative values round tens, hundreds, etc.'),
    'base': ('底数', 'number', '要进行乘方运算的数字。', 'Number to raise to a power.'),
    'power': ('指数', 'power', '例如 POWER(2,3) 返回 8。', 'For example POWER(2,3) returns 8.'),
    'divisor': ('除数', 'divisor', '除数不能为 0。', 'The divisor must not be zero.'),
    'lookup': ('查找值', 'lookup_value', '要查找的值或单元格。', 'The value or cell to look up.'),
    'table': ('查找表', 'table_array', '在区域第一列查找，从指定列返回值。', 'Search the first column and return a value from the specified column.'),
    'column': ('列序号', 'col_index', '从所选区域的第一列算起，第一列为 1。', 'Column within the selected range, starting at 1.'),
    'approx': ('近似匹配', 'range_lookup', 'FALSE 为精确匹配；默认 TRUE 为近似匹配，查找列需升序排列。', 'FALSE: exact; default TRUE: approximate, requiring an ascending lookup column.'),
    'lookup_range': ('查找区域', 'lookup_array', '单行或单列的查找区域。', 'A single row or column to search.'),
    'return_range': ('返回区域', 'return_array', '与查找区域对应的单行或单列。', 'A single row or column corresponding to the lookup range.'),
    'not_found': ('未找到时的值', 'if_not_found', '默认返回 #N/A；可填写 "未找到"。', 'Defaults to #N/A; optionally enter "Not found".'),
    'match_mode': ('匹配模式', 'match_mode', '0 精确（默认）；-1 精确或较小值；1 精确或较大值；2 通配符。', '0 exact (default); -1 exact or next smaller; 1 exact or next larger; 2 wildcards.'),
    'match_type': ('匹配类型', 'match_type', '0 精确；1 较小值（默认，升序）；-1 较大值（降序）。', '0 exact; 1 next smaller (default, ascending); -1 next larger (descending).'),
    'array': ('区域', 'array', '要从中返回值的区域。', 'The range to return a value from.'),
    'row': ('行序号', 'row_num', '区域内的行号，从 1 开始；单行向量中表示位置。', 'Row within the range, starting at 1; position for a row vector.'),
    'col_num': ('列序号', 'col_num', '区域内的列号，从 1 开始；二维区域必须填写。', 'Column starting at 1; required for a two-dimensional range.'),
    'year': ('年', 'year', '例如 2026。', 'For example 2026.'),
    'month': ('月', 'month', '例如 1 到 12。', 'For example 1 through 12.'),
    'day': ('日', 'day', '例如 1 到 31。', 'For example 1 through 31.'),
    'date': ('日期', 'date', '日期单元格、Excel 日期序号或 "2026-01-15"。', 'A date cell, Excel serial date or "2026-01-15".'),
    'end_date': ('结束日期', 'end_date', '结束日期单元格或日期文本。', 'End date cell or date text.'),
    'start_date': ('开始日期', 'start_date', '开始日期单元格或日期文本。', 'Start date cell or date text.'),
    'unit': ('单位', 'unit', '本应用支持 "Y" 整年、"M" 整月、"D" 天数。', 'This app supports "Y" whole years, "M" whole months, "D" days.'),
    'return_type': ('星期类型', 'return_type', '1 周日=1（默认）；2 周一=1；3 周一=0。', '1 Sunday=1 (default); 2 Monday=1; 3 Monday=0.'),
}
CATEGORIES = {
    'stats': ('求和统计', 'Statistics'), 'logic': ('逻辑判断', 'Logical'),
    'text': ('文本处理', 'Text'), 'math': ('数学', 'Math'),
    'lookup': ('查找引用', 'Lookup'), 'date': ('日期', 'Date'),
}


@dataclass(frozen=True)
class FunctionSpec:
    name: str
    category: str
    title: tuple
    params: tuple
    minimum: int
    maximum: int | None
    example: str
    repeat: int = 0
    note: tuple = ('', '')

    @property
    def description(self):
        return local(*self.title)

    def parameter_key(self, index):
        if not self.params:
            return None
        if index >= len(self.params) and self.repeat:
            index = len(self.params) - self.repeat + (index - len(self.params)) % self.repeat
        return self.params[min(index, len(self.params) - 1)]

    def signature(self, active=None):
        parts = []
        for i, key in enumerate(self.params):
            label = local(*PARAMS[key][:2])
            if i >= self.minimum:
                label = '[' + label + ']'
            parts.append(label)
        if self.repeat:
            parts.append('…')
        return self.name + '(' + ', '.join(parts) + ')'


def _spec(name, cat, zh, en, params, minimum, maximum, example, repeat=0, note=('', '')):
    return FunctionSpec(name, cat, (zh, en), tuple(params.split()) if params else (), minimum, maximum, example, repeat, note)


_SPECS = [
    _spec('SUM', 'stats', '求和', 'Sum numbers', 'number', 0, None, '=SUM(B2:B10)', 1),
    _spec('AVERAGE', 'stats', '计算平均值', 'Average numbers', 'number', 0, None, '=AVERAGE(B2:B10)', 1),
    _spec('COUNT', 'stats', '统计数值个数', 'Count numbers', 'value', 0, None, '=COUNT(B2:B10)', 1),
    _spec('COUNTA', 'stats', '统计非空值个数', 'Count nonblank values', 'value', 0, None, '=COUNTA(A2:A10)', 1),
    _spec('MAX', 'stats', '取最大值', 'Largest number', 'number', 0, None, '=MAX(B2:B10)', 1),
    _spec('MIN', 'stats', '取最小值', 'Smallest number', 'number', 0, None, '=MIN(B2:B10)', 1),
    _spec('COUNTIF', 'stats', '按一个条件计数', 'Count by one condition', 'range criteria', 2, 2, '=COUNTIF(B2:B10,">=60")'),
    _spec('SUMIF', 'stats', '按一个条件求和', 'Sum by one condition', 'range criteria sum_range', 2, 3, '=SUMIF(A2:A10,"苹果",B2:B10)'),
    _spec('AVERAGEIF', 'stats', '按一个条件求平均', 'Average by one condition', 'range criteria average_range', 2, 3, '=AVERAGEIF(B2:B10,">=60")'),
    _spec('COUNTIFS', 'stats', '按多个条件计数', 'Count by multiple conditions', 'range criteria', 2, None, '=COUNTIFS(A2:A10,"苹果",B2:B10,">0")', 2),
    _spec('SUMIFS', 'stats', '按多个条件求和', 'Sum by multiple conditions', 'sum_range range criteria', 3, None, '=SUMIFS(C2:C10,A2:A10,"苹果",B2:B10,">0")', 2),
    _spec('AVERAGEIFS', 'stats', '按多个条件求平均', 'Average by multiple conditions', 'average_range range criteria', 3, None, '=AVERAGEIFS(C2:C10,A2:A10,"苹果")', 2),
    _spec('IF', 'logic', '根据条件返回不同结果', 'Return a value based on a condition', 'condition true_value false_value', 3, 3, '=IF(B2>=60,"及格","不及格")', note=('本应用要求填写三个参数。', 'All three arguments are required in this app.')),
    _spec('IFERROR', 'logic', '公式出错时返回备用值', 'Return a fallback on error', 'value fallback', 2, 2, '=IFERROR(B2/C2,0)'),
    _spec('AND', 'logic', '所有条件均成立', 'All conditions are true', 'condition', 0, None, '=AND(B2>0,C2>0)', 1),
    _spec('OR', 'logic', '任一条件成立', 'Any condition is true', 'condition', 0, None, '=OR(B2>0,C2>0)', 1),
    _spec('NOT', 'logic', '条件取反', 'Reverse a condition', 'condition', 1, 1, '=NOT(B2>0)'),
    _spec('CONCAT', 'text', '连接文本', 'Join text', 'text', 0, None, '=CONCAT(A2,"-",B2)', 1),
    _spec('CONCATENATE', 'text', '连接文本（兼容名称）', 'Join text (compatibility name)', 'text', 0, None, '=CONCATENATE(A2,"-",B2)', 1),
    _spec('LEFT', 'text', '提取左侧字符', 'Extract leftmost characters', 'text length', 1, 2, '=LEFT(A2,2)'),
    _spec('RIGHT', 'text', '提取右侧字符', 'Extract rightmost characters', 'text length', 1, 2, '=RIGHT(A2,4)'),
    _spec('MID', 'text', '从指定位置提取字符', 'Extract characters from a position', 'text start length', 3, 3, '=MID(A2,2,3)'),
    _spec('LEN', 'text', '计算文本长度', 'Text length', 'text', 1, 1, '=LEN(A2)'),
    _spec('UPPER', 'text', '转换为大写', 'Uppercase text', 'text', 1, 1, '=UPPER(A2)'),
    _spec('LOWER', 'text', '转换为小写', 'Lowercase text', 'text', 1, 1, '=LOWER(A2)'),
    _spec('TRIM', 'text', '去除首尾空白', 'Remove leading and trailing whitespace', 'text', 1, 1, '=TRIM(A2)', note=('目前只去除首尾空白，保留文本内部空格。', 'Currently trims edges only; internal spaces are preserved.')),
    _spec('ABS', 'math', '绝对值', 'Absolute value', 'number', 1, 1, '=ABS(B2)'),
    _spec('INT', 'math', '向下取整', 'Round down to an integer', 'number', 1, 1, '=INT(B2)'),
    *[_spec(n, 'math', zh, en, 'number digits', 1, 2, f'={n}(B2,2)') for n, zh, en in (
        ('ROUND', '四舍五入', 'Round a number'), ('ROUNDUP', '远离零取整', 'Round away from zero'), ('ROUNDDOWN', '向零取整', 'Round toward zero'))],
    _spec('POWER', 'math', '乘方', 'Raise to a power', 'base power', 2, 2, '=POWER(B2,2)'),
    _spec('SQRT', 'math', '平方根', 'Square root', 'number', 1, 1, '=SQRT(B2)'),
    _spec('MOD', 'math', '取余数', 'Remainder', 'number divisor', 2, 2, '=MOD(B2,3)'),
    _spec('VLOOKUP', 'lookup', '按首列查找并返回指定列', 'Search first column and return a value', 'lookup table column approx', 3, 4, '=VLOOKUP(A2,D2:F10,3,FALSE)'),
    _spec('XLOOKUP', 'lookup', '在区域中查找对应值', 'Look up a corresponding value', 'lookup lookup_range return_range not_found match_mode', 3, 5, '=XLOOKUP(A2,D2:D10,E2:E10,"未找到",0)', note=('本应用支持前五个参数，暂不支持第六个 search_mode 参数。', 'Supports the first five arguments; search_mode is not supported.')),
    _spec('INDEX', 'lookup', '返回区域内指定位置的值', 'Return a value at a position', 'array row col_num', 2, 3, '=INDEX(A2:C10,2,3)'),
    _spec('MATCH', 'lookup', '查找值在区域中的位置', 'Find the position of a value', 'lookup lookup_range match_type', 2, 3, '=MATCH(A2,D2:D10,0)'),
    _spec('TODAY', 'date', '今天的日期', "Today's date", '', 0, 0, '=TODAY()'),
    _spec('NOW', 'date', '当前日期与时间', 'Current date and time', '', 0, 0, '=NOW()'),
    _spec('DATE', 'date', '由年月日生成日期', 'Create a date', 'year month day', 3, 3, '=DATE(2026,1,15)'),
    *[_spec(n, 'date', zh, en, 'date', 1, 1, f'={n}(A2)') for n, zh, en in (
        ('YEAR', '提取年份', 'Extract year'), ('MONTH', '提取月份', 'Extract month'), ('DAY', '提取日', 'Extract day'))],
    _spec('WEEKDAY', 'date', '计算星期', 'Day of the week', 'date return_type', 1, 2, '=WEEKDAY(A2,2)'),
    _spec('DAYS', 'date', '两个日期相差的天数', 'Days between two dates', 'end_date start_date', 2, 2, '=DAYS(B2,A2)'),
    _spec('DATEDIF', 'date', '两个日期相差的整年、月或天', 'Whole years, months or days between dates', 'start_date end_date unit', 3, 3, '=DATEDIF(A2,B2,"M")'),
]
FUNCTIONS = {s.name: s for s in _SPECS}
COMMON = ('SUM', 'AVERAGE', 'IF', 'COUNTIF', 'SUMIF', 'XLOOKUP')


def search_functions(query='', category=None):
    q = query.strip().casefold()
    specs = [s for s in FUNCTIONS.values() if (category is None or s.category == category)
             and (not q or q in (s.name + ' ' + ' '.join(s.title)).casefold())]
    return sorted(specs, key=lambda s: (not s.name.casefold().startswith(q), s.name))


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    start: int
    end: int


_TOKEN = re.compile(
    r'(?P<string>"(?:""|[^"])*"|\'[^\']*\')'
    r'|(?P<ref>\$?[A-Za-z]+\$?\d+(?::\$?[A-Za-z]+\$?\d+)?)\b'
    r'|(?P<number>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)'
    r'|(?P<name>[A-Za-z_][A-Za-z_0-9.]*)'
    r'|(?P<error>\#(?:DIV/0!|VALUE!|NAME\?|NUM!|N/A|REF!|CIRC!|ERROR))'
    r'|(?P<space>\s+)|(?P<symbol>.)', re.DOTALL)


def tokens(text):
    return [Token(m.lastgroup, m.group(), m.start(), m.end()) for m in _TOKEN.finditer(text)]


def completion_span(text, cursor):
    if not text.startswith('='):
        return None
    for t in tokens(text):
        if t.kind == 'string' and t.start < cursor < t.end:
            return None
    # Incomplete quoted strings also suppress suggestions.
    if _open_quote(text[:cursor]):
        return None
    start = cursor
    while start > 1 and (text[start - 1].isalpha() or text[start - 1] in '_.'):
        start -= 1
    end = cursor
    while end < len(text) and (text[end].isalnum() or text[end] in '_.'):
        end += 1
    prefix = text[start:cursor]
    if not prefix:
        return (cursor, cursor, '') if text.strip() == '=' else None
    if start > 1 and (text[start - 1].isalnum() or text[start - 1] in '$:'):
        return None
    if re.fullmatch(r'[A-Za-z]+\$?\d+', text[start:end]):
        return None
    return start, end, prefix


def _open_quote(text):
    quote = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                if quote == '"' and i + 1 < len(text) and text[i + 1] == '"':
                    i += 2
                    continue
                quote = None
        elif ch in ('"', "'"):
            quote = ch
        i += 1
    return quote


@dataclass
class Call:
    name: str | None
    start: int
    open: int
    commas: list = field(default_factory=list)
    close: int | None = None

    @property
    def end(self):
        return self.close + 1 if self.close is not None else self.open + 1


def calls(text):
    stack, result = [], []
    previous = None
    for t in tokens(text):
        if t.kind in ('space', 'string'):
            if t.kind == 'string':
                previous = t
            continue
        if t.text == '(':
            name = previous.text.upper() if previous and previous.kind in ('name', 'ref') and ':' not in previous.text else None
            c = Call(name, previous.start if name else t.start, t.start)
            stack.append(c)
            result.append(c)
        elif t.text == ',' and stack:
            stack[-1].commas.append(t.start)
        elif t.text == ')' and stack:
            stack.pop().close = t.start
        previous = t
    return result


def active_call(text, cursor):
    active = [c for c in calls(text) if c.name and c.open < cursor
              and (c.close is None or cursor <= c.close)]
    if not active:
        return None
    c = active[-1]
    return c, sum(p < cursor for p in c.commas)


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    start: int
    end: int
    replacement: str | None = None
    incomplete: bool = False


def diagnose(text, final=False):
    """Return one actionable diagnostic without evaluating any cell values."""
    if not text.startswith('='):
        return None
    ts = tokens(text)
    quote = _open_quote(text)
    if quote:
        start = next((t.start for t in ts if t.text == quote), len(text))
        return Diagnostic('quote', local('文本引号尚未闭合。', 'The text quotation is not closed.'), start, len(text), incomplete=True)
    for t in ts[1:]:
        if t.kind != 'string' and t.text in '，（）；':
            return Diagnostic('punctuation', local('公式中使用了中文标点，请替换为英文标点。', 'Replace this full-width punctuation with formula punctuation.'), t.start, t.end,
                              {'，': ',', '（': '(', '）': ')', '；': ','}[t.text])
    stack = []
    for t in ts:
        if t.kind == 'symbol' and t.text == '(':
            stack.append(t)
        elif t.kind == 'symbol' and t.text == ')':
            if not stack:
                return Diagnostic('parenthesis', local('这里多了一个右括号。', 'Unexpected closing parenthesis.'), t.start, t.end, '')
            stack.pop()
    for c in calls(text):
        if not c.name:
            continue
        spec = FUNCTIONS.get(c.name)
        if spec is None:
            near = difflib.get_close_matches(c.name, FUNCTIONS, n=1, cutoff=.6)
            suggestion = near[0] if near else None
            msg = local('本应用不支持函数 {}。', 'This app does not support {}.').format(c.name)
            if suggestion:
                msg += local(' 是否改为 {}？', ' Did you mean {}?').format(suggestion)
            return Diagnostic('function', msg, c.start, c.start + len(c.name), suggestion)
        if c.close is None:
            continue
        boundaries = [c.open, *c.commas, c.close]
        parts = [text[a + 1:b].strip() for a, b in zip(boundaries, boundaries[1:])]
        count = 0 if len(parts) == 1 and not parts[0] else len(parts)
        valid_pairs = not (spec.repeat == 2 and (count - spec.minimum) % 2)
        if count and not all(parts):
            missing = next(i for i, part in enumerate(parts) if not part)
            return Diagnostic('arguments', local('{} 的第 {} 个参数为空，请填写参数或删除多余的逗号。', '{} argument {} is empty; fill it in or remove the extra comma.').format(c.name, missing + 1), boundaries[missing] + 1, boundaries[missing + 1])
        if count < spec.minimum or (spec.maximum is not None and count > spec.maximum) or not valid_pairs:
            expected = (str(spec.minimum) if spec.minimum == spec.maximum else
                        f'{spec.minimum}–{spec.maximum}' if spec.maximum is not None else
                        local('至少 {}', 'at least {}').format(spec.minimum))
            msg = local('{} 需要 {} 个参数；当前为 {} 个。', '{} expects {} arguments; currently {}.').format(c.name, expected, count)
            if count < spec.minimum:
                key = spec.parameter_key(count)
                msg += local(' 请填写「{}」。', ' Fill in {}.').format(local(*PARAMS[key][:2]))
            if not valid_pairs:
                msg += local(' 条件区域与条件必须成对填写。', ' Criteria ranges and criteria must be paired.')
            return Diagnostic('arguments', msg, c.start, c.end)
    if stack:
        return Diagnostic('parenthesis', local('公式还缺少 {} 个右括号。', '{} closing parentheses still needed.').format(len(stack)), len(text), len(text), ')' * len(stack), True)
    if not final:
        return None
    # Mirror the engine's operator/string transformations, with inert references.
    # Source spans above stay precise; a generic syntax failure selects the tail.
    expr = text[1:]
    literals = []
    expr = FormulaEngine.STRING_PATTERN.sub(
        lambda m: _stash_literal(repr(FormulaEngine._decode_string_literal(m.group())), literals), expr)
    expr = FormulaEngine.RANGE_REF_PATTERN.sub('1', expr)
    expr = FormulaEngine.CELL_REF_PATTERN.sub('1', expr)
    expr = expr.replace('#REF!', '_referr()')
    expr = FormulaEngine._normalize_operators(expr)
    expr = re.sub(r'\x00(\d+)\x00', lambda m: literals[int(m.group(1))], expr)
    try:
        tree = ast.parse(expr.strip(), mode='eval')
    except (SyntaxError, ValueError, RecursionError):
        return Diagnostic('syntax', local('公式尚未完成或语法有误，请检查运算符、参数分隔符和引号。', 'Incomplete or invalid formula. Check operators, argument separators and quotes.'), max(1, len(text) - 1), len(text), incomplete=True)
    # A name without parentheses (e.g. =SUM) cannot be evaluated by the engine.
    function_nodes = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id.upper() in FUNCTIONS and id(node) not in function_nodes:
            t = next((t for t in ts if t.kind == 'name' and t.text.upper() == node.id.upper()), None)
            return Diagnostic('function', local('函数 {} 后需要括号和参数。', 'Function {} needs parentheses and arguments.').format(node.id.upper()), t.start if t else 1, t.end if t else len(text), node.id.upper() + '(')
        if isinstance(node, ast.Name) and node.id.upper() not in {*FUNCTIONS, 'TRUE', 'FALSE', '_REFERR', '_PCT_'}:
            match = next((t for t in ts if t.kind == 'name' and t.text == node.id), None)
            return Diagnostic('name', local('无法识别名称 {}；文本请加双引号。', 'Unknown name {}; enclose text in double quotes.').format(node.id), match.start if match else 1, match.end if match else len(text))
    return None


def _stash_literal(value, literals):
    literals.append(value)
    return '\x00{}\x00'.format(len(literals) - 1)


ERROR_HELP = {
    '#DIV/0!': ('除数为 0、空白，或没有可平均的数值。请检查除数及参与平均的区域。', 'The divisor is zero/blank, or there are no numbers to average. Check the divisor and range.'),
    '#VALUE!': ('参数类型或区域大小不合适。请检查数字、文本及对应区域的长度。', 'An argument type or range size is unsuitable. Check numbers, text and matching range lengths.'),
    '#NAME?': ('存在无法识别或尚不支持的函数、名称。文本需加双引号。', 'A function or name is unknown or unsupported. Enclose text in double quotes.'),
    '#REF!': ('引用已失效或查找位置超出区域。请检查引用和行列序号。', 'A reference is invalid or a lookup position is outside its range. Check references and row/column numbers.'),
    '#N/A': ('未找到匹配项。请检查查找值、区域和匹配方式。', 'No match was found. Check the lookup value, range and match mode.'),
    '#NUM!': ('数值超出有效范围，例如负数开平方或结束日期早于开始日期。', 'A number is outside the valid domain, such as a negative square root or reversed dates.'),
    '#CIRC!': ('公式直接或间接引用了自身。请编辑公式，检查引用关系。', 'The formula refers to itself directly or indirectly. Edit it and check references.'),
    '#ERROR': ('公式无法计算。请编辑公式并检查语法、参数及应用支持的函数。', 'The formula could not be calculated. Check syntax, arguments and supported functions.'),
}


def error_help(code):
    return local(*ERROR_HELP[code]) if code in ERROR_HELP else ''


def reference_tokens(text):
    return [t for t in tokens(text) if t.kind == 'ref']
