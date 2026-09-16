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


class TestLeadingZerosPreserved:
    """文本格式的 "007"（工号/邮编）读入与保存都不能被静默改成数字 7"""

    def test_xlsx_text_cell_keeps_leading_zeros_and_round_trips(self, tmp_path):
        import openpyxl
        path = str(tmp_path / 'lz.xlsx')
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'S'
        ws.append(['ID', 'N'])
        for r, (sid, n) in enumerate([('007', 3), ('0123', 4.5)], start=2):
            c = ws.cell(row=r, column=1, value=sid)
            c.number_format = '@'
            ws.cell(row=r, column=2, value=n)
        wb.save(path)

        for engine in ('calamine', 'openpyxl'):
            xf = pd.ExcelFile(path, engine=engine)
            df = file_io.read_sheet(xf, 'S')
            assert df['ID'].tolist() == ['007', '0123'], engine
            assert df['N'].dtype.kind == 'f', engine   # 数字列仍是数字

        file_io.save_workbook(path, {'S': df}, ['S'])
        ws = openpyxl.load_workbook(path)['S']
        assert ws['A2'].value == '007'
        assert ws['B2'].value == 3

    def test_csv_leading_zero_column_stays_text(self, tmp_path):
        path = str(tmp_path / 'lz.csv')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('zip,n,neg,f,mixed\n00123,1,-007,0.5,7\n00456,2,-8,1.5,08\n')
        df = file_io.read_csv_any_encoding(path)
        assert df['zip'].tolist() == ['00123', '00456']
        assert df['neg'].tolist() == ['-007', '-8']
        assert df['mixed'].tolist() == ['7', '08']
        assert df['n'].dtype.kind == 'i'      # 普通整数列不受影响
        assert df['f'].dtype.kind == 'f'      # "0.5" 不是前导零

    def test_csv_without_leading_zeros_infers_numbers(self, tmp_path):
        path = str(tmp_path / 'n.csv')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('a,b\n10,0.5\n20,0\n')
        df = file_io.read_csv_any_encoding(path)
        assert df['a'].dtype.kind == 'i' and df['b'].dtype.kind == 'f'


class TestCsvEncoding:
    def test_utf16_bom_detected(self, tmp_path):
        path = str(tmp_path / 'u16.csv')
        with open(path, 'w', encoding='utf-16') as f:
            f.write('姓名,金额\n张三,10\n')
        df = file_io.read_csv_any_encoding(path)
        assert list(df.columns) == ['姓名', '金额']
        assert df.attrs['source_encoding'] == 'utf-16'

    def test_utf8_bom_detected_and_written_back(self, tmp_path):
        path = str(tmp_path / 'bom.csv')
        with open(path, 'w', encoding='utf-8-sig') as f:
            f.write('姓名,金额\n张三,10\n')
        df = file_io.read_csv_any_encoding(path)
        assert df.attrs['source_encoding'] == 'utf-8-sig'
        file_io.save_csv(path, df)
        with open(path, 'rb') as f:
            assert f.read(3) == b'\xef\xbb\xbf'
        assert list(file_io.read_csv_any_encoding(path).columns) == ['姓名', '金额']

    def test_plain_utf8_stays_without_bom(self, tmp_path):
        path = str(tmp_path / 'u8.csv')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('姓名,金额\n张三,10\n')
        df = file_io.read_csv_any_encoding(path)
        assert df.attrs['source_encoding'] == 'utf-8'
        file_io.save_csv(path, df)
        with open(path, 'rb') as f:
            assert not f.read(3).startswith(b'\xef\xbb\xbf')

    def test_gbk_round_trip(self, tmp_path):
        path = str(tmp_path / 'g.csv')
        with open(path, 'w', encoding='gbk') as f:
            f.write('姓名,金额\n张三,10\n')
        df = file_io.read_csv_any_encoding(path)
        assert df.attrs['source_encoding'] == 'gbk'
        file_io.save_csv(path, df)
        with open(path, 'r', encoding='gbk') as f:
            assert f.readline().strip() == '姓名,金额'

    def test_big5_detected(self, tmp_path):
        path = str(tmp_path / 'b5.csv')
        with open(path, 'w', encoding='big5') as f:
            f.write('姓名,金額\n王五,10\n')
        df = file_io.read_csv_any_encoding(path)
        assert list(df.columns) == ['姓名', '金額']

    def test_new_csv_defaults_to_utf8_bom(self, tmp_path):
        path = str(tmp_path / 'new.csv')
        file_io.save_csv(path, pd.DataFrame({'a': ['中']}))
        with open(path, 'rb') as f:
            assert f.read(3) == b'\xef\xbb\xbf'


