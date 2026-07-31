"""文件读取测试：带元数据前言的 CSV（世界银行式导出）——原样载入，不静默删行"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from qtui import file_io
from qtui.main_window import MainWindow

WORLD_BANK_CSV = (
    '"数据源","世界发展指标",\n'
    '\n'
    '"最后更新时间","2026-07-13",\n'
    '\n'
    '"Country Name","Country Code","Indicator Name","Indicator Code","1960","1961"\n'
    '"阿鲁巴","ABW","GDP","NY.GDP.MKTP.CD","",""\n'
    '"阿富汗","AFG","GDP","NY.GDP.MKTP.CD","1000","2000"\n'
    '"安哥拉","AGO","GDP","NY.GDP.MKTP.CD","3000","4000"\n'
)


class TestPreambleCsvFullLoad:
    def test_world_bank_csv_loads_everything(self, tmp_path):
        # 原样载入：元数据行、空行一行不丢，列名用位置字母，由用户决定表头
        path = str(tmp_path / 'wb.csv')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(WORLD_BANK_CSV)
        df = file_io.read_csv_any_encoding(path)
        assert list(df.columns) == ['A', 'B', 'C', 'D', 'E', 'F']
        assert len(df) == 8                       # 2 元数据 + 2 空行 + 表头 + 3 数据
        assert df.iloc[0, 0] == '数据源'           # 前言仍在
        assert df.iloc[4, 0] == 'Country Name'    # 真表头作为数据行保留
        assert df.iloc[7, 0] == '安哥拉'

    def test_gbk_preamble_csv_loads(self, tmp_path):
        path = str(tmp_path / 'g.csv')
        with open(path, 'w', encoding='gbk') as f:
            f.write(WORLD_BANK_CSV)
        df = file_io.read_csv_any_encoding(path)
        assert len(df) == 8
        assert df.iloc[4, 0] == 'Country Name'

    def test_normal_csv_unaffected(self, tmp_path):
        path = str(tmp_path / 'n.csv')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('A,B\n1,2\n3,4\n')
        df = file_io.read_csv_any_encoding(path)
        assert list(df.columns) == ['A', 'B']
        assert len(df) == 2


class TestHeaderCandidateDetection:
    def _full_loaded_df(self):
        return pd.DataFrame({
            'A': ['数据源', None, '最后更新时间', None, 'Country Name', '阿鲁巴', '阿富汗'],
            'B': ['世界发展指标', None, '2026-07-13', None, 'Country Code', 'ABW', 'AFG'],
            'C': [None, None, None, None, 'Indicator', 'GDP', 'GDP'],
            'D': [None, None, None, None, '1960', None, '1000'],
        })

    def test_detects_header_after_narrow_preamble(self):
        assert MainWindow._detect_header_candidate(self._full_loaded_df()) == 4

    def test_named_columns_not_flagged(self):
        df = pd.DataFrame({'姓名': ['a', 'b', 'c'], '金额': [1, 2, 3]})
        assert MainWindow._detect_header_candidate(df) == -1

    def test_letter_columns_without_preamble_not_flagged(self):
        df = pd.DataFrame({'A': ['x', 'y', 'z'], 'B': [1, 2, 3]})
        assert MainWindow._detect_header_candidate(df) == -1


class TestCellColorPersistence:
    """背景色写入 xlsx 真实填充并可读回（重启不丢、Excel 可见）"""

    def test_colors_round_trip(self, tmp_path):
        df = pd.DataFrame({'X': [1.0, 2.0], 'Y': [3.0, 4.0]})
        path = str(tmp_path / 'c.xlsx')
        colors = {(-1, 0): '#ff0000',   # 表头行
                  (0, 1): '#e3f2fd',
                  (1, 0): '#00ff00'}
        file_io.save_workbook(path, {'S': df}, ['S'],
                              cell_colors={'S': colors})
        assert file_io.read_sheet_colors(path, 'S') == colors

    def test_plain_file_fast_path_returns_empty(self, tmp_path):
        path = str(tmp_path / 'p.xlsx')
        file_io.save_workbook(path, {'S': pd.DataFrame({'X': [1.0]})}, ['S'])
        assert not file_io._xlsx_has_custom_fills(path)
        assert file_io.read_sheet_colors(path, 'S') == {}


class TestDetectPreambleEnd:
    def test_world_bank_style_rows(self):
        rows = [['数据源', '世界发展指标'],
                ['最后更新时间', '2026-07-13'],
                ['Country Name', 'Country Code', 'Indicator', '1960'],
                ['阿鲁巴', 'ABW', 'GDP', '100'],
                ['阿富汗', 'AFG', 'GDP', '200']]
        assert MainWindow._detect_preamble_end(rows) == 2

    def test_uniform_rows_no_preamble(self):
        rows = [['A', 'B'], ['1', '2'], ['3', '4']]
        assert MainWindow._detect_preamble_end(rows) == 0

    def test_too_few_rows(self):
        assert MainWindow._detect_preamble_end([['a'], ['b', 'c']]) == 0
