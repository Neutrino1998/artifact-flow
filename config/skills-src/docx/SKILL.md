---
name: docx
description: >
  读取、创建、修改和审阅 Word 文档(.docx/.doc)，包括保留现有版式编辑、批量修订、
  批注、接受或拒绝修订、渲染质检及导出 PDF。需要处理 Word 文件或交付 docx/PDF 时激活。
  工作在无网络沙盒中，优先使用稳定脚本，OOXML 仅作最后手段。
license: Apache-2.0
compatibility: 需要沙盒(bash/mount/persist)。镜像已烤 LibreOffice、Pandoc、python-docx、lxml、Pillow、RapidFuzz。
metadata:
  version: "2.5.0"
---

# Word 文档

先 `mount` 输入文件并 `mount_skill` 本技能，技能目录记作
`SKILL=/workspace/.skills/docx`。所有产物写到 `/workspace`，原文件只读保留。

包内工具：[apply_redline.py](scripts/apply_redline.py)、
[accept_changes.py](scripts/accept_changes.py)、[add_comment.py](scripts/add_comment.py)、
[check_redlines.py](scripts/check_redlines.py)、[unpack.py](scripts/unpack.py)、
[pack.py](scripts/pack.py)、[decompose_docx.py](scripts/decompose_docx.py)、
[修订标记参考](references/redlines.md)、
[默认 reference.docx](references/reference.docx)。

## 路线选择

| 需求 | 首选路线 |
|---|---|
| 读取、总结、抽取修订 | `decompose_docx.py` 拆出正文、可见图片和表格索引 |
| 新建普通文档 | Markdown + Pandoc reference docx |
| 保留既有版式做普通小改 | python-docx，修改最少对象后另存 |
| 以修订模式做多处修改 | `apply_redline.py --plan` |
| 接受/拒绝修订、加批注 | 对应技能脚本 |
| `.doc` 老格式 | 先用 `artifactflow-office convert` 转 `.docx` |
| 导出 PDF、视觉质检 | `artifactflow-office convert/render` |

不要把 docx 往返转换成 Markdown 再写回原件；那是重建，不是保版式编辑。
LibreOffice 在本技能中主要负责兼容格式、渲染和 PDF 导出，不作为默认编辑器。

## 读取

常规读取统一先做一次语义拆解：

```bash
python "$SKILL/scripts/decompose_docx.py" 输入.docx /workspace/docx-read
```

脚本用 Pandoc 保留修订并生成 `document.md`，同时输出 `manifest.json` 和逐出现位置的
`figures/`。`document.md` 是唯一内容来源，包含标题、段落、列表和表格；manifest 只是图片/表格的
轻量目录，不重复保存正文或单元格文本。先读 `document.md`；需要定位图表时再查 manifest 中的
合成 ID、标题、标题层级、前后段落和顺序，不要把整个目录或全部图片一次性塞回上下文。

图片按“在文档中的一次出现”记录，而不是按媒体文件去重。同一原图若被不同裁剪，会得到不同的
figure。只把与任务有关、`include_in_current_view: true` 且存在 `visible_path` 的图片持久化并交给
视觉能力：普通位图是原始可见内容，矩形 `a:srcRect` 裁剪会先物化成裁剪后的 PNG，
EMF/WMF/SVG 会 best-effort 转 PNG。
脚本不会输出未经显示变换的原始媒体；`source_part` 只是 DOCX 包内定位元数据，不能代替
`visible_path`，否则可能读到文档中被裁掉的内容。`document.md` 中 Pandoc 生成的 `media/...`
引用同样只是内容位置提示，不是可读取产物；实际图片只认 manifest 的 `visible_path`。正文关系可达
的页眉、页脚、脚注和尾注图片也会进入 manifest；未知内容 part 中的图片只标记 fallback，不猜其
显示语义。图片的 `revision_state`
会区分 current/inserted/moved_to 与 deleted/moved_from；后两类只留目录记录，不物化图片，也不能当作
当前文档内容送去识图。

表格内容只从 `document.md` 阅读。manifest 的表格项来自 Pandoc 已解析的内容树，只提供
`table-###` 合成 ID、可获得的题注/源 ID、文档顺序和标题层级，方便引用与定位；合成 ID 只在本次
拆解结果内有效，不是 Word 原生 ID，也不保证跨版本稳定。脚本不另建表格 JSON，不声称还原物理
行列、合并、嵌套、内容控件或修订语义。若任务依赖 Pandoc 无法表达的精确表格结构或可见版式，
再定位并渲染必要页面，按 best-effort 结果说明限制；不要用目录数量证明 Word 中所有表格均已识别。

普通文字文档即使含有插图，也不要因此渲染全文。manifest 中 `fallback: page_required` 表示脚本
不能可靠恢复当前出现位置的可见像素，例如旋转/翻转、非矩形裁剪、图片效果、VML、SmartArt 或
OLE；此时才定位并渲染必要的物理页。先转 PDF 并按正文/标题线索确定候选页，再显式传页码：

```bash
artifactflow-office convert 输入.docx /workspace/输入.pdf
artifactflow-office render /workspace/输入.pdf /workspace/docx-pages --pages 5,8
```

把 `5,8` 替换为已定位的物理页；它不一定等于页脚页码或章节号。无法一次精确定位时，只渲染小的
候选范围并依据结果缩小/扩展，不省略 `--pages`。跳过 logo、小图标和装饰图。若视觉结果与预期
不符，重新核对 manifest 的 fallback 原因和页码，不要把同一文件换 ID 重试。

视觉验证采用风险驱动的最小范围：读取、总结和抽取默认不做版式 QA；简单局部编辑先做结构检查，
能可靠定位时只查看受影响或高风险页面；新建或大改默认抽查首页、末页及表格/图片密集页。只有用户
明确要求版式审校、打印就绪或高保真交付，发生全局字体/模板/页尺寸等版式变更，
或用户已反馈视觉问题时，才做完整逐页检查。未做完整检查时按 best-effort 交付，不宣称已逐页验证或
版式完全正确。

## 创建

普通报告先写 Markdown，再使用用户模板或包内 reference docx：

```bash
pandoc content.md \
  --reference-doc="$SKILL/references/reference.docx" \
  -o /workspace/输出.docx
```

复杂表格、分节、题注或精确图片尺寸用 python-docx。Pandoc 适合语义内容和统一样式，
python-docx 适合逐元素控制；不要一开始就拆 OOXML。
流程、架构、时序等语义图按 `mermaid-to-png` skill 渲染为临时 PNG 后插入；默认只持久化最终
文档。定量数据图用 `dataviz`/matplotlib。

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
artifactflow-office render /workspace/输出.docx /workspace/docx-final-pages --pages 1
```

触发视觉验证时，把示例中的 `1` 替换或扩展为按上述风险范围选出的物理页，检查页数是否异常、
标题/表格是否被截断、图片是否缺失、分页是否漂移。复杂版式经 LibreOffice 渲染与 Microsoft
Word 仍可能有差异，交付时说明这是 best-effort 兼容结果。

## 边界

- Pandoc 重建会损失多栏、文本框、艺术字等复杂版式；保版式修改不能走往返转换。
- `decompose_docx.py` 不是 Word 排版引擎，不推断元素所在物理页；不确定显示语义会明确要求页面 fallback。
- python-docx/LibreOffice 对宏、OLE、SmartArt 和第三方扩展部件只做 best-effort 保留。
- OOXML 解包只用于明确、可验证的结构手术；失败后回到原件，不把半成品当成果。
