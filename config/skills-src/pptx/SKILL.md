---
name: pptx
description: >
  读取、创建、修改和质检 PowerPoint 演示文稿(.pptx/.ppt)，包括复用模板、批量替换、
  页面渲染和导出 PDF。用户提供演示文稿或要求交付 PPT/PDF 时激活。
  工作在无网络沙盒中，编辑用 python-pptx，渲染与转换用 LibreOffice。
license: Apache-2.0
compatibility: 需要沙盒(bash/mount/persist)。镜像已烤 LibreOffice、python-pptx、Pillow、matplotlib、RapidFuzz。
metadata:
  version: "2.1.2"
---

# 演示文稿

先 `mount` 文件并 `mount_skill` 本技能，技能目录记作
`SKILL=/workspace/.skills/pptx`。保留原文件，输出写到 `/workspace`。

包内工具：[inspect_deck.py](scripts/inspect_deck.py)、
[replace_text.py](scripts/replace_text.py)、[build_deck.py](scripts/build_deck.py)、
[check_geometry.py](scripts/check_geometry.py)、[设计方法](references/design.md)、
[叙事结构](references/deck_patterns.md)、[风格样单](assets/design_gallery.html)。

## 标准流程

1. `inspect_deck.py` 读取结构、文本、表格、图片和形状坐标。
2. 选择保守编辑或重新生成；有模板时默认保留原设计。
3. 用 JSON 文件驱动批量操作，不把长文本塞进 shell 参数。
4. 跑静态几何检查。
5. 用 LibreOffice 渲染每页并做视觉检查。

```bash
python "$SKILL/scripts/inspect_deck.py" 输入.pptx > /workspace/deck.json
```

`.ppt` 老格式先转为 `.pptx`：

```bash
artifactflow-office convert 输入.ppt /workspace/输入.pptx
```

## 保留模板编辑

先确认原文和目标页，再创建批量替换文件：

```bash
cat > /workspace/replacements.json <<'JSON'
[
  {"find": "旧标题", "replace": "新标题", "match": "auto", "expect": 1},
  {"find": "旧要点", "replace": "新的较长要点", "match": "auto", "expect": 1}
]
JSON

python "$SKILL/scripts/replace_text.py" 输入.pptx /workspace/修改稿.pptx \
  --map /workspace/replacements.json
```

数组中的单处替换默认按 exact → Unicode/标点归一化 → 有界 fuzzy 定位，并在整个选定页面中
要求唯一；多个候选或找不到锚点都失败。可用 `match: exact|normalized|auto` 收紧策略，
`expect > 1` 只允许 exact。不要用 `--allow-missing` 掩盖计划未命中。输出里的
`paragraph_rewrites` 表示匹配跨 run，格式可能被合并，必须重点查看对应页面。
字段、显式换行和其他非普通文本节点会切断匹配，脚本不会跨过它们替换两侧 run。
源内容超出槽位时优先缩短或拆页，不要靠持续缩小字号硬塞。复杂成组对象、母版和 SmartArt
不要临时拆 XML；保守编辑做不到时，明确采用 best-effort 重建。

## 从零生成

先读[叙事结构](references/deck_patterns.md)确定页面角色，定制视觉时再读
[设计方法](references/design.md)。普通演示可写 `deck.json` 后生成：

```bash
python "$SKILL/scripts/build_deck.py" deck.json /workspace/输出.pptx
```

JSON 支持的版式见脚本头部 docstring。定量数据图用 `dataviz`/matplotlib；流程、架构、时序等
语义图按 `mermaid-to-png` skill 渲染为临时 PNG，再放入 image 版式；默认只持久化最终演示文稿。
`build_deck.py` 是快速生成器，不是模板编辑器；已有模板时不要无故重做。

## 质检与导出

静态检查发现越界、重叠、超载、占位符、小字号和空页：

```bash
python "$SKILL/scripts/check_geometry.py" /workspace/输出.pptx
```

视觉检查使用新目录，逐页查看 PNG：

```bash
artifactflow-office render /workspace/输出.pptx /workspace/pptx-pages
artifactflow-office convert /workspace/输出.pptx /workspace/输出.pdf
```

修正内容后重新生成到新的渲染目录。至少检查文字截断、元素重叠、字体替换、图片缺失和页间一致性。
当前模型看不到图片时，按部署能力委派视觉子代理，不能把静态几何通过等同于视觉通过。

## 边界

- LibreOffice 用于渲染和转换，不用于反复打开并保存模板；往返保存可能改变版式。
- 动画、音视频、SmartArt、复杂原生图表和母版手术只做 best-effort 保留。
- LibreOffice 与 Microsoft PowerPoint 的字体度量可能不同；最终渲染是必要检查，不是绝对保真承诺。
