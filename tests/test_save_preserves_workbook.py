"""保存不再重建工作簿：透视表之类 openpyxl 能读回的东西必须原样保留。

用条件格式/数据验证/列宽/冻结窗格/合并单元格/图表当替身来验证——它们和
透视表走的是同一条"openpyxl 读回再写出"的路径。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from PyQt6.QtWidgets import QApplication, QMessageBox

_app = QApplication.instance() or QApplication([])

from qtui import file_io
from qtui.i18n import tr
from qtui.main_window import MainWindow


def make_rich_xlsx(path, rows=5):
    wb = Workbook()
    ws = wb.active
    ws.title = "数据"
    ws.append(["a", "b"])
    for i in range(1, rows + 1):
        ws.append([i, i * 2])
    ws.column_dimensions["A"].width = 33
    ws.freeze_panes = "A2"
    ws.merge_cells("A{0}:B{0}".format(rows + 1))   # 数据区最后一行合并（表外的合并区会随删列一起删除）
    ws.conditional_formatting.add(
        "A2:A99", CellIsRule(operator="greaterThan", formula=["2"],
                             fill=PatternFill(bgColor="FFC7CE")))
    dv = DataValidation(type="list", formula1='"x,y"')
    ws.add_data_validation(dv)
    dv.add("C2:C99")
    chart = BarChart()
    chart.add_data(Reference(ws, min_col=1, min_row=1, max_row=rows + 1),
                   titles_from_data=True)
    ws.add_chart(chart, "G2")
    other = wb.create_sheet("原样")
    other.append(["k", "v"])
    other.append([1, "=A2*10"])
    other["B3"] = "别动我"
    wb.save(path)
    return path


def features(path, sheet="数据"):
    ws = load_workbook(path)[sheet]
    return {
        "cf": len(ws.conditional_formatting._cf_rules),
        "dv": len(ws.data_validations.dataValidation),
        "width": ws.column_dimensions["A"].width,
        "freeze": ws.freeze_panes,
        "merged": [str(r) for r in ws.merged_cells.ranges],
        "charts": len(ws._charts),
    }


class TestPatchWorkbook:
    def test_keeps_everything_pandas_rebuild_loses(self, tmp_path):
        src = make_rich_xlsx(str(tmp_path / "src.xlsx"))
        before = features(src)
        df = pd.DataFrame({"a": [1, 2, 3, 4, 5], "b": [9, 4, 6, 8, 10]})
        file_io.patch_workbook(src, src, {"数据": df}, ["数据", "原样"])
        assert features(src) == before
        ws = load_workbook(src)["数据"]
        assert ws["B2"].value == 9          # 改动写进去了

    def test_untouched_sheet_keeps_its_formula(self, tmp_path):
        src = make_rich_xlsx(str(tmp_path / "src.xlsx"))
        df = pd.DataFrame({"a": [1], "b": [2]})
        file_io.patch_workbook(src, src, {"数据": df}, ["数据", "原样"])
        ws = load_workbook(src)["原样"]
        assert ws["B2"].value == "=A2*10"
        assert ws["B3"].value == "别动我"

    def test_shrinking_table_deletes_leftover_rows(self, tmp_path):
        src = make_rich_xlsx(str(tmp_path / "src.xlsx"), rows=8)
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        file_io.patch_workbook(src, src, {"数据": df}, ["数据", "原样"])
        ws = load_workbook(src)["数据"]
        assert ws.max_row == 3
        assert ws["A4"].value is None

    def test_header_formula_is_kept_not_flattened(self, tmp_path):
        src = str(tmp_path / "h.xlsx")
        wb = Workbook(); ws = wb.active; ws.title = "S"
        ws["A1"] = "=CONCATENATE(\"col\",\"A\")"
        ws["B1"] = "b"
        ws.append([1, 2])
        wb.save(src)
        df = pd.DataFrame({"colA": [7], "b": [8]})
        result = file_io.patch_workbook(src, src, {"S": df}, ["S"])
        ws = load_workbook(src)["S"]
        assert ws["A1"].value == "=CONCATENATE(\"col\",\"A\")"
        assert ws["B1"].value == "b"
        assert result["kept_header_formulas"] == 1

    def test_formulas_and_colors_written(self, tmp_path):
        src = make_rich_xlsx(str(tmp_path / "src.xlsx"))
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        file_io.patch_workbook(src, src, {"数据": df}, ["数据", "原样"],
                               formulas={"数据": {(0, 1): "=A2*3"}},
                               cell_colors={"数据": {(-1, 0): "#ff0000",
                                                     (1, 0): "#00ff00"}})
        ws = load_workbook(src)["数据"]
        assert ws["B2"].value == "=A2*3"
        assert ws["A1"].fill.start_color.rgb == "FFFF0000"
        assert ws["A3"].fill.start_color.rgb == "FF00FF00"

    def test_cleared_color_is_removed(self, tmp_path):
        src = str(tmp_path / "c.xlsx")
        wb = Workbook(); ws = wb.active; ws.title = "S"
        ws.append(["a"]); ws.append([1])
        ws["A2"].fill = PatternFill(start_color="FF00FF00", end_color="FF00FF00",
                                    fill_type="solid")
        wb.save(src)
        df = pd.DataFrame({"a": [1]})
        file_io.patch_workbook(src, src, {"S": df}, ["S"], cell_colors={"S": {}})
        assert load_workbook(src)["S"]["A2"].fill.fill_type is None

    def test_sheet_add_delete_and_order(self, tmp_path):
        src = make_rich_xlsx(str(tmp_path / "src.xlsx"))
        df = pd.DataFrame({"x": [1]})
        file_io.patch_workbook(src, src, {"新表": df}, ["新表", "数据"])
        wb = load_workbook(src)
        assert wb.sheetnames == ["新表", "数据"]   # 原样 sheet 被删除，顺序对齐

    def test_numpy_and_missing_values(self, tmp_path):
        src = str(tmp_path / "n.xlsx")
        wb = Workbook(); ws = wb.active; ws.title = "S"
        ws.append(["a", "b", "c"]); ws.append([0, 0, 0])
        wb.save(src)
        df = pd.DataFrame({"a": [np.int64(5)], "b": [np.nan],
                           "c": [pd.Timestamp("2026-01-02 03:04:05")]})
        file_io.patch_workbook(src, src, {"S": df}, ["S"])
        ws = load_workbook(src)["S"]
        assert ws["A2"].value == 5 and ws["B2"].value is None
        assert str(ws["C2"].value) == "2026-01-02 03:04:05"

    def test_save_as_copy_keeps_source_intact(self, tmp_path):
        src = make_rich_xlsx(str(tmp_path / "src.xlsx"))
        dest = str(tmp_path / "copy.xlsx")
        df = pd.DataFrame({"a": [7, 2, 3, 4, 5], "b": [8, 4, 6, 8, 10]})
        file_io.patch_workbook(src, dest, {"数据": df}, ["数据", "原样"])
        assert features(dest) == features(src)          # 副本继承全部设置
        assert load_workbook(dest)["数据"]["A2"].value == 7
        assert load_workbook(src)["数据"]["A2"].value == 1   # 原文件没被动


class TestLossyParts:
    def test_plain_file_has_nothing_to_warn_about(self, tmp_path):
        src = make_rich_xlsx(str(tmp_path / "src.xlsx"))
        assert file_io.xlsx_lossy_parts(src) == []

    def test_macro_workbook_is_flagged(self, tmp_path):
        import zipfile
        src = make_rich_xlsx(str(tmp_path / "src.xlsx"))
        with zipfile.ZipFile(src, "a") as z:
            z.writestr("xl/vbaProject.bin", b"\x00")
        assert tr("宏（VBA）") in file_io.xlsx_lossy_parts(src)   # 已按界面语言翻译


class TestSaveThroughWindow:
    def test_editing_a_cell_keeps_workbook_features(self, tmp_path, monkeypatch):
        src = make_rich_xlsx(str(tmp_path / "src.xlsx"))
        before = features(src)
        win = MainWindow()
        win.load_file(src)
        _app.processEvents()
        win.model.setData(win.model.index(1, 1), "99")
        assert win.save_file() is True
        _app.processEvents()
        assert features(src) == before
        ws = load_workbook(src)["数据"]
        assert ws["B2"].value == 99
        assert ws["A2"].value == 1
        win.model.modified = False
        win.close()

    def test_lossy_features_ask_before_saving(self, tmp_path, monkeypatch):
        import zipfile
        src = make_rich_xlsx(str(tmp_path / "src.xlsx"))
        with zipfile.ZipFile(src, "a") as z:
            z.writestr("xl/slicers/slicer1.xml", b"<x/>")
        win = MainWindow()
        win.load_file(src)
        _app.processEvents()
        win.model.setData(win.model.index(1, 1), "99")
        asked = []
        monkeypatch.setattr(QMessageBox, "warning",
                            lambda *a, **k: (asked.append(a[2]),
                                             QMessageBox.StandardButton.Cancel)[1])
        assert win.save_file() is False
        assert asked and tr("切片器") in asked[0]
        assert load_workbook(src)["数据"]["B2"].value == 2   # 取消后原文件没动
        win.model.modified = False
        win.close()


class TestFillsAppNeverReadArePreserved:
    """就地保存只清应用认识的底色；主题色/调色板色不能被抹掉，也不能读成垃圾"""

    def _make(self, path):
        from openpyxl.styles.colors import Color
        wb = Workbook(); ws = wb.active; ws.title = "S"
        ws.append(["a", "b", "c", "d"]); ws.append([1, 2, 3, 4]); ws.append([5, 6, 7, 8])
        ws["A2"].fill = PatternFill(fill_type="solid", fgColor=Color(theme=4, tint=0.4))
        ws["B2"].fill = PatternFill(fill_type="solid", fgColor=Color(indexed=5))
        ws["C2"].fill = PatternFill(fill_type="solid", fgColor="FF00FF00")
        ws["D3"].fill = PatternFill(fill_type="solid", fgColor=Color(theme=7))
        wb.save(path)
        return path

    def test_read_resolves_theme_and_indexed_to_rgb(self, tmp_path):
        src = self._make(str(tmp_path / "t.xlsx"))
        colors = file_io.read_sheet_colors(src, "S")
        assert colors[(0, 2)] == "#00ff00"
        assert colors[(0, 1)] == "#ffff00"                    # 默认调色板第 5 号是黄色
        import re
        for key in ((0, 0), (1, 3)):
            assert re.fullmatch(r"#[0-9a-f]{6}", colors[key]), colors[key]   # 不再是 "#'str'>"
        assert colors[(0, 0)] != colors[(1, 3)]

    def test_inplace_save_keeps_theme_fill_objects_when_unchanged(self, tmp_path):
        src = self._make(str(tmp_path / "t.xlsx"))
        colors = file_io.read_sheet_colors(src, "S")
        df = pd.DataFrame({"a": [1, 5], "b": [2, 6], "c": [3, 7], "d": [4, 8]})
        file_io.patch_workbook(src, src, {"S": df}, ["S"], cell_colors={"S": colors})
        ws = load_workbook(src)["S"]
        assert ws["A2"].fill.fgColor.type == "theme"
        assert ws["A2"].fill.fgColor.theme == 4 and abs(ws["A2"].fill.fgColor.tint - 0.4) < 1e-9
        assert ws["B2"].fill.fgColor.type == "indexed" and ws["B2"].fill.fgColor.indexed == 5
        assert ws["D3"].fill.fgColor.type == "theme" and ws["D3"].fill.fgColor.theme == 7
        assert ws["C2"].fill.fgColor.rgb == "FF00FF00"
        assert file_io.read_sheet_colors(src, "S") == colors   # 再读一遍完全一致

    def test_user_cleared_known_fill_is_removed_but_unknown_kept(self, tmp_path, monkeypatch):
        src = self._make(str(tmp_path / "t.xlsx"))
        # 主题表解析不出来时（应用从没显示过这些主题色）：即使用户清空全部颜色也原样保留
        monkeypatch.setattr(file_io, "_theme_palette", lambda wb: None)
        assert (0, 0) not in file_io.read_sheet_colors(src, "S")
        df = pd.DataFrame({"a": [1, 5], "b": [2, 6], "c": [3, 7], "d": [4, 8]})
        file_io.patch_workbook(src, src, {"S": df}, ["S"], cell_colors={"S": {}})
        ws = load_workbook(src)["S"]
        assert ws["A2"].fill.fgColor.type == "theme" and ws["A2"].fill.fill_type == "solid"
        assert ws["D3"].fill.fgColor.type == "theme"
        assert ws["B2"].fill.fill_type is None        # 调色板色应用认识：用户清了就清掉
        assert ws["C2"].fill.fill_type is None

    def test_changed_color_overrides_theme_fill(self, tmp_path):
        src = self._make(str(tmp_path / "t.xlsx"))
        colors = file_io.read_sheet_colors(src, "S")
        colors[(0, 0)] = "#123456"
        df = pd.DataFrame({"a": [1, 5], "b": [2, 6], "c": [3, 7], "d": [4, 8]})
        file_io.patch_workbook(src, src, {"S": df}, ["S"], cell_colors={"S": colors})
        ws = load_workbook(src)["S"]
        assert ws["A2"].fill.fgColor.rgb == "FF123456"


class TestArrayFormulas:
    def _make(self, path):
        from openpyxl.worksheet.formula import ArrayFormula
        wb = Workbook(); ws = wb.active; ws.title = "S"
        ws.append(["a", "b", "c"]); ws.append([1, 2, None]); ws.append([3, 4, None])
        ws["C2"] = ArrayFormula("C2", "=SUM(A2:A3*B2:B3)")
        ws["C3"] = "=A3+B3"
        wb.save(path)
        return path

    def test_read_sheet_formulas_sees_array_formula(self, tmp_path):
        src = self._make(str(tmp_path / "af.xlsx"))
        assert file_io.read_sheet_formulas(src, "S") == {(0, 2): "=SUM(A2:A3*B2:B3)",
                                                         (1, 2): "=A3+B3"}

    def test_inplace_save_keeps_array_formula(self, tmp_path):
        from openpyxl.worksheet.formula import ArrayFormula
        src = self._make(str(tmp_path / "af.xlsx"))
        formulas = file_io.read_sheet_formulas(src, "S")
        df = pd.DataFrame({"a": [1, 3], "b": [2, 4], "c": [14.0, 7.0]})   # c 列是缓存值
        file_io.patch_workbook(src, src, {"S": df}, ["S"], formulas={"S": formulas})
        ws = load_workbook(src)["S"]
        assert isinstance(ws["C2"].value, ArrayFormula)
        assert ws["C2"].value.text == "=SUM(A2:A3*B2:B3)" and ws["C2"].value.ref == "C2"
        assert ws["C3"].value == "=A3+B3"
        # 调用方没有公式信息时同样不能用缓存值把数组公式盖掉
        file_io.patch_workbook(src, src, {"S": df}, ["S"])
        assert isinstance(load_workbook(src)["S"]["C2"].value, ArrayFormula)

    def test_edited_array_formula_written_back_as_array(self, tmp_path):
        from openpyxl.worksheet.formula import ArrayFormula
        src = self._make(str(tmp_path / "af.xlsx"))
        df = pd.DataFrame({"a": [1, 3], "b": [2, 4], "c": [0.0, 7.0]})
        file_io.patch_workbook(src, src, {"S": df}, ["S"],
                               formulas={"S": {(0, 2): "=SUM(A2:A3+B2:B3)", (1, 2): "=A3+B3"}})
        v = load_workbook(src)["S"]["C2"].value
        assert isinstance(v, ArrayFormula) and v.text == "=SUM(A2:A3+B2:B3)" and v.ref == "C2"

    def test_user_removed_array_formula_becomes_value(self, tmp_path):
        src = self._make(str(tmp_path / "af.xlsx"))
        df = pd.DataFrame({"a": [1, 3], "b": [2, 4], "c": [99.0, 7.0]})
        file_io.patch_workbook(src, src, {"S": df}, ["S"], formulas={"S": {(1, 2): "=A3+B3"}})
        assert load_workbook(src)["S"]["C2"].value == 99


class TestMergedRangesOnShrink:
    def _make(self, path):
        wb = Workbook(); ws = wb.active; ws.title = "S"
        ws.append(["a", "b", "c"])
        for i in range(1, 9):
            ws.append([i, i * 2, i * 3])
        ws.merge_cells("A2:A3")     # 完全在新范围内：保留
        ws.merge_cells("B3:B6")     # 跨出新范围：裁到 B3:B4
        ws.merge_cells("A8:A9")     # 整体在删掉的行里：删除
        ws.merge_cells("C5:C9")     # 整列被删：删除
        ws.merge_cells("E1:F1")     # 表头右侧被删的列：删除
        wb.save(path)
        return path

    def test_no_crash_and_ranges_synced(self, tmp_path):
        src = self._make(str(tmp_path / "m.xlsx"))
        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        result = file_io.patch_workbook(src, src, {"S": df}, ["S"])
        ws = load_workbook(src)["S"]
        assert ws.max_row == 4 and ws.max_column == 2
        assert sorted(str(r) for r in ws.merged_cells.ranges) == ["A2:A3", "B3:B4"]
        assert ws["A2"].value == 1 and ws["B3"].value == 5   # 合并区左上角照常写入
        assert result["skipped_merged_cells"] == 2      # A3/B4 是占位格，df 里的 2/6 写不进去

    def test_merged_placeholder_without_value_is_not_counted(self, tmp_path):
        src = str(tmp_path / "m2.xlsx")
        wb = Workbook(); ws = wb.active; ws.title = "S"
        ws.append(["a", "b"]); ws.append([1, None]); ws.merge_cells("A2:B2")
        wb.save(src)
        df = pd.DataFrame({"a": [7], "b": [None]})
        result = file_io.patch_workbook(src, src, {"S": df}, ["S"])
        assert result["skipped_merged_cells"] == 0
        ws = load_workbook(src)["S"]
        assert ws["A2"].value == 7 and [str(r) for r in ws.merged_cells.ranges] == ["A2:B2"]
