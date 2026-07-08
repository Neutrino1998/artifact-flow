---
name: docx
description: >
  读取、创建、编辑 Word 文档(.docx),含审阅能力:读出/产生/接受/拒绝修订
  (track changes)与批注。当用户上传 docx 要提取内容,或要求生成 Word 文档、
  按要求改文档、处理修订与批注时激活。工作在沙盒中进行(mount 文档 →
  bash 处理 → persist 结果),扫描件与文档内图片做视觉识别(自己能识图就直接读,
  否则委派视觉子代理)。
license: Apache-2.0
compatibility: 需要沙盒(bash/mount/persist)。镜像已烤 pandoc 3.x、python-docx、lxml;无网络。
metadata:
  version: "1.0.0"
---

# Word 文档(.docx)

所有操作在沙盒里完成。先 `mount` 目标文档、`mount_skill` 本技能
(得到 `/workspace/.skills/docx/`,下文记作 `$SKILL`),产出文件用 `persist` 存回。

包内文件:[unpack.py](scripts/unpack.py) · [pack.py](scripts/pack.py) ·
[accept_changes.py](scripts/accept_changes.py) · [add_comment.py](scripts/add_comment.py) ·
[apply_redline.py](scripts/apply_redline.py) · [check_redlines.py](scripts/check_redlines.py) ·
[默认样式参考文档](references/reference.docx) · [修订手术参考](references/redlines.md)

## 任务 → 路线

| 任务 | 路线 |
|---|---|
| 读内容/提取文本 | pandoc 转 Markdown(§读取) |
| 读审阅稿(要看到修订痕迹) | pandoc `--track-changes=all`(§读取) |
| 新建文档 | Markdown 写好 → pandoc + 默认 reference.docx 转 docx;复杂版式/精细结构用 python-docx(§创建) |
| 常规修改(不留痕) | 小改 python-docx;结构性改动 unpack→改 XML→pack(§编辑) |
| 以修订方式修改(留痕) | 简单同段落替换/插入/删除用 apply_redline.py;复杂结构再手写 XML→必跑校验(§修订) |
| 接受/拒绝修订、加批注 | 现成脚本,一条命令(§修订) |
| 文档带图/扫描件 | 提取图片 → persist → 视觉识别(§图片) |

## 读取

```bash
pandoc 文档.docx -t gfm -o out.md            # 纯文本视角(修订按已接受呈现)
pandoc 文档.docx -t gfm --extract-media=media -o out.md   # 同时抽出全部图片到 media/
```

审阅稿(需要看到谁改了什么):

```bash
pandoc 文档.docx --track-changes=all -t markdown -o out.md
```

输出里插入/删除/批注是带 `insertion`/`deletion`/`comment-start` class 的
span,含作者与时间,例如 `[新增文字]{.insertion author="张三" date="..."}`。
只想要"改前"或"改后"版本,把 `all` 换成 `reject` 或 `accept`。

大文档先转出 Markdown 再按需分段读,不要整篇读回对话。

## 创建

首选 Markdown → pandoc + 随技能分发的默认参考文档(标题/列表/表格/图片都支持)。不要裸跑
`pandoc content.md -o 输出.docx`,默认样式以 `$SKILL/references/reference.docx` 为准:

```bash
pandoc content.md --reference-doc=$SKILL/references/reference.docx -o 输出.docx
```

默认 reference.docx 必须保持为瘦身模板:只放样式、编号、页边距、页眉页脚等
reference-doc 需要的结构,不要放示例正文、隐藏图片或其他可被复制进输出的媒体资产。

用户有公司模板、品牌样式或明确格式要求时,优先请用户提供自己的 reference docx,
再用它生成。reference-doc 适合控制正文字体字号、标题样式、页边距、列表与表格基础样式:

```bash
pandoc content.md --reference-doc=用户提供的reference.docx -o 输出.docx
```

需要从零制作或调整 reference docx 时:

```bash
pandoc --print-default-data-file reference.docx > ref.docx
# 用 python-docx 或 unpack/pack 修改 ref.docx 的 styles.xml 后:
pandoc content.md --reference-doc=ref.docx -o 输出.docx
```

逐元素精细构建(复杂表格、题注、分节)用 python-docx。只有当 reference-doc 和
python-docx 都表达不了、或必须保留既有 Word 复杂版式时,才升级到 unpack 后手写
OOXML;Word 手术是最后手段:

