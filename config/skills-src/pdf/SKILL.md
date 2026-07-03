---
name: pdf
description: >
  读取 PDF:提取文本与表格、拆分合并页面;扫描件/纯图 PDF 渲染成图片后交给
  vision_agent 识别。当用户上传 PDF 要读内容、抽表格、拆合文档时激活。
  工作在沙盒中进行。
license: Apache-2.0
compatibility: 需要沙盒(bash/mount/persist)。镜像已烤 pypdf、pdfplumber、pypdfium2;无网络,无 OCR 引擎。
metadata:
  version: "1.0.0"
---

# PDF

先 `mount` 文件、`mount_skill` 本技能(`/workspace/.skills/pdf/`,记作 `$SKILL`)。
包内文件:[pdf_to_images.py](scripts/pdf_to_images.py)。
第一步永远是**判断文本层**:有文本层走 pdfplumber,没有(扫描件)走渲染+vision_agent。

## 判断与提取文本

```python
import pdfplumber
with pdfplumber.open("输入.pdf") as pdf:
    print("pages:", len(pdf.pages))
    for i, page in enumerate(pdf.pages[:3], 1):
        text = page.extract_text() or ""
        print(f"--- p{i} ({len(text)} chars)\n{text[:500]}")
```

- 前几页普遍只有零星字符甚至为空 → 扫描件/纯图 PDF,走下面的§扫描件。
- 大文档逐页提取写进文件再按需读,不要整本读回对话。

## 表格

```python
with pdfplumber.open("输入.pdf") as pdf:
    for tbl in pdf.pages[6].extract_tables():
        import pandas as pd
        df = pd.DataFrame(tbl[1:], columns=tbl[0])
```

线框不规整时表格会拆错行列——抽完抽查几行对原文,不对就调
`table_settings`(`{"vertical_strategy": "text"}` 应对无框线表),仍不行按
§扫描件把该页渲染成图交 vision_agent 读。

## 扫描件 / 纯图 PDF(无 OCR,走视觉子代理)

本环境没有 OCR 引擎,识别靠 vision_agent:

```bash
python $SKILL/scripts/pdf_to_images.py 输入.pdf pages/ --pages 1-5 --dpi 150
```

然后逐张 `persist` 需要识别的 `pages/page_*.png` 为 artifact,`call_subagent`
委派 vision_agent(给 artifact id + 具体问题:转写全文/读某个表/找某个字段)。
长文档分批(每批 ≤5 页)委派,拿回结果再继续,不要一次全转。

## 拆分 / 合并 / 旋转

```python
from pypdf import PdfReader, PdfWriter
w = PdfWriter()
for p in PdfReader("输入.pdf").pages[0:10]:
    w.add_page(p)                      # 拆:取 1-10 页
w.append("另一份.pdf")                  # 合:整份追加
with open("输出.pdf", "wb") as f:
    w.write(f)
```

加密文件:`PdfReader(path)` 抛 `FileNotDecryptedError` 时向用户要密码,
`reader.decrypt(密码)` 后照常操作。

## 边界

- **不生成 PDF**(镜像无 reportlab/LaTeX)——用户要"生成 PDF 报告"时,产出
  docx(可用 Word 另存 PDF)或 HTML artifact(浏览器可打印为 PDF),并说明。
- **不做 OCR**:识别质量取决于 vision_agent;手写体/低分辨率扫描的结果要
  标注不确定性。
- PDF 内嵌图片的批量抽取:渲染整页(pdf_to_images.py)通常够用;确需原图时用
  `page.images`(pdfplumber)定位后截取。
