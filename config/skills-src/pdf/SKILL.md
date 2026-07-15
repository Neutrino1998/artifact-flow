---
name: pdf
description: >
  读取、生成、渲染和处理 PDF：提取文本与表格、识别扫描页、拆分合并旋转，或从
  DOCX/PPTX/XLSX 导出并质检 PDF。用户提供 PDF 或要求交付 PDF 时激活。
  读取或检索长 PDF 前必须先读本技能的流式处理规则。工作在无网络沙盒中，
  PDF 处理用 pdfplumber/pypdf/PDFium，Office 导出用 LibreOffice。
license: Apache-2.0
compatibility: 需要沙盒(bash/mount/persist)。镜像已烤 LibreOffice、pdfplumber、pypdf、pypdfium2；无 OCR 引擎。
metadata:
  version: "2.1.0"
---

# PDF

先 `mount` 文件并 `mount_skill` 本技能，技能目录记作
`SKILL=/workspace/.skills/pdf`。共享渲染入口是 `artifactflow-office render`；包内
[pdf_to_images.py](scripts/pdf_to_images.py) 仅保留为兼容旧调用的 PDFium 包装器。

## 长文档的资源约束

PDF 的磁盘大小是压缩后字节数，不代表解析时的内存。上百页或页内对象复杂的
PDF 必须流式处理：

- 禁止把全文累积到 `pages_text = []`、`text += ...`、全量 DataFrame/图像列表中。
  每次只保留当前页和有上限的命中结果；需要全文时逐页追加写入文件。
- `... | head -500` 只限制工具输出，不限制上游 Python 在首次输出前的内存，
  不能当作内存保护。
- `pdfplumber` 页会缓存解析对象。每页必须在 `finally` 中调用 `page.close()`，
  并在找齐目标后立即停止。
- 不在 bash 结果里打印全文。把提取结果写到 `/workspace`，再用 `rg -n -C`
  只读需要的上下文。全文处理仍较重时，按页区间分成多个独立 Python 进程。

多目标定位优先边提取边搜索，命中后只输出有上限的上下文：

```python
import pdfplumber

pending = {value.casefold(): value for value in ["TargetTableA", "TargetTableB"]}
with pdfplumber.open("输入.pdf") as pdf:
    for page_number, page in enumerate(pdf.pages, 1):
        try:
            text = page.extract_text() or ""
            folded = text.casefold()
            for key, label in list(pending.items()):
                position = folded.find(key)
                if position >= 0:
                    print(f"===== {label}: page {page_number} =====")
                    print(text[max(0, position - 800):position + len(label) + 1600])
                    pending.pop(key)
        finally:
            page.close()
        if not pending:
            break
print("not found:", list(pending.values()))
```

确实需要全文时才逐页落盘：

```python
import pdfplumber

with pdfplumber.open("输入.pdf") as pdf, open(
    "/workspace/pdf-text.txt", "w", encoding="utf-8"
) as output:
    for page_number, page in enumerate(pdf.pages, 1):
        try:
            text = page.extract_text() or ""
            output.write(f"\n\n===== page {page_number} =====\n{text}")
        finally:
            page.close()
```

```bash
rg -n -C 20 'TargetTableA|TargetTableB' /workspace/pdf-text.txt | head -500
```

## 读取分流

先判断文本层：

```python
import pdfplumber
with pdfplumber.open("输入.pdf") as pdf:
    print("pages", len(pdf.pages))
    for index, page in enumerate(pdf.pages[:3], 1):
        try:
            text = page.extract_text() or ""
            print(index, len(text), text[:500])
        finally:
            page.close()
```

- 多数页面有连续文本：按上述有界流式逐页提取到文件，再按需读取。
- 只有零星字符或为空：按扫描件处理，渲染需要的页。
- 表格先用 `extract_tables()`，抽取后必须和原页抽查；无框线表可调整 `table_settings`。

## 渲染与扫描件

```bash
artifactflow-office render 输入.pdf /workspace/pdf-pages --pages 1-5
```

页码是 1-based，输出稳定为 `page-1.png` 等。长文档分批处理，不一次把整本图片送入上下文。
当前模型能看图就直接识别；看不到图片时按部署能力委派视觉子代理。环境没有 OCR 引擎，
手写体、低分辨率和复杂表格结果需标注不确定性。

## 拆分、合并与旋转

```python
from pypdf import PdfReader, PdfWriter

writer = PdfWriter()
for page in PdfReader("输入.pdf").pages[:10]:
    writer.add_page(page)
writer.append("另一份.pdf")
with open("/workspace/输出.pdf", "wb") as handle:
    writer.write(handle)
```

加密文件需用户提供密码后调用 `reader.decrypt()`。页面操作后检查页数、页面尺寸和旋转值，
再渲染首尾页及发生修改的页面。

## 生成 PDF

普通报告先按 DOCX skill 生成可编辑 `.docx`，再导出 PDF；演示或表格同理：

```bash
artifactflow-office convert /workspace/报告.docx /workspace/报告.pdf
artifactflow-office render /workspace/报告.pdf /workspace/report-pages
```

转换后用 pypdf 检查可打开和页数，用 pdfplumber 抽查文本层，再逐页或抽样检查 PNG。
不要只看到命令退出码为 0 就宣称成功。

## 边界

- LibreOffice 转 PDF 是常用格式的 best-effort 路线；复杂字体、透明度、SmartArt、宏和嵌入对象可能变化。
- 不做真正 OCR；视觉识别结果不是可搜索文本层。需要可搜索 PDF 时应明确说明当前环境能力不足。
- PDF 表格抽取不是版面理解保证；行列错位时回到渲染页核对。
