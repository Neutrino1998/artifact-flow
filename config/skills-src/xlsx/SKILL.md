---
name: xlsx
description: >
  读取、分析、创建、修改和质检 Excel 工作簿(.xlsx/.xls/.csv)，包括公式、格式、
  LibreOffice 重算和页面渲染。用户提供表格、要求数据分析或交付 Excel/PDF 时激活。
  工作在无网络沙盒中，分析用 pandas，编辑用 openpyxl，重算与转换用 LibreOffice。
license: Apache-2.0
compatibility: 需要沙盒(bash/mount/persist)。镜像已烤 LibreOffice、pandas、openpyxl。
metadata:
  version: "2.0.0"
---

# 表格

先 `mount` 文件并 `mount_skill` 本技能，技能目录记作
`SKILL=/workspace/.skills/xlsx`。包内工具：[check_formulas.py](scripts/check_formulas.py)。

职责固定：**pandas 做分析，openpyxl 保留公式与样式做读写，LibreOffice 做兼容转换、公式重算和渲染。**
不要用 CSV 或 DataFrame 往返保存一个需要保留样式、公式或多工作表的 xlsx。

## 读取与分析

```python
import pandas as pd
sheets = pd.read_excel("输入.xlsx", sheet_name=None)
for name, frame in sheets.items():
    print(name, frame.shape, list(frame.columns))
```

表头位置不明时先 `header=None` 看原始行。需要公式、合并单元格、格式和打印设置时改用：

```python
from openpyxl import load_workbook
wb = load_workbook("输入.xlsx", data_only=False)
for ws in wb.worksheets:
    print(ws.title, ws.max_row, ws.max_column, ws.freeze_panes, ws.print_area)
```

`data_only=True` 读取的是最近一次计算缓存，不是公式本身。大表先用
`read_only=True` 或 CSV 分块扫描，避免一次装入全部数据。

`.xls` 老格式先转为 `.xlsx`：

```bash
artifactflow-office convert 输入.xls /workspace/输入.xlsx
```

## 创建与修改

用 openpyxl 写值、公式、样式、列宽、冻结窗格和打印设置。可推导的值写公式，数字保持数字类型，
日期保持日期类型；不要把百分比、日期和金额预格式化成字符串。

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

wb = Workbook()
ws = wb.active
ws.title = "汇总"
ws.append(["区域", "营收", "占比"])
ws.append(["华东", 4200, "=B2/SUM(B$2:B$9)"])
for cell in ws[1]:
    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = PatternFill("solid", fgColor="1F4E78")
ws["B2"].number_format = "#,##0"
ws["C2"].number_format = "0.0%"
ws.freeze_panes = "A2"
ws.sheet_properties.pageSetUpPr.fitToPage = True
ws.page_setup.fitToWidth = 1
ws.page_setup.fitToHeight = 0
wb.save("/workspace/输出-未重算.xlsx")
```

修改现有文件时只改目标单元格并另存。遇到宏、数据透视表、切片器、外部连接或第三方扩展时，
先检查 openpyxl warning；不要静默覆盖原件。`.xlsm` 必须显式使用 `keep_vba=True`，本技能的
`recalc` 仅接受 `.xlsx`，不承诺宏往返。

## 公式重算

先静态检查，再让 Calc 打开、重算并另存到**不同路径**，最后复查：

```bash
python "$SKILL/scripts/check_formulas.py" /workspace/输出-未重算.xlsx
artifactflow-office recalc /workspace/输出-未重算.xlsx /workspace/输出.xlsx
python "$SKILL/scripts/check_formulas.py" /workspace/输出.xlsx
```

`recalc` 会检查保存前后公式数量，输出缓存错误列表；`cached_errors` 非空时修公式后重跑。
大型工作簿的扫描可能返回 `scan_truncated: true`，此时只承诺 best-effort。LibreOffice 与 Excel
在动态数组、LAMBDA、新函数、外部链接和循环计算上可能不一致，关键结果仍需说明兼容性。

## 渲染与交付

渲染前设置打印区域、方向、缩放和重复标题行，否则 Calc 可能把表格切成大量空白页：

```bash
artifactflow-office render /workspace/输出.xlsx /workspace/xlsx-pages
artifactflow-office convert /workspace/输出.xlsx /workspace/输出.pdf
```

逐页检查列是否截断、表头是否重复、数字格式是否正确、公式错误是否可见。当前模型看不到图片时，
按部署能力委派视觉子代理。CSV 交付无需 LibreOffice，使用 pandas 并明确编码。

## 边界

- Excel 专有新函数、宏和复杂数据模型只做 best-effort；不要声称 Calc 重算等同于 Excel。
- 大于约 50 万行的工作簿优先流式处理，避免全量 pandas/openpyxl 内存加载。
- 视觉渲染是打印视图，不等同于用户在 Excel 网格中的全部交互状态。
