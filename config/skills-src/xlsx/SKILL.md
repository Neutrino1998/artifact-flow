---
name: xlsx
description: >
  读取与分析 Excel 工作簿(.xlsx/.csv),以及创建、修改带格式和公式的表格。
  当用户上传表格要分析数据、做汇总透视,或要求生成/修改 Excel 文件时激活。
  工作在沙盒中进行(pandas 分析 + openpyxl 读写)。
license: Apache-2.0
compatibility: 需要沙盒(bash/mount/persist)。镜像已烤 pandas、openpyxl;无网络,不能重算公式。
metadata:
  version: "1.0.0"
---

# 表格(.xlsx / .csv)

分工:**分析用 pandas,读写格式与公式用 openpyxl**。先 `mount` 文件、
`mount_skill` 本技能(`/workspace/.skills/xlsx/`,记作 `$SKILL`)。
包内文件:[check_formulas.py](scripts/check_formulas.py)。

## 读取与分析

```python
import pandas as pd
sheets = pd.read_excel("输入.xlsx", sheet_name=None)   # 全部工作表 → {名: DataFrame}
for name, df in sheets.items():
    print(name, df.shape, list(df.columns))
```

- 表头不在第一行:先 `header=None` 读原样看结构,再定 `header=`/`skiprows=`。
- 要看公式与格式(pandas 只给值):`openpyxl.load_workbook(path)` 后遍历
  `cell.value`(公式串)/`cell.number_format`;`data_only=True` 读的是 Excel
  上次保存的缓存值。
- csv 注意编码:先试 `encoding="utf-8"`,乱码换 `"gbk"`。
- 分析结论交付:小结果直接写在回复里;大结果表 `df.to_excel` 后 `persist`。

## 创建

要求:数字列给数字格式(千分位/百分比/小数位),表头行加底色加粗,冻结表头,
列宽给够。**凡是能由其他单元格推导的值一律写公式**(如合计 `=SUM(B2:B9)`),
不要用 Python 算好硬编码——用户后续改数字时公式会自动更新,硬编码不会。

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
wb = Workbook(); ws = wb.active; ws.title = "汇总"
ws.append(["区域", "营收", "占比"])
for c in ws[1]:
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = PatternFill("solid", fgColor="1B2A4A")
ws.append(["华东", 4200, "=B2/B$5"])
ws["C2"].number_format = "0.0%"
ws["B2"].number_format = "#,##0"
ws.column_dimensions[get_column_letter(1)].width = 14
ws.freeze_panes = "A2"
wb.save("输出.xlsx")
```

## 公式契约(重要)

**本环境没有计算引擎,写入的公式不会被算出值**——值在用户用 Excel/WPS 打开时
自动计算,这是正常交付形态。因此:

- 写完公式跑静态体检(坏引用/不存在的表名/缓存错误值):

```bash
python $SKILL/scripts/check_formulas.py 输出.xlsx
```

- **不要**用 `data_only=True` 或 pandas 去读自己刚写的公式结果——读到的是
  None,不是错误。需要向用户报告计算结果时,用 pandas 平行算一份并说明
  "文件内为公式,打开自动计算"。

## 修改已有文件

```python
from openpyxl import load_workbook
wb = load_workbook("输入.xlsx")   # 保留格式与公式
```

改完 `wb.save("输出.xlsx")`。**注意**:openpyxl 保存会**丢弃图表、图片和透视表**
——目标文件含这些对象时,把改动范围报给用户确认,或只输出新增的工作表/数据
让用户自行粘贴,不要静默覆盖原件。

## 边界

- 不能重算公式(见公式契约);不支持 .xls 老格式(请用户另存为 .xlsx)、宏(.xlsm 的宏会丢)。
- 大表(>50 万行)pandas 直读可能吃满内存:分块 `chunksize`(csv)或先用
  openpyxl `read_only=True` 流式扫描确定范围。