class TestEmptyCsv:
    def test_empty_file_returns_empty_frame(self, tmp_path):
        path = str(tmp_path / 'e.csv')
        open(path, 'w').close()
        df = file_io.read_csv_any_encoding(path)
        assert df.shape == (0, 0)

    def test_blank_lines_only(self, tmp_path):
        path = str(tmp_path / 'b.csv')
        with open(path, 'w') as f:
            f.write('\n\n')
        assert file_io.read_csv_any_encoding(path).shape == (0, 0)


class TestAtomicSave:
    def test_csv_save_keeps_permissions_and_no_temp_left(self, tmp_path):
        path = str(tmp_path / 'p.csv')
        file_io.save_csv(path, pd.DataFrame({'a': [1]}))
        os.chmod(path, 0o664)
        file_io.save_csv(path, pd.DataFrame({'a': [2]}))
        assert os.stat(path).st_mode & 0o777 == 0o664
        assert sorted(os.listdir(tmp_path)) == ['p.csv']

    def test_csv_save_failure_leaves_original_intact(self, tmp_path, monkeypatch):
        path = str(tmp_path / 'p.csv')
        file_io.save_csv(path, pd.DataFrame({'a': [1]}))
        before = open(path, 'rb').read()

        def boom(self, *a, **k):
            raise OSError('disk full')
        monkeypatch.setattr(pd.DataFrame, 'to_csv', boom)
        import pytest
        with pytest.raises(OSError):
            file_io.save_csv(path, pd.DataFrame({'a': [2]}))
        assert open(path, 'rb').read() == before
        assert sorted(os.listdir(tmp_path)) == ['p.csv']

    def test_xlsx_save_keeps_permissions(self, tmp_path):
        path = str(tmp_path / 'p.xlsx')
        file_io.save_workbook(path, {'S': pd.DataFrame({'a': [1]})})
        os.chmod(path, 0o664)
        file_io.save_workbook(path, {'S': pd.DataFrame({'a': [2]})})
        assert os.stat(path).st_mode & 0o777 == 0o664
        assert sorted(os.listdir(tmp_path)) == ['p.xlsx']


class TestSheetNameValidation:
    def test_rules(self):
        assert file_io.check_sheet_name('') is not None
        assert file_io.check_sheet_name('   ') is not None
        assert file_io.check_sheet_name('a' * 31) is None
        assert file_io.check_sheet_name('a' * 32) is not None
        for ch in '\\/?*[]:':
            assert file_io.check_sheet_name('a' + ch) is not None, ch
        assert file_io.check_sheet_name('Abc', ['abc']) is not None   # 不区分大小写
        assert file_io.check_sheet_name('ok', ['x']) is None

    def test_save_rejects_colliding_long_names_instead_of_overwriting(self, tmp_path):
        import pytest
        path = str(tmp_path / 'x.xlsx')
        long_a = 'S' * 31 + 'x'
        long_b = 'S' * 31 + 'y'
        with pytest.raises(ValueError):
            file_io.save_workbook(path, {long_a: pd.DataFrame({'a': [1]}),
                                         long_b: pd.DataFrame({'a': [2]})})
        assert not os.path.exists(path)

    def test_save_rejects_case_duplicate(self, tmp_path):
        import pytest
        with pytest.raises(ValueError):
            file_io.save_workbook(str(tmp_path / 'x.xlsx'),
                                  {'S': pd.DataFrame({'a': [1]}),
                                   's': pd.DataFrame({'a': [2]})})


