# Smart Table Hub

**English** | [中文](#smart-table-hub---智能表格处理工具)

A powerful Excel alternative built with Python and PyQt6 for fast processing of Excel and CSV files, with a rich set of data manipulation features.

> The app UI is available in English and Chinese — switch via "View → Language" menu, takes effect after restart.

## Download & Install

Download the installer for your platform from [Releases](https://github.com/StellarStar255/stellar_smart_table_quick_analysing_hub/releases/latest):

| Platform | Installer |
|---|---|
| macOS (Apple Silicon) | `SmartTableHub-<版本>-macos-arm64.dmg` |
| macOS (Intel) | `SmartTableHub-<版本>-macos-x86_64.dmg` |
| Ubuntu / Debian | `SmartTableHub-<版本>-linux-amd64.deb` |
| Windows 10/11 (64-bit) | `SmartTableHub-<版本>-windows-x64-setup.exe` |

Once installed, the app checks for new versions automatically. You can also update in one click via "Help → Check for Updates..." (download → SHA256 verification → silent install → automatic restart).

> On macOS, if you see a "cannot verify the developer" warning on first launch, go to
> System Settings → Privacy & Security and click "Open Anyway" (this only happens when the installer is not notarized by Apple).

## Quick Start

```bash
# Pass a file path directly (positional argument)
python smart_table_quick_analysing_hub_qt.py data.xlsx
python smart_table_quick_analysing_hub_qt.py /path/to/file.csv

# Specify a file with -f or --file
python smart_table_quick_analysing_hub_qt.py -f report.xlsx
python smart_table_quick_analysing_hub_qt.py --file /path/to/data.xlsx

# Show help
python smart_table_quick_analysing_hub_qt.py -h

```

## Features

### 📊 File Operations
- **Multi-format support**: Open and save .xlsx, .xls, and .csv files
- **Smart encoding detection**: Automatically detects CSV file encodings (UTF-8, GBK, GB2312, and more)
- **Quick import/export**: One-click CSV import and export
- ✨ **Smart save**: The save dialog pre-selects the file name so you can rename it instantly
- ✨ **Save vs. Save As**:
  - **Save**: switches to the new file after saving
  - **Save As**: saves a copy while you keep editing the original file

### ✏️ Data Editing
- **Intuitive editing**: Double-click any cell to edit
- **Copy & paste**: Standard copy/paste operations
- **Undo/redo**: Full undo/redo support (up to 50 steps)
- **Batch operations**: Multi-row selection and bulk deletion

### 🧮 Cell Formulas
- **Excel-consistent grid**: Fixed letter columns (A/B/C...), row 1 is the editable header row, data starts at row 2 — formula coordinates mean exactly the same thing here and in Excel
- **Excel-style formulas**: Type `=` in any cell, e.g. `=SUM(A2:A10)`, `=IF(A2>10, "high", "low")`
- **39 functions**: SUM / AVERAGE / MAX / MIN / COUNT / COUNTA / COUNTIF / SUMIF / AVERAGEIF / IF / AND / OR / NOT / VLOOKUP / XLOOKUP / INDEX / MATCH / TODAY / NOW / DATE / YEAR / MONTH / DAY / WEEKDAY / DAYS / DATEDIF / ABS / ROUND / POWER / SQRT / MOD / CONCAT / LEFT / RIGHT / MID / LEN / UPPER / LOWER / TRIM, with arbitrary nesting
- **Excel syntax**: `=` and `<>` comparisons, TRUE/FALSE, absolute references (`$A$1`), criteria like `">10"` and wildcards in COUNTIF/SUMIF
- **Auto recalculation**: Dependent formulas update when referenced cells change
- **References follow your data**: Formulas adjust automatically on sort and row/column insert/delete (deleted references show `#REF!`); copy/paste shifts relative references like Excel fill; filtering suspends formulas and restores them when filters are cleared
- **Error codes**: `#DIV/0!`, `#NAME?`, `#NUM!`, `#VALUE!`, `#REF!`, `#N/A`
- **Round-trip with Excel**: Formulas are read from and written back to .xlsx as real formulas

### 🔧 Data Processing
- **Sorting**: Sort any column in ascending or descending order
- **Filtering**: Multiple conditions including equals, contains, greater than, and less than
  - ✨ Pick filter values from a dropdown list
  - ✨ All active filters shown as live tags
  - ✨ Click a tag to edit the filter, click X to remove it
  - ✨ Stack multiple filter conditions
- **Find & replace**: Global find and replace
- **Insert/delete**: Flexible row and column insertion and deletion

### 📈 Statistics
- **Descriptive statistics**: View complete statistics for your data in one click
- **Common functions**: Sum, average, max, min, and count
- **Instant calculation**: Select a column and get its statistics right away

## Keyboard Shortcuts

**File Operations**
- `Ctrl+N`: New file
- `Ctrl+O`: Open file
- `Ctrl+S`: Save (switches to the new file after saving)
- `Ctrl+Shift+S`: Save As a copy (keep editing the original file)

**Editing**
- `Ctrl+Z`: Undo
- `Ctrl+Y`: Redo
- `Ctrl+C`: Copy
- `Ctrl+V`: Paste
- `Delete` / `Backspace`: Clear selected cells
- Just type on a selected cell to overwrite it (Excel-style); arrow keys commit and move
- `Enter`: Commit and move down (`Shift+Enter` moves up); `Tab` commits and moves right, and a following `Enter` returns to the column where you started tabbing
- `F2` / double-click: Edit in place with the caret at the end; `Esc` cancels

**Navigation** ✨New
- `↑` / `↓`: Move up/down (auto page-turn at boundaries)
- `Page Up` / `Page Down`: Previous/next page
- `Home` / `End`: First/last row of the current page

## Run from Source

### 1. Clone or download the project

```bash
cd smart_table_quick_analysing_hub
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the app

```bash
python smart_table_quick_analysing_hub_qt.py
```

## System Requirements

- Python 3.8 or later
- OS: Windows, macOS, Linux

## Dependencies

- `pandas`: Core data processing
- `numpy`: Numerical computation
- `openpyxl`: Excel file I/O
- `PyQt6`: GUI framework

## User Guide

### Basic Operations

1. **Create a new spreadsheet**
   - Click "File" > "New" or press `Ctrl+N`
   - Creates an empty 100-row x 26-column spreadsheet by default

2. **Open an existing file**
   - Click "File" > "Open" or press `Ctrl+O`
   - Select an .xlsx, .xls, or .csv file

3. **Edit data**
   - Double-click any cell to start editing
   - Press `Enter` to confirm your input
   - Press `Esc` to cancel editing

4. **Save your file**

   **Save (switch to the new file)**:
   - Click "Save" on the toolbar or press `Ctrl+S`
   - A dialog appears with the file name pre-selected
   - Rename and save; the app switches to the new file automatically

   **Save As (keep editing the original file)**:
   - Click "Save As" on the toolbar or press `Ctrl+Shift+S`
   - Saves a copy to a new file while you keep editing the original
   - Great for backups or exporting filtered results

### Advanced Features

#### Sorting
1. Click "Data" > "Sort"
2. Choose the column to sort
3. Choose ascending or descending order
4. Click "Sort"

Alternatively, click a column header for quick sorting.

#### Filtering
1. Click "Data" > "Filter"
2. Choose a column and a condition
3. Click the "Value" dropdown arrow and pick a value from the list (or type one)
4. Click "Apply Filter"
5. Filter tags appear below the toolbar
6. Click a tag to edit the filter, click X to remove it
7. Click "Clear All Filters" to restore the original data

**Multiple filters**:
- Apply as many filter conditions as you like, one after another
- All conditions are combined (AND logic)
- The filter bar shows every active filter condition

#### Statistical Analysis
1. Click "Statistics" > "Descriptive Statistics" for a full summary
2. Or pick a specific function (sum, average, etc.)
3. Choose the column to calculate
4. View the result

#### Find & Replace
1. Click "Edit" > "Find & Replace"
2. Enter the search text and the replacement text
3. Click "Replace All"

## Comparison with Excel

| Feature | Smart Table Hub | Microsoft Excel |
|------|-------------|-----------------|
| Open Excel files | ✅ | ✅ |
| Save Excel files | ✅ | ✅ |
| CSV support | ✅ | ✅ |
| Data editing | ✅ | ✅ |
| Sorting & filtering | ✅ | ✅ |
| Basic statistics | ✅ | ✅ |
| Find & replace | ✅ | ✅ |
| Undo/redo | ✅ (50 steps) | ✅ |
| Completely free | ✅ | ❌ |
| Open source | ✅ | ❌ |
| Cross-platform | ✅ | ⚠️ |
| Cell formulas | ✅ (39 functions) | ✅ |
| Charts | ⚠️ Planned | ✅ |

## Roadmap

- [x] Excel formula evaluation (25+ functions, nesting, auto-recalc, `#REF!` tracking)
- [x] Keep formulas while filtering (suspended as static values during filtering, restored and recalculated when filters are cleared)
- [ ] Data visualization (charts)
- [ ] Conditional formatting
- [ ] Pivot tables
- [ ] Multiple worksheets
- [ ] More data processing functions
- [ ] Themes and style customization

## FAQ

**Q: Some Excel files won't open?**
A: Make sure the `openpyxl` library is installed. Legacy .xls files require the `xlrd` library.

**Q: My CSV file shows garbled characters. What can I do?**
A: The app automatically tries multiple encodings (UTF-8, GBK, GB2312, and more). If the problem persists, convert the encoding with a text editor first.

**Q: Does it handle large files?**
A: The app limits the display to the first 1,000 rows for performance. For files with over 100,000 rows, a dedicated database tool is recommended. This tool works best with small to medium datasets (<100k rows).

**Q: Can I export to PDF?**
A: Not in the current version; it is being considered for a future release.

**Q: I see ghosting/double images on macOS. How do I fix it?**
A: Fixed in v1.1.0. The app now detects macOS and adjusts its display settings automatically.

**Q: Opening large files is slow or laggy?**
A: Performance was optimized in v1.2.0. Turn off the "Image Preview" toggle on the toolbar for maximum performance. The app uses paginated display with 50 rows per page.

**Q: Photos in the image preview look squashed?**
A: Fixed in v1.2.0. Images now keep their original aspect ratio without stretching. Photos in 16:9, 4:3, square, and other ratios all display correctly.

**Q: I set 50 rows per page but only 20 are shown?**
A: Fixed in v1.2.0. Previously the row count was automatically reduced for files with many columns. The algorithm has been improved to guarantee at least 30-50 rows. A status hint is shown for files with an exceptionally large number of columns (>150).

## License

This project is licensed under the MIT License — free to use, modify, and distribute.

## Contributing

Issues and Pull Requests are welcome!

---

# Smart Table Hub - 智能表格处理工具

[English](#smart-table-hub) | **中文**

一个功能强大的Excel替代品，使用Python和PyQt6构建，支持快速处理Excel、CSV文件和各种数据操作。

> 应用界面支持中英文切换，菜单「视图 → 语言 / Language」，切换后重启生效。

## 下载安装

从 [Releases](https://github.com/StellarStar255/stellar_smart_table_quick_analysing_hub/releases/latest) 下载对应平台的安装包：

| 平台 | 安装包 |
|---|---|
| macOS（Apple Silicon） | `SmartTableHub-<版本>-macos-arm64.dmg` |
| macOS（Intel） | `SmartTableHub-<版本>-macos-x86_64.dmg` |
| Ubuntu / Debian | `SmartTableHub-<版本>-linux-amd64.deb` |
| Windows 10/11（64 位） | `SmartTableHub-<版本>-windows-x64-setup.exe` |

安装后应用会自动检查新版本，也可通过菜单「帮助 → 检查更新...」
一键升级（下载 → SHA256 校验 → 静默安装 → 自动重启）。

> macOS 首次打开若提示"无法验证开发者"，请在
> 系统设置 → 隐私与安全性 中点击"仍要打开"（安装包未经 Apple 公证时才会出现）。

## Quick Start

```bash
# 直接传入文件路径（位置参数）
python smart_table_quick_analysing_hub_qt.py data.xlsx
python smart_table_quick_analysing_hub_qt.py /path/to/file.csv

# 使用 -f 或 --file 参数指定文件
python smart_table_quick_analysing_hub_qt.py -f report.xlsx
python smart_table_quick_analysing_hub_qt.py --file /path/to/data.xlsx

# 查看帮助信息
python smart_table_quick_analysing_hub_qt.py -h

```




## 功能特性

### 📊 文件操作
- **多格式支持**: 打开和保存 .xlsx, .xls, .csv 文件
- **智能编码**: 自动检测CSV文件编码（UTF-8, GBK, GB2312等）
- **快速导入导出**: 一键导入/导出CSV文件
- ✨ **智能保存**: 保存时弹出对话框，文件名自动全选，方便快速修改
- ✨ **保存 vs 保存为**:
  - **保存**：保存后切换到新文件
  - **保存为**：保存副本但继续编辑原文件

### ✏️ 数据编辑
- **直观编辑**: 双击单元格即可编辑
- **复制粘贴**: 支持标准的复制粘贴操作
- **撤销重做**: 完整的撤销/重做功能（最多50步）
- **批量操作**: 支持多行选择和批量删除

### 🧮 单元格公式
- **Excel 一致的网格**: 固定字母列（A/B/C...），第 1 行是可编辑的表头行，数据从第 2 行起——公式坐标与 Excel 完全一致，跨应用含义相同
- **Excel 风格公式**: 单元格输入 `=` 即可，如 `=SUM(A2:A10)`、`=IF(A2>10, "高", "低")`
- **39 个函数**: SUM / AVERAGE / MAX / MIN / COUNT / COUNTA / COUNTIF / SUMIF / AVERAGEIF / IF / AND / OR / NOT / VLOOKUP / XLOOKUP / INDEX / MATCH / TODAY / NOW / DATE / YEAR / MONTH / DAY / WEEKDAY / DAYS / DATEDIF / ABS / ROUND / POWER / SQRT / MOD / CONCAT / LEFT / RIGHT / MID / LEN / UPPER / LOWER / TRIM，支持任意嵌套
- **Excel 语法**: `=`、`<>` 比较符，TRUE/FALSE，绝对引用（`$A$1`），COUNTIF/SUMIF 支持 `">10"` 条件和通配符
- **自动重算**: 被引用单元格变化时依赖公式自动更新
- **引用跟随数据**: 排序、插入/删除行列后公式自动调整（被删引用显示 `#REF!`），复制粘贴时相对引用平移（同 Excel 填充）；筛选期间公式挂起，清除筛选后恢复重算
- **错误码**: `#DIV/0!`、`#NAME?`、`#NUM!`、`#VALUE!`、`#REF!`、`#N/A`
- **与 Excel 互通**: 公式从 .xlsx 读入，保存时也以真公式写回

### 🔧 数据处理
- **排序**: 按任意列升序或降序排序
- **筛选**: 支持等于、包含、大于、小于等多种筛选条件
  - ✨ 从下拉列表选择筛选值
  - ✨ 实时显示所有筛选条件标签
  - ✨ 点击标签编辑筛选，点击X删除筛选
  - ✨ 支持多个筛选条件叠加
- **查找替换**: 全局查找和替换功能
- **插入/删除**: 灵活插入和删除行列

### 📈 数据统计
- **描述性统计**: 一键查看数据的完整统计信息
- **常用函数**: 求和、平均值、最大值、最小值、计数
- **实时计算**: 选择列后即可计算统计值

### ⌨️ 快捷键

**文件操作**
- `Ctrl+N`: 新建文件
- `Ctrl+O`: 打开文件
- `Ctrl+S`: 保存文件（保存后切换到新文件）
- `Ctrl+Shift+S`: 保存为副本（继续编辑原文件）

**编辑操作**
- `Ctrl+Z`: 撤销
- `Ctrl+Y`: 重做
- `Ctrl+C`: 复制
- `Ctrl+V`: 粘贴
- `Delete` / `Backspace`: 清空选中单元格
- 选中单元格后直接打字即覆盖输入（与 Excel 一致），方向键提交并移动
- `Enter`: 提交并下移（`Shift+Enter` 上移）；`Tab` 提交并右移，之后按 `Enter` 回到开始 Tab 的那一列
- `F2` / 双击: 光标编辑模式（光标在末尾），`Esc` 取消

**导航操作** ✨新
- `↑` / `↓`: 上下移动（到边界时自动翻页）
- `Page Up` / `Page Down`: 上一页/下一页
- `Home` / `End`: 当前页首行/末行

## 安装方法

### 1. 克隆或下载项目

```bash
cd smart_table_quick_analysing_hub
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 运行程序

```bash
python smart_table_quick_analysing_hub_qt.py
```

## 系统要求

- Python 3.8 或更高版本
- 操作系统: Windows, macOS, Linux

## 依赖库

- `pandas`: 数据处理核心库
- `numpy`: 数值计算支持
- `openpyxl`: Excel文件读写
- `PyQt6`: GUI界面

## 使用指南

### 基本操作

1. **创建新表格**
   - 点击"文件" > "新建"或按 `Ctrl+N`
   - 默认创建 100行 x 26列 的空表格

2. **打开现有文件**
   - 点击"文件" > "打开"或按 `Ctrl+O`
   - 选择 .xlsx, .xls 或 .csv 文件

3. **编辑数据**
   - 双击任意单元格开始编辑
   - 输入数据后按 `Enter` 保存
   - 按 `Esc` 取消编辑

4. **保存文件**

   **保存（切换到新文件）**:
   - 点击工具栏"保存"或按 `Ctrl+S`
   - 弹出对话框，文件名自动全选
   - 修改文件名后保存，程序自动切换到新文件

   **保存为（继续编辑原文件）**:
   - 点击工具栏"保存为"或按 `Ctrl+Shift+S`
   - 保存副本到新文件，但继续编辑原文件
   - 适合保存备份或导出筛选结果

### 高级功能

#### 数据排序
1. 点击"数据" > "排序"
2. 选择要排序的列
3. 选择升序或降序
4. 点击"排序"

或者：直接点击列标题进行快速排序

#### 数据筛选
1. 点击"数据" > "筛选"
2. 选择列、条件
3. 点击"值"下拉箭头，从列表选择值（或手动输入）
4. 点击"应用筛选"
5. 工具栏下方会显示筛选条件标签
6. 点击标签可编辑，点击X删除筛选
7. 点击"清除所有筛选"恢复原始数据

**多重筛选**：
- 可以连续应用多个筛选条件
- 所有条件会叠加（AND关系）
- 筛选栏会显示所有激活的筛选条件

#### 统计分析
1. 点击"统计" > "描述性统计"查看完整统计
2. 或选择具体函数（求和、平均值等）
3. 选择要计算的列
4. 查看结果

#### 查找替换
1. 点击"编辑" > "查找替换"
2. 输入查找内容和替换内容
3. 点击"全部替换"

## 功能对比

| 功能 | Smart Table Hub | Microsoft Excel |
|------|-------------|-----------------|
| 打开Excel文件 | ✅ | ✅ |
| 保存Excel文件 | ✅ | ✅ |
| CSV支持 | ✅ | ✅ |
| 数据编辑 | ✅ | ✅ |
| 排序筛选 | ✅ | ✅ |
| 基本统计 | ✅ | ✅ |
| 查找替换 | ✅ | ✅ |
| 撤销重做 | ✅ (50步) | ✅ |
| 完全免费 | ✅ | ❌ |
| 开源 | ✅ | ❌ |
| 跨平台 | ✅ | ⚠️ |
| 单元格公式 | ✅（39 函数） | ✅ |
| 图表 | ⚠️ 待开发 | ✅ |

## 未来规划

- [x] 支持Excel公式计算（25+ 常用函数、嵌套、自动重算、`#REF!` 追踪）
- [x] 筛选时保留公式（筛选期间挂起为静态值，清除筛选后恢复并重算）
- [ ] 数据可视化（图表）
- [ ] 条件格式化
- [ ] 数据透视表
- [ ] 多工作表支持
- [ ] 更多数据处理函数
- [ ] 主题和样式定制

## 常见问题

**Q: 无法打开某些Excel文件？**
A: 请确保安装了 `openpyxl` 库。对于较老的 .xls 文件，需要 `xlrd` 库。

**Q: CSV文件乱码怎么办？**
A: 程序会自动尝试多种编码（UTF-8, GBK, GB2312等），如仍有问题，可以先用文本编辑器转换编码。

**Q: 支持大文件吗？**
A: 程序会自动限制显示前1000行以保证性能。对于超过10万行的大文件，建议使用专业数据库工具。本工具适合中小型数据集（<10万行）。

**Q: 可以导出PDF吗？**
A: 当前版本暂不支持，未来版本会考虑添加。

**Q: macOS上显示有重影怎么办？**
A: v1.1.0已修复此问题。程序会自动检测macOS并调整显示设置。

**Q: 打开大文件很慢或卡顿？**
A: v1.2.0已优化性能。关闭工具栏的"图片预览"开关可获得极致性能。程序使用分页显示，每页50行。

**Q: 图片预览中的照片被拉扁变形？**
A: v1.2.0已修复此问题。图片现在会保持原始宽高比，不会拉伸变形。16:9、4:3、正方形等不同比例的照片都能正确显示。

**Q: 设置每页50行，但实际只显示20行？**
A: v1.2.0已修复此问题。之前对于多列文件会自动降低显示行数，现在优化了算法，确保至少显示30-50行。如果列数特别多（>150列），会显示状态提示。

## 许可证

本项目采用 MIT 许可证，可自由使用、修改和分发。

## 贡献

欢迎提交 Issue 和 Pull Request！
