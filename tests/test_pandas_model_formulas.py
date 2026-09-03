"""表格模型公式联动测试：编辑重算、排序后公式跟随

运行: python3 -m pytest tests/ -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from PyQt6.QtCore import QCoreApplication, Qt

from qtui.pandas_model import PandasTableModel

# QAbstractTableModel 需要应用实例存在（无需事件循环）
_app = QCoreApplication.instance() or QCoreApplication([])


def make_model():
    df = pd.DataFrame({'X': [30.0, 10.0, 20.0], 'Y': [1.0, 2.0, 3.0]})
    return PandasTableModel(df)


class TestVirtualHeaderRow:
    """视图第 0 行是表头行：显示/编辑列名，水平表头是固定字母。"""

    def test_row_count_includes_header(self):
        m = make_model()
        assert m.rowCount() == 4  # 3 数据行 + 1 表头行

    def test_header_row_shows_column_names(self):
        m = make_model()
        assert m.data(m.index(0, 0)) == 'X'
        assert m.data(m.index(0, 1)) == 'Y'

    def test_horizontal_header_is_fixed_letters(self):
        m = make_model()
        assert m.headerData(0, Qt.Orientation.Horizontal) == 'A'
        assert m.headerData(1, Qt.Orientation.Horizontal) == 'B'

    def test_editing_header_row_renames_column(self):
        m = make_model()
        assert m.setData(m.index(0, 0), '销售额')
        assert list(m._df.columns) == ['销售额', 'Y']
        assert m.headerData(0, Qt.Orientation.Horizontal) == 'A'  # 字母不变

    def test_rename_to_existing_name_rejected(self):
        m = make_model()
        assert not m.setData(m.index(0, 0), 'Y')
        assert list(m._df.columns) == ['X', 'Y']

    def test_formula_can_reference_header(self):
        m = make_model()
        m.setData(m.index(1, 1), '=CONCAT(A1, "!")')
        assert m._df.iat[0, 1] == 'X!'

    def test_default_letter_names_display_empty(self):
        # 新建表默认列名就是字母，表头行显示为空（字母已在固定列头）
        df = pd.DataFrame({'A': [1.0], 'B': [2.0]})
        m = PandasTableModel(df)
        assert m.data(m.index(0, 0)) == ''
        assert m.data(m.index(0, 1)) == ''
        m.setData(m.index(0, 0), '名称')
        assert m.data(m.index(0, 0)) == '名称'

    def test_empty_rename_is_silent_noop(self):
        m = make_model()
        messages = []
        m.renameFailed.connect(messages.append)
        assert not m.setData(m.index(0, 0), '')
        assert not messages  # 不弹提示
        assert list(m._df.columns) == ['X', 'Y']


class TestXlsxRoundTrip:
    """公式坐标与 Excel 完全一致：写盘/读盘零转换。"""

    def test_formula_lands_on_excel_cell_and_reads_back(self, tmp_path):
        from qtui import file_io
        from openpyxl import load_workbook
        df = pd.DataFrame({'X': [10.0, 20.0]})
        path = str(tmp_path / 't.xlsx')
        # 公式在数据行 1（视图/Excel 第 3 行），引用第一条数据 A2
        file_io.save_workbook(path, {'S': df}, ['S'],
                              {'S': {(1, 0): '=A2*2'}})
        wb = load_workbook(path)
        assert wb['S']['A3'].value == '=A2*2'  # Excel 里 A2 正是第一条数据
        wb.close()
        formulas = file_io.read_sheet_formulas(path, 'S')
        assert formulas == {(1, 0): '=A2*2'}
        # 读回后引擎按同一坐标计算
        m = PandasTableModel(df.copy())
        m.set_dataframe(df.copy(), formulas=formulas)
        assert m._df.iat[1, 0] == 20  # 10*2


class TestPromoteRowToHeader:
    """真实表头不在首行的文件（如世界银行导出）：把数据行提升为表头"""

    def test_promote_world_bank_style_layout(self):
        df = pd.DataFrame({
            'Data Source': ['Last Updated', None, 'Country Name', '阿鲁巴', '阿富汗'],
            '世界发展指标': ['2026-01-01', None, 'Country Code', 'ABW', 'AFG'],
            'Unnamed: 2': [None, None, 1960.0, 100.0, 200.0],
            'Unnamed: 3': [None, None, None, 300.0, 400.0],
        })
        m = PandasTableModel(df)
        assert m.promote_row_to_header(2)
        # 年份 1960.0 -> '1960'；空表头 -> 位置字母
        assert list(m._df.columns) == ['Country Name', 'Country Code', '1960', 'D']
        assert len(m._df) == 2
        assert m._df.iloc[0, 0] == '阿鲁巴'

    def test_duplicate_header_values_deduped(self):
        df = pd.DataFrame({'A': ['x', 1.0], 'B': ['x', 2.0]})
        m = PandasTableModel(df)
        assert m.promote_row_to_header(0)
        assert list(m._df.columns) == ['x', 'x_1']

    def test_invalid_row_rejected(self):
        m = make_model()
        assert not m.promote_row_to_header(99)
        assert not m.promote_row_to_header(-1)

    def test_promote_reinfers_numeric_columns(self):
        # 原样载入的文件全是文本，提升表头后数值列应恢复为数值类型
        df = pd.DataFrame({'A': ['名称', 'x', 'y'], 'B': ['金额', '100', '200']})
        m = PandasTableModel(df)
        assert m.promote_row_to_header(0)
        assert list(m._df.columns) == ['名称', '金额']
        assert pd.api.types.is_numeric_dtype(m._df['金额'])
        assert m._df['金额'].tolist() == [100, 200]


class TestHeaderRenameIntegrity:
    """坐标系迁移审查回归：重命名的撤销/依赖重算/失败反馈"""

    def test_rename_is_undoable(self):
        m = make_model()
        m.setData(m.index(0, 0), '销售额')
        assert list(m._df.columns) == ['销售额', 'Y']
        assert m.undo()
        assert list(m._df.columns) == ['X', 'Y']
        assert m.redo()
        assert list(m._df.columns) == ['销售额', 'Y']

    def test_rename_undo_does_not_touch_cell_edits(self):
        m = make_model()
        m.setData(m.index(1, 0), '99')       # 单元格编辑
        m.setData(m.index(0, 1), '新列')      # 重命名
        m.undo()                              # 只撤销重命名
        assert list(m._df.columns) == ['X', 'Y']
        assert m._df.iat[0, 0] == 99
        m.undo()                              # 再撤销单元格编辑
        assert m._df.iat[0, 0] == 30

    def test_invalid_rename_emits_feedback(self):
        m = make_model()
        messages = []
        m.renameFailed.connect(messages.append)
        assert not m.setData(m.index(0, 0), 'Y')  # 重名
        assert messages

    def test_header_referencing_formula_recalcs_on_rename(self):
        m = make_model()
        m.setData(m.index(1, 1), '=CONCAT(A1, "!")')
        assert m._df.iat[0, 1] == 'X!'
        m.setData(m.index(0, 0), '销售额')
        assert m._df.iat[0, 1] == '销售额!'

    def test_header_only_range_not_frozen_on_sort(self):
        m = make_model()
        m.setData(m.index(1, 1), '=COUNTA(A1:B1)')  # 纯表头区域
        frozen = m.sort(0, Qt.SortOrder.AscendingOrder)
        assert frozen == 0
        assert m.formulas  # 公式保留


class TestCoordMarker:
    """二轮审查 finding 2/3：坐标版本标记区分本应用文件与外来文件"""

    def test_app_saved_file_has_marker(self, tmp_path):
        from qtui import file_io
        df = pd.DataFrame({'X': [1.0]})
        path = str(tmp_path / 'a.xlsx')
        file_io.save_workbook(path, {'S': df}, ['S'])
        assert file_io.xlsx_has_coord_marker(path)

    def test_foreign_file_has_no_marker(self, tmp_path):
        from qtui import file_io
        path = str(tmp_path / 'b.xlsx')
        pd.DataFrame({'X': [1.0]}).to_excel(path, index=False)
        assert not file_io.xlsx_has_coord_marker(path)
        assert not file_io.xlsx_has_coord_marker(None)  # 无文件时安全返回

    def test_error_formula_survives_app_save_reload(self, tmp_path):
        # finding 2 的完整闭环：本应用保存的错误公式重开后仍显示错误
        from qtui import file_io
        # 公式放在 A4，引用 A2/A3（放在 A2 会自引用 -> 循环引用 #CIRC!）
        df = pd.DataFrame({'X': [10.0, 0.0, 1.0]})
        path = str(tmp_path / 'c.xlsx')
        file_io.save_workbook(path, {'S': df}, ['S'],
                              {'S': {(2, 0): '=A2/A3'}})  # 10/0 -> #DIV/0!
        assert file_io.xlsx_has_coord_marker(path)
        loaded = file_io.read_sheet_formulas(path, 'S')
        m = PandasTableModel(pd.DataFrame())
        # 带标记 -> from_file=False -> 错误结果正常写入
        m.set_dataframe(df.copy(), formulas=loaded,
                        from_file=not file_io.xlsx_has_coord_marker(path))
        assert m._df.iat[2, 0] == '#DIV/0!'


class TestLoadKeepsCachedOnError:
    """审查 finding 1：文件加载路径任何错误都不覆盖 Excel 缓存值"""

    def test_from_file_keeps_cached_on_value_error(self):
        m = make_model()
        df = m._df.copy()
        df.iat[0, 1] = 42.0  # Excel 缓存的正确值
        # 旧版坐标的公式：A1/B1 现在是表头文本相除 -> #VALUE!
        m.set_dataframe(df, formulas={(0, 1): '=A1/B1'}, from_file=True)
        assert m._df.iat[0, 1] == 42.0

    def test_structural_path_still_writes_errors(self):
        m = make_model()
        df = m._df.copy()
        df.iat[0, 1] = 42.0
        m.set_dataframe(df, formulas={(0, 1): '=A1/B1'})  # 非文件路径
        assert m._df.iat[0, 1] == '#VALUE!'


class TestFormulaEditing:
    def test_formula_stores_text_and_result(self):
        m = make_model()
        assert m.setData(m.index(1, 1), '=A2*2')
        assert m.formulas[(0, 1)] == '=A2*2'
        assert m._df.iat[0, 1] == 60

    def test_dependent_recalc_on_edit(self):
        m = make_model()
        m.setData(m.index(1, 1), '=A2+A3')
        assert m._df.iat[0, 1] == 40
        m.setData(m.index(2, 0), '5')  # A2: 10 -> 5
        assert m._df.iat[0, 1] == 35

    def test_overwrite_formula_with_value(self):
        m = make_model()
        m.setData(m.index(1, 1), '=A2*2')
        m.setData(m.index(1, 1), '7')
        assert (0, 1) not in m.formulas
        assert m._df.iat[0, 1] == 7


class TestSortFollowsFormulas:
    def test_formula_cell_moves_with_its_row(self):
        m = make_model()
        m.setData(m.index(1, 1), '=A2*2')  # 行 X=30，结果 60
        m.sort(0, Qt.SortOrder.AscendingOrder)  # X: 10, 20, 30
        # X=30 的行排到第 3 行，公式跟着走且引用重写
        assert m.formulas == {(2, 1): '=A4*2'}
        assert m._df.iat[2, 1] == 60

    def test_recalc_after_sort_keeps_result(self):
        m = make_model()
        m.setData(m.index(2, 1), '=A3+100')  # 行 X=10，结果 110
        m.sort(0, Qt.SortOrder.AscendingOrder)  # X=10 的行排到第 1 行
        assert m.formulas == {(0, 1): '=A2+100'}
        assert m._df.iat[0, 1] == 110

    def test_dependency_tracking_survives_sort(self):
        m = make_model()
        m.setData(m.index(1, 1), '=A2*2')
        m.sort(0, Qt.SortOrder.AscendingOrder)
        # 排序后编辑被引用的单元格，公式应重算
        m.setData(m.index(3, 0), '50')  # 原 X=30 行现在在第 3 行
        assert m._df.iat[2, 1] == 100

    def test_partial_range_formula_frozen_on_sort(self):
        # 部分区域排序后成员会变成无关行，冻结为静态值（审查 finding 4）
        m = make_model()
        m.setData(m.index(1, 1), '=SUM(A2:A3)')  # 30+10 = 40
        frozen = m.reorder_rows([1, 2, 0])  # 相当于升序排序
        assert frozen == 1
        assert m.formulas == {}
        assert m._df.iat[2, 1] == 40  # 排序前的正确值随行移动

    def test_full_range_formula_survives_sort(self):
        m = make_model()
        m.setData(m.index(1, 1), '=SUM(A2:A4)')  # 60
        frozen = m.sort(0, Qt.SortOrder.AscendingOrder)
        assert frozen == 0
        assert m.formulas == {(2, 1): '=SUM(A2:A4)'}
        assert m._df.iat[2, 1] == 60

    def test_sort_without_formulas(self):
        m = make_model()
        m.sort(0, Qt.SortOrder.AscendingOrder)
        assert list(m._df['X']) == [10.0, 20.0, 30.0]


class TestCellColors:
    def test_data_row_color_via_background_role(self):
        m = make_model()
        m.set_cell_color(0, 0, '#ffcc00')
        assert m.data(m.index(1, 0), Qt.ItemDataRole.BackgroundRole).name() == '#ffcc00'
        m.set_cell_color(0, 0, None)
        assert m.data(m.index(1, 0), Qt.ItemDataRole.BackgroundRole) is None

    def test_header_row_color_supported(self):
        # 回归：表头行（视图行 0）也能设背景色，此前被静默跳过
        m = make_model()
        m.set_cell_color(-1, 0, '#ff0000')
        assert m.data(m.index(0, 0), Qt.ItemDataRole.BackgroundRole).name() == '#ff0000'
        m.set_cell_color(-1, 0, None)
        # 清除后回落到表头默认底色（非自定义色）
        assert m.data(m.index(0, 0), Qt.ItemDataRole.BackgroundRole).name() != '#ff0000'

    def test_color_batch_is_undoable(self):
        # 回归：设置背景颜色可用 Cmd+Z 撤销；一次选区着色 = 一条记录
        m = make_model()
        m.set_cell_color(0, 0, '#111111')   # 已有颜色，撤销应还原它
        m.apply_cell_colors([(-1, 0), (0, 0), (1, 1)], '#ffcc00')
        assert m.cell_colors[(-1, 0)] == '#ffcc00'
        assert m.undo()
        assert (-1, 0) not in m.cell_colors          # 原本无色 -> 清除
        assert m.cell_colors[(0, 0)] == '#111111'    # 原有色 -> 还原
        assert (1, 1) not in m.cell_colors
        assert m.redo()
        assert m.cell_colors[(0, 0)] == '#ffcc00'

    def test_color_undo_interleaves_with_cell_edits(self):
        m = make_model()
        m.setData(m.index(1, 0), '99')
        m.apply_cell_colors([(0, 1)], '#ff0000')
        m.undo()                                     # 先撤销颜色
        assert (0, 1) not in m.cell_colors
        assert m._df.iat[0, 0] == 99
        m.undo()                                     # 再撤销单元格编辑
        assert m._df.iat[0, 0] == 30

    def test_noop_color_not_recorded(self):
        m = make_model()
        m.set_cell_color(0, 0, '#ff0000')
        assert not m.apply_cell_colors([(0, 0)], '#ff0000')  # 无变化
        assert not m._undo_stack

    def test_header_color_survives_sort(self):
        m = make_model()
        m.set_cell_color(-1, 0, '#ff0000')
        m.set_cell_color(1, 1, '#00ff00')
        m.reorder_rows([1, 2, 0])
        assert m.cell_colors[(-1, 0)] == '#ff0000'   # 表头色不动
        assert m.cell_colors[(0, 1)] == '#00ff00'    # 数据行色跟随（旧1->新0）


class TestReorderRows:
    def test_reorder_moves_formulas_colors_and_data(self):
        m = make_model()
        m.setData(m.index(1, 1), '=A2*2')  # 60
        m.cell_colors[(0, 0)] = '#ff0000'
        m.reorder_rows([2, 0, 1])  # 旧行 2/0/1 -> 新行 0/1/2
        assert list(m._df['X']) == [20.0, 30.0, 10.0]
        assert m.formulas == {(1, 1): '=A3*2'}
        assert m.cell_colors == {(1, 0): '#ff0000'}
        assert m._df.iat[1, 1] == 60

    def test_reorder_clears_undo_and_marks_modified(self):
        m = make_model()
        m.setData(m.index(1, 0), '99')
        m.reorder_rows([1, 2, 0])
        assert m.modified
        assert not m._undo_stack


class TestInsertDeleteFollowsFormulas:
    def test_insert_row_shifts_formula_and_refs(self):
        m = make_model()
        m.setData(m.index(1, 1), '=A3')  # 引用 X=10
        m.insert_row(0)
        # 公式随行下移，引用也指向下移后的数据
        assert m.formulas == {(1, 1): '=A4'}
        assert m._df.iat[1, 1] == 10

    def test_insert_row_below_does_not_touch_refs(self):
        m = make_model()
        m.setData(m.index(1, 1), '=A2*2')
        m.insert_row(2)
        assert m.formulas == {(0, 1): '=A2*2'}
        assert m._df.iat[0, 1] == 60

    def test_insert_row_grows_range(self):
        m = make_model()
        m.setData(m.index(1, 1), '=SUM(A2:A4)')  # 60
        m.insert_row(1)
        assert m.formulas == {(0, 1): '=SUM(A2:A5)'}
        assert m._df.iat[0, 1] == 60  # 插入的空行按 0 计

    def test_insert_column_shifts_refs(self):
        m = make_model()
        m.setData(m.index(1, 1), '=A2*2')
        m.insert_column(0)
        assert m.formulas == {(0, 2): '=B2*2'}
        assert m._df.iat[0, 2] == 60

    def test_delete_referenced_row_becomes_ref_error(self):
        m = make_model()
        m.setData(m.index(1, 1), '=A3')
        m.remove_rows([1])
        assert m.formulas == {(0, 1): '=#REF!'}
        assert m._df.iat[0, 1] == '#REF!'

    def test_delete_row_inside_range_shrinks_and_recalcs(self):
        m = make_model()
        m.setData(m.index(1, 1), '=SUM(A2:A4)')  # 60
        m.remove_rows([1])  # 删掉 X=10
        assert m.formulas == {(0, 1): '=SUM(A2:A3)'}
        assert m._df.iat[0, 1] == 50

    def test_delete_referenced_column_becomes_ref_error(self):
        m = make_model()
        m.setData(m.index(1, 1), '=A2*2')
        m.remove_columns([0])
        assert m.formulas == {(0, 0): '=#REF!*2'}
        assert m._df.iat[0, 0] == '#REF!'

    def test_delete_formula_cell_row_drops_formula(self):
        m = make_model()
        m.setData(m.index(2, 1), '=A2')
        m.remove_rows([1])
        assert m.formulas == {}


class TestReviewFindings:
    """代码审查确认问题的回归测试（finding 1/6/7）"""

    def test_new_error_after_delete_is_written(self):
        # finding 1: 结构变更后新产生的 #DIV/0! 必须写入，不能保留旧值
        m = make_model()
        m.setData(m.index(3, 1), '=AVERAGEIF(A2:A4, ">25")')  # 只有 X=30 匹配
        assert m._df.iat[2, 1] == 30
        m.remove_rows([0])  # 删掉唯一匹配行；公式收缩为 A1:A2 且无匹配
        assert m.formulas == {(1, 1): '=AVERAGEIF(A2:A3, ">25")'}
        assert m._df.iat[1, 1] == '#DIV/0!'

    def test_name_error_keeps_cached_value_on_load(self):
        # finding 1 的反面：不支持的函数（#NAME?）仍保留 Excel 缓存值
        m = make_model()
        df = m._df.copy()
        df.iat[0, 1] = 42.0  # Excel 里的缓存计算值
        # 区域不含公式所在的 B2 本身（自引用是循环引用，另有测试）
        m.set_dataframe(df, formulas={(0, 1): '=SUMPRODUCT(A2:A4, B3:B4)'})
        assert m._df.iat[0, 1] == 42.0

    def test_insert_column_clears_undo(self):
        # finding 6: 列插入后旧撤销记录会写错列
        m = make_model()
        m.setData(m.index(1, 1), '99')
        assert m._undo_stack
        m.insert_column(0)
        assert not m._undo_stack and not m._redo_stack

    def test_remove_columns_clears_undo(self):
        m = make_model()
        m.setData(m.index(1, 1), '99')
        m.remove_columns([0])
        assert not m._undo_stack

    def test_structure_version_bumps_on_structural_ops(self):
        # finding 7: 结构版本号供公式剪贴板判断失效
        m = make_model()
        v = m.structure_version
        m.insert_row(0); assert m.structure_version > v; v = m.structure_version
        m.remove_rows([0]); assert m.structure_version > v; v = m.structure_version
        m.insert_column(0); assert m.structure_version > v; v = m.structure_version
        m.remove_columns([0]); assert m.structure_version > v; v = m.structure_version
        m.reorder_rows([1, 2, 0]); assert m.structure_version > v; v = m.structure_version
        m.set_dataframe(m._df.copy()); assert m.structure_version > v
        # 普通编辑不 bump
        v = m.structure_version
        m.setData(m.index(1, 0), '7')
        assert m.structure_version == v


# ---------- 全面审查回归：依赖顺序、长链、显示与 dtype ----------

class TestTopologicalRecalc:
    def test_diamond_dependency_uses_fresh_values(self):
        # B2 = A2*2, C2 = A2+B2：无论集合迭代顺序如何 C2 都用新的 B2
        for _ in range(20):
            m = PandasTableModel(pd.DataFrame({'A': [1.0], 'B': [0.0], 'C': [0.0]}))
            m.setData(m.index(1, 2), '=A2+B2')
            m.setData(m.index(1, 1), '=A2*2')
            m.setData(m.index(1, 0), '5')
            assert m._df.iat[0, 1] == 10
            assert m._df.iat[0, 2] == 15

    def test_evaluate_all_respects_dependency_order(self):
        m = PandasTableModel(pd.DataFrame())
        m.set_dataframe(pd.DataFrame({'A': [0.0, 0.0]}),
                        formulas={(0, 0): '=A3+1', (1, 0): '=5'})
        assert m._df.iat[0, 0] == 6

    def test_remove_rows_recalcs_chained_formulas(self):
        m = PandasTableModel(pd.DataFrame({'A': [10.0, 20.0, 30.0], 'B': [0.0] * 3, 'C': [0.0] * 3}))
        m.setData(m.index(1, 2), '=B2*2')          # C2 先创建（字典顺序在前）
        m.setData(m.index(1, 1), '=SUM(A2:A4)')    # B2 = 60, C2 = 120
        assert m._df.iat[0, 2] == 120
        m.remove_rows([1])                          # B2 = 40 -> C2 = 80
        assert m._df.iat[0, 1] == 40
        assert m._df.iat[0, 2] == 80

    def test_long_chain_does_not_recurse(self):
        n = 2000
        m = PandasTableModel(pd.DataFrame({'A': [0.0] * n}))
        formulas = {(i, 0): f'=A{i + 1}+1' for i in range(1, n)}
        m.set_dataframe(pd.DataFrame({'A': [0.0] * n}), formulas=formulas)
        m.setData(m.index(1, 0), '10')
        assert m._df.iat[n - 1, 0] == 10 + n - 1

    def test_self_reference_is_circular(self):
        m = make_model()
        m.setData(m.index(1, 0), '=A2+1')
        assert m._df.iat[0, 0] == '#CIRC!'

    def test_mutual_reference_is_circular(self):
        m = make_model()
        m.setData(m.index(1, 0), '=B2')
        m.setData(m.index(1, 1), '=A2')
        assert m._df.iat[0, 0] == '#CIRC!'
        assert m._df.iat[0, 1] == '#CIRC!'
        # 解除循环后恢复正常（列已退化为 object，输入保持文本）
        m.setData(m.index(1, 1), '7')
        assert m._df.iat[0, 0] == '7'

    def test_range_result_does_not_break_display(self):
        m = make_model()
        m.setData(m.index(1, 1), '=A2:A4')
        assert m._df.iat[0, 1] == '#VALUE!'
        assert m.data(m.index(1, 1)) == '#VALUE!'


class TestDisplayFormatting:
    def test_datetime_column_display(self):
        df = pd.DataFrame({'D': [pd.Timestamp('2026-01-15'),
                                 pd.Timestamp('2026-01-15 08:30:00'), pd.NaT]})
        m = PandasTableModel(df)
        assert m.data(m.index(1, 0)) == '2026-01-15'
        assert m.data(m.index(2, 0)) == '2026-01-15 08:30:00'
        assert m.data(m.index(3, 0)) == ''

    def test_huge_float_display(self):
        m = PandasTableModel(pd.DataFrame({'A': [1e300, 12.0, float('nan')]}))
        assert m.data(m.index(1, 0)) == '1e+300'
        assert m.data(m.index(2, 0)) == '12'
        assert m.data(m.index(3, 0)) == ''

    def test_non_scalar_value_does_not_raise(self):
        m = PandasTableModel(pd.DataFrame({'A': [[1, 2], None]}, dtype=object))
        assert m.data(m.index(1, 0)) == '[1, 2]'
        assert m.data(m.index(2, 0)) == ''


class TestDtypeHandling:
    def test_undo_restores_numeric_dtype(self):
        m = make_model()
        m.setData(m.index(1, 0), 'abc')
        assert m._df.iloc[:, 0].dtype == object
        m.undo()
        assert pd.api.types.is_float_dtype(m._df.iloc[:, 0].dtype)
        assert m._df.iat[0, 0] == 30.0

    def test_sort_mixed_column_does_not_raise(self):
        m = make_model()
        m.setData(m.index(1, 0), '=1/0')            # 列变 object，含 '#DIV/0!'
        m.sort(0, Qt.SortOrder.AscendingOrder)
        values = list(m._df.iloc[:, 0])
        assert values[:2] == [10.0, 20.0]
        assert values[2] == '#DIV/0!'

    def test_promote_header_keeps_leading_zeros(self):
        df = pd.DataFrame({'A': ['id', '00123', '00456'], 'B': ['n', '1', '2']})
        m = PandasTableModel(df)
        m.promote_row_to_header(0)
        assert list(m._df.columns) == ['id', 'n']
        assert list(m._df['id']) == ['00123', '00456']
        assert list(m._df['n']) == [1, 2]

    def test_clear_formulas_clears_indexes(self):
        m = make_model()
        m.setData(m.index(1, 1), '=A2*2')
        m.clear_formulas()
        assert not m.formulas and not m._dependents and not m._formula_deps