class TestTextFormats:
    """TSV/TXT 端到端：读取记录分隔符，保存按原分隔符或目标扩展名写回"""

    def test_is_text_format(self):
        assert file_io.is_text_format('a.csv') and file_io.is_text_format('A.TSV')
        assert file_io.is_text_format('/x/y.txt')
        assert not file_io.is_text_format('a.xlsx') and not file_io.is_text_format('a.xls')

    def test_tsv_round_trip_keeps_tabs(self, tmp_path):
        path = str(tmp_path / 't.tsv')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('姓名\t金额\n张三\t10\n李,四\t20\n')
        df = file_io.read_csv_any_encoding(path)
        assert list(df.columns) == ['姓名', '金额']
        assert df.attrs['source_sep'] == '\t'
        assert df['姓名'].tolist() == ['张三', '李,四']
        file_io.save_csv(path, df)
        with open(path, encoding='utf-8') as f:
            assert f.read() == '姓名\t金额\n张三\t10\n李,四\t20\n'
        assert file_io.read_csv_any_encoding(path)['姓名'].tolist() == ['张三', '李,四']

    def test_txt_uses_tab(self, tmp_path):
        path = str(tmp_path / 't.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('a\tb\n1\t2\n')
        df = file_io.read_csv_any_encoding(path)
        assert list(df.columns) == ['a', 'b'] and df.attrs['source_sep'] == '\t'
        out = str(tmp_path / 'new.txt')
        file_io.save_csv(out, pd.DataFrame({'a': [1], 'b': [2]}))   # 新文件按扩展名
        with open(out, encoding='utf-8-sig') as f:
            assert f.read() == 'a\tb\n1\t2\n'
        assert sorted(os.listdir(tmp_path)) == ['new.txt', 't.txt']   # 临时文件不残留

    def test_csv_records_comma_and_custom_delimiter_written_back(self, tmp_path):
        path = str(tmp_path / 'c.csv')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('a;b\n1;2\n')
        df = file_io.read_csv_any_encoding(path, delimiter=';')
        assert df.attrs['source_sep'] == ';'
        file_io.save_csv(path, df)
        with open(path, encoding='utf-8') as f:
            assert f.read() == 'a;b\n1;2\n'
        plain = file_io.read_csv_any_encoding(path.replace('c.csv', 'c.csv'), delimiter=';')
        assert plain.attrs['source_sep'] == ';'

    def test_export_between_formats_follows_target_extension(self, tmp_path):
        tsv = str(tmp_path / 't.tsv')
        with open(tsv, 'w', encoding='utf-8') as f:
            f.write('a\tb\n1\t2\n')
        df = file_io.read_csv_any_encoding(tsv)
        csv = str(tmp_path / 'e.csv')
        file_io.save_csv(csv, df)                        # 导出成 .csv 不带制表符
        with open(csv, encoding='utf-8') as f:
            assert f.read() == 'a,b\n1,2\n'
        df2 = file_io.read_csv_any_encoding(csv)
        out = str(tmp_path / 'o.tsv')
        file_io.save_csv(out, df2)                       # .tsv 一定是制表符
        with open(out, encoding='utf-8') as f:
            assert f.read() == 'a\tb\n1\t2\n'
        file_io.save_csv(csv, df2, sep='|')              # 显式 sep 优先
        with open(csv, encoding='utf-8') as f:
            assert f.read() == 'a|b\n1|2\n'


class TestLeadingZeroRegex:
    def test_float_column_like_10_0_is_not_leading_zero(self, tmp_path):
        # 回归："原文比数字位数长"把 "10.0" 当成前导零，整个浮点列变成文本
        path = str(tmp_path / 'f.csv')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('zip,v,w\n00123,10.0,0\n00456,20.0,100\n')
        df = file_io.read_csv_any_encoding(path)
        assert df['zip'].tolist() == ['00123', '00456']       # 真前导零仍是文本
        assert df['v'].dtype.kind == 'f' and df['v'].tolist() == [10.0, 20.0]
        assert df['w'].dtype.kind == 'i'                       # "0"/"100" 不是前导零

    def test_plus_leading_zero_and_spaces(self, tmp_path):
        path = str(tmp_path / 'p.csv')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('a,b\n+007, 08\n12,9\n')
        df = file_io.read_csv_any_encoding(path)
        assert df['a'].tolist() == ['+007', '12']
        assert df['b'].tolist() == [' 08', '9']


class TestEncodingDetection:
    def test_utf32_bom_detected_before_utf16(self, tmp_path):
        # 回归：UTF-32 LE 的 BOM FF FE 00 00 曾被 2 字节判定当成 UTF-16
        path = str(tmp_path / 'u32.csv')
        with open(path, 'w', encoding='utf-32') as f:
            f.write('姓名,金额\n张三,10\n')
        assert file_io._sniff_bom(path) == 'utf-32'
        df = file_io.read_csv_any_encoding(path)
        assert list(df.columns) == ['姓名', '金额'] and df['金额'].tolist() == [10]
        assert df.attrs['source_encoding'] == 'utf-32'
        path_be = str(tmp_path / 'u32be.csv')
        with open(path_be, 'wb') as f:
            f.write(b'\x00\x00\xfe\xff' + 'a,b\n1,2\n'.encode('utf-32-be'))
        assert file_io._sniff_bom(path_be) == 'utf-32'
        assert list(file_io.read_csv_any_encoding(path_be).columns) == ['a', 'b']

    def test_utf16_bom_still_detected(self, tmp_path):
        path = str(tmp_path / 'u16.csv')
        with open(path, 'w', encoding='utf-16') as f:
            f.write('a,b\n1,2\n')
        assert file_io._sniff_bom(path) == 'utf-16'

    def test_gbk_file_parsed_once(self, tmp_path, monkeypatch):
        # 回归：曾用每种编码整文件 read_csv 一遍（utf-8 失败后再 gbk）
        path = str(tmp_path / 'g.csv')
        with open(path, 'w', encoding='gbk') as f:
            f.write('姓名,金额\n张三,10\n李四,20\n')
        calls = []
        real = pd.read_csv

        def counting(*a, **k):
            calls.append(k.get('encoding'))
            return real(*a, **k)
        monkeypatch.setattr(pd, 'read_csv', counting)
        df = file_io.read_csv_any_encoding(path)
        assert df.attrs['source_encoding'] == 'gbk'
        assert calls == ['gbk']

    def test_sample_cut_inside_multibyte_char(self, tmp_path, monkeypatch):
        # 样本边界切在多字节字符中间不能误判编码
        path = str(tmp_path / 'cut.csv')
        text = '姓名,金额\n张三,10\n'
        for enc, nbytes in (('gbk', 5), ('utf-8', 4)):
            with open(path, 'w', encoding=enc) as f:
                f.write(text)
            monkeypatch.setattr(file_io, '_ENCODING_SAMPLE_BYTES', nbytes)
            assert file_io._detect_encodings(path)[0] == ('gbk' if enc == 'gbk' else 'utf-8-sig')
            df = file_io.read_csv_any_encoding(path)
            assert list(df.columns) == ['姓名', '金额']

    def test_fallback_order_kept_for_undecodable_utf8(self, tmp_path):
        path = str(tmp_path / 'b.csv')
        with open(path, 'w', encoding='big5') as f:
            f.write('姓名,金額\n王五,10\n')
        encs = file_io._detect_encodings(path)
        assert 'utf-8-sig' not in encs
        assert list(encs) == [e for e in file_io.CSV_ENCODINGS if e in encs]
        assert list(file_io.read_csv_any_encoding(path).columns) == ['姓名', '金額']


class TestLossyPartsTranslated:
    def test_labels_go_through_tr(self, tmp_path):
        import zipfile
        from qtui.i18n import tr
        path = str(tmp_path / 'l.xlsx')
        file_io.save_workbook(path, {'S': pd.DataFrame({'a': [1]})})
        with zipfile.ZipFile(path, 'a') as z:
            z.writestr('xl/slicers/slicer1.xml', b'<x/>')
            z.writestr('xl/slicerCaches/c1.xml', b'<x/>')
            z.writestr('xl/vbaProject.bin', b'\x00')
        assert file_io.xlsx_lossy_parts(path) == [tr('切片器'), tr('宏（VBA）')]
