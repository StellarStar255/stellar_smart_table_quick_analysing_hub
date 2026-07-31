"""文件读取测试：带元数据前言的 CSV（世界银行式导出）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


class TestPreambleCsv:
    def test_world_bank_csv_loads(self, tmp_path):
        path = str(tmp_path / 'wb.csv')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(WORLD_BANK_CSV)
        df = file_io.read_csv_any_encoding(path)
        assert 'Country Name' in df.columns
        assert '1960' in df.columns
        assert len(df) == 3
        assert df.iloc[0]['Country Code'] == 'ABW'

    def test_detect_header_row(self, tmp_path):
        path = str(tmp_path / 'wb.csv')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(WORLD_BANK_CSV)
        assert file_io.detect_csv_header_row(path, 'utf-8', ',') == 4

    def test_normal_csv_unaffected(self, tmp_path):
        path = str(tmp_path / 'n.csv')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('A,B\n1,2\n3,4\n')
        df = file_io.read_csv_any_encoding(path)
        assert list(df.columns) == ['A', 'B']
        assert len(df) == 2

    def test_gbk_preamble_csv_loads(self, tmp_path):
        path = str(tmp_path / 'g.csv')
        with open(path, 'w', encoding='gbk') as f:
            f.write(WORLD_BANK_CSV)
        df = file_io.read_csv_any_encoding(path)
        assert 'Country Name' in df.columns
        assert len(df) == 3


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
