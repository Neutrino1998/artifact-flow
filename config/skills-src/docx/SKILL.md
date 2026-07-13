---
name: docx
description: >
  读取、创建、修改和审阅 Word 文档(.docx/.doc)，包括保留现有版式编辑、批量修订、
  批注、接受或拒绝修订、渲染质检及导出 PDF。需要处理 Word 文件或交付 docx/PDF 时激活。
  工作在无网络沙盒中，优先使用稳定脚本，OOXML 仅作最后手段。
license: Apache-2.0
compatibility: 需要沙盒(bash/mount/persist)。镜像已烤 LibreOffice、Pandoc、python-docx、lxml、RapidFuzz。
metadata:
  version: "2.1.1"
---

# Word 文档

先 `mount` 输入文件并 `mount_skill` 本技能，技能目录记作
`SKILL=/workspace/.skills/docx`。所有产物写到 `/workspace`，原文件只读保留。

包内工具：[apply_redline.py](scripts/apply_redline.py)、
[accept_changes.py](scripts/accept_changes.py)、[add_comment.py](scripts/add_comment.py)、
[check_redlines.py](scripts/check_redlines.py)、[unpack.py](scripts/unpack.py)、
[pack.py](scripts/pack.py)、[修订标记参考](references/redlines.md)、
[默认 reference.docx](references/reference.docx)。

## 路线选择

| 需求 | 首选路线 |
|---|---|
| 读取、总结、抽取修订 | Pandoc 输出 Markdown；图片另行抽取 |
| 新建普通文档 | Markdown + Pandoc reference docx |
| 保留既有版式做普通小改 | python-docx，修改最少对象后另存 |
| 以修订模式做多处修改 | `apply_redline.py --plan` |
| 接受/拒绝修订、加批注 | 对应技能脚本 |
| `.doc` 老格式 | 先用 `artifactflow-office convert` 转 `.docx` |
| 导出 PDF、视觉质检 | `artifactflow-office convert/render` |

不要把 docx 往返转换成 Markdown 再写回原件；那是重建，不是保版式编辑。
LibreOffice 在本技能中主要负责兼容格式、渲染和 PDF 导出，不作为默认编辑器。

## 读取

保留修订信息读取：

```bash
pandoc --track-changes=all --extract-media=/workspace/docx-media \
  输入.docx -t gfm -o /workspace/document.md
```

若只需结构化检查表格、段落和样式，用 python-docx。文档以图片、文本框或图表为主时，
直接渲染页面：

```bash
artifactflow-office render 输入.docx /workspace/docx-pages
```

逐页检查 PNG；当前模型看不到图片时，按部署能力委派视觉子代理。不要仅凭文本抽取宣称版式正确。

## 创建

普通报告先写 Markdown，再使用用户模板或包内 reference docx：

```bash
pandoc content.md \
  --reference-doc="$SKILL/references/reference.docx" \
  -o /workspace/输出.docx
```

复杂表格、分节、题注或精确图片尺寸用 python-docx。Pandoc 适合语义内容和统一样式，
python-docx 适合逐元素控制；不要一开始就拆 OOXML。

## 批量修订

长文本、引号、换行和中文标点不要放进 shell 参数。用**单引号 heredoc 写文件**，再只传短路径。
heredoc 负责安全落盘，脚本负责解析和校验：

```bash
cat > /workspace/old.txt <<'TEXT'
需要替换的长文本，包含 "$变量"、反引号和引号也不会被 shell 展开。
TEXT

cat > /workspace/new.txt <<'TEXT'
修订后的长文本。
TEXT

cat > /workspace/redline-plan.json <<'JSON'
{
  "author": "审阅",
  "changes": [
    {
      "op": "replace",
      "find_file": "old.txt",
      "replace_file": "new.txt",
      "match": "auto",
      "expect": 1
    },
    {
      "op": "delete",
      "find": "应删除的短语",
      "expect": 1
    },
    {
      "op": "insert_after",
      "find": "插入锚点",
      "text": "新增内容",
      "expect": 1
    }
  ]
}
JSON

python "$SKILL/scripts/apply_redline.py" 输入.docx /workspace/修订稿.docx \
  --plan /workspace/redline-plan.json
```

plan 中相对文件路径以 plan 所在目录为基准；UTF-8 文本文件会去掉一个 heredoc 末尾换行。
每项默认 `expect: 1`，并按 exact → Unicode/标点归一化 → 有界 fuzzy 定位；多个候选一律失败。
可用 `match: exact|normalized|auto` 收紧策略，`expect > 1` 只允许 exact。命中数不符或任一项失败
时不写输出。单处操作也可用
`--replace-file/--with-file`、`--delete-file`、`--insert-after-file/--text-file`。

脚本只编辑 `word/document.xml` 中单段落的普通直接 run，不处理跨段、页眉页脚、文本框、
超链接、字段或已有修订内部；这些不可编辑节点会切断匹配，绝不拼接两侧 run。超出边界时先缩小
需求或用 python-docx；确实必须保留复杂结构时，
才使用 `unpack.py`/`pack.py` 并阅读[修订标记参考](references/redlines.md)。不要在转换失败后
临时手搓 OOXML。

修订后必须对照原件检查无静默正文改写：

```bash
python "$SKILL/scripts/check_redlines.py" 输入.docx /workspace/修订稿.docx --author 审阅
```

## 修订处置与批注

```bash
python "$SKILL/scripts/accept_changes.py" 输入.docx /workspace/接受稿.docx --accept
python "$SKILL/scripts/accept_changes.py" 输入.docx /workspace/拒绝张三稿.docx --reject --author 张三
python "$SKILL/scripts/add_comment.py" 输入.docx /workspace/批注稿.docx \
  --anchor "被批注的原文" --text-file /workspace/comment.txt --author 审阅
```

`accept_changes.py` 输出中的 `skipped` 非空时必须如实报告。批注锚点须在单段落内。

## 转换与质检

```bash
artifactflow-office convert 输入.doc /workspace/输入.docx
artifactflow-office convert /workspace/输出.docx /workspace/输出.pdf
artifactflow-office render /workspace/输出.docx /workspace/docx-final-pages
```

最终至少检查：页数是否异常、标题/表格是否被截断、图片是否缺失、分页是否漂移。复杂版式经
LibreOffice 渲染与 Microsoft Word 仍可能有差异，交付时说明这是 best-effort 兼容结果。

## 边界

- Pandoc 重建会损失多栏、文本框、艺术字等复杂版式；保版式修改不能走往返转换。
- python-docx/LibreOffice 对宏、OLE、SmartArt 和第三方扩展部件只做 best-effort 保留。
- OOXML 解包只用于明确、可验证的结构手术；失败后回到原件，不把半成品当成果。