```python
from docx import Document
from docx.shared import Pt, Cm
doc = Document()
doc.add_heading("标题", level=1)
doc.add_paragraph("正文…")
t = doc.add_table(rows=2, cols=3); t.style = "Table Grid"
doc.add_picture("chart.png", width=Cm(14))
doc.save("输出.docx")
```

## 编辑(不留痕的常规修改)

- 内容小改(替换文字、加段落、改表格单元格):python-docx 直接读改存。
- 结构性/样式级改动(python-docx 没有暴露的部分):XML 手术三步——

```bash
python $SKILL/scripts/unpack.py 文档.docx work/     # 解开并断行,XML 可读可编辑
# 编辑 work/word/document.xml(或 styles.xml/headerN.xml…)
python $SKILL/scripts/pack.py work/ 输出.docx       # 回包,自带 XML+结构双重检查
```

XML 里定位内容:`grep -n "关键词" work/word/document.xml`。改动原则:动最小的
元素;文本都在 `<w:t>` 里;格式在相邻的 `<w:rPr>`(run 级)/`<w:pPr>`(段级)。

## 修订与批注(审阅模式)

**处置已有修订**(纯 Python,不需要 Word):

```bash
python $SKILL/scripts/accept_changes.py 输入.docx 输出.docx --accept            # 接受全部
python $SKILL/scripts/accept_changes.py 输入.docx 输出.docx --reject --author 张三   # 按作者拒绝
```

末行输出 JSON(处理计数 + 未处理项点名);`skipped` 非空时告知用户哪些改动需要
在 Word 里手工处置。

**以修订方式修改文档**(用户要"留痕"/"用修订模式改"):

简单同段落替换/删除/插入优先用脚本生成 `w:ins`/`w:del`:

```bash
python $SKILL/scripts/apply_redline.py 输入.docx 修改后.docx \
    --replace "旧文本" --with "新文本" --author 审阅
python $SKILL/scripts/apply_redline.py 输入.docx 修改后.docx \
    --delete "要删除的文本" --author 审阅
python $SKILL/scripts/apply_redline.py 输入.docx 修改后.docx \
    --insert-after "锚点文本" --text "新增文本" --author 审阅
```

`apply_redline.py` 只处理正文 document.xml 中一个段落内的普通文本 run。遇到跨段落、
页眉页脚、脚注、文本框、超链接、已有修订里的再修订、整段删除/移动等复杂情况,再
unpack 后按 [references/redlines.md](references/redlines.md) 的标记规则手写
`w:ins`/`w:del`(全部修订统一一个作者名)。

无论脚本还是手写,最后都**必须跑完整性校验**:

```bash
python $SKILL/scripts/check_redlines.py 原始.docx 修改后.docx --author 审阅
```

FAIL 即说明有正文被静默改写——回去修标记,不要跳过。

**加批注**:

```bash
python $SKILL/scripts/add_comment.py 输入.docx 输出.docx \
    --anchor "被批注的原文片段" --text "批注内容" --author 审阅
```

锚点须落在单个段落内;生成的是基础批注(无回复串,回复需求写进批注文本)。

## 图片与扫描件

链路:

1. 抽图:读取时 `--extract-media=media`,或 unpack 后拿 `work/word/media/*`。
2. `persist` 图片文件为 artifact。
3. 按你自己的识图能力分流:
   - **你自己能识图**——`read_artifact` 该图能看到图像内容 → 直接读、转写。
   - **看不到图**——只拿到占位文本(你的模型不支持识图)→ `call_subagent` 委派
     `vision_agent`,给出 artifact id + 具体问题(转写文字/描述图表/读表格);若
     `available_subagents` 里没有 `vision_agent`,说明本部署无图片识别能力,如实告知用户。

写入图片:python-docx `add_picture`(见§创建);向既有文档插图优先也走
python-docx,手写 OOXML 插图涉及 rels+Content_Types 样板,费力易错。

## 边界

- **不支持 docx→PDF**(需要 LibreOffice,镜像未装)——用户要 PDF 时说明并交付 docx。
- **不支持 .doc 老格式**——请用户先在 Word 里另存为 .docx。
- 复杂修订叠加(移动+格式+嵌套)的自动接受可能有 `skipped` 残留,如实转告。
- pandoc 路线是"重建式"转换,复杂版式(多栏、文本框、艺术字)会有保真损失;
  保版式的修改务必走 python-docx/XML 手术,不要 docx→md→docx 往返。
