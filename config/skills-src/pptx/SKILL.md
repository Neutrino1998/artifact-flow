---
name: pptx
description: >
  读取、创建与修改 PowerPoint 演示文稿(.pptx)。当用户要做 PPT/幻灯片/
  演示材料,上传或要求修改已有 pptx,或希望基于模板/旧 deck 复用版式时激活。
  默认优先保留既有设计做内容编辑;无模板时可用内置主题和版式快速生成。
  工作在沙盒中进行。
license: Apache-2.0
compatibility: 需要沙盒(bash/mount/persist)。镜像已烤 python-pptx、Pillow;无网络,无渲染器。
metadata:
  version: "1.0.0"
---

# 演示文稿(.pptx)

先 `mount_skill` 本技能(得到 `/workspace/.skills/pptx/`,记作 `$SKILL`)。
PPTX 任务先分流,不要一律走固定生成器。

包内文件:[inspect_deck.py](scripts/inspect_deck.py) ·
[replace_text.py](scripts/replace_text.py) · [build_deck.py](scripts/build_deck.py) ·
[check_geometry.py](scripts/check_geometry.py) ·
[风格样单](assets/design_gallery.html) · [设计方法论](references/design.md) ·
[叙事结构](references/deck_patterns.md)

## 任务 → 路线

| 任务 | 路线 |
|---|---|
| 读取/总结已有 pptx | `inspect_deck.py` 看结构与文本,图片按需导出后视觉识别 |
| 保留原设计改文字 | `inspect_deck.py` 定位 → `replace_text.py` 做确定替换 → `check_geometry.py` |
| 基于模板/旧 deck 改内容 | 先做 slide mapping,优先只改文字/图片;结构大改前告知 best-effort |
| 从零生成普通 deck | 读 `deck_patterns.md` 定大纲 → 读 `design.md` 定风格 → `build_deck.py` → `check_geometry.py` |
| 高度定制设计 | 读 `design.md`,用 python-pptx 手写布局,仍跑 `check_geometry.py`;必要时承认可视 QA 需要用户打开确认 |

## 读取与分析

```bash
python $SKILL/scripts/inspect_deck.py 输入.pptx > deck.json
```

输出包含每页 layout、文本块、表格样例、图片/图表/placeholder、形状坐标。用它做三件事:

- 判断 deck 是“内容可直接抽取”,还是图片/图表占主体;
- 模板编辑前做 slide mapping:哪页版式适合哪段内容,不要重复同一种重文字页;
- 修改后复查是否有残留 placeholder、空页、过密页面。

页内图片:python-pptx 可从 `shape.image.blob` 写出文件 → `persist` → 视觉识别。识别按你自己的
识图能力分流:`read_artifact` 能看到图像内容就直接读;只拿到占位文本则 `call_subagent`
委派 `vision_agent`(若 `available_subagents` 里没有,说明本部署无图片识别能力,如实告知用户)。

## 修改已有 pptx / 模板

**优先保留原设计、只换内容。**先用 `inspect_deck.py` 找到目标页和原文,然后对确定文本替换:

```bash
python $SKILL/scripts/replace_text.py 输入.pptx 输出.pptx \
    --find "原文片段" --replace "新文本"
```

多处替换写 JSON:

```json
{
  "旧标题": "新标题",
  "旧要点": "新要点"
}
```

```bash
python $SKILL/scripts/replace_text.py 输入.pptx 输出.pptx --map replacements.json
```

`replace_text.py` 找不到锚点会失败;不要用 `--allow-missing` 掩盖目标没命中的问题。若命中跨多个
run,脚本会重写该段并在 JSON 里报告 `paragraph_rewrites`——这通常仍可接受,但要复查该页。

模板适配纪律:

- 先确定每段内容映射到哪页模板;同类页面不要机械重复。
- 大改模板或重排内容前读 [设计方法论](references/design.md),避免把原模板改成连续 bullet。
- 源内容少于模板槽位时,多余图片/形状/文本框不要只清空文字;当前脚本不安全删除成组对象,
  这类结构改动要说明 best-effort,或改走重新生成。
- 源内容长于模板槽位时,优先拆页或精简文案;不要把长段塞进原框。
- 修改后必须跑 `check_geometry.py`。

## 从零生成新演示文稿

无模板或用户接受统一版式时,用内置生成器。先和用户确认大纲(讲什么、给谁看、大约几页);
没有大纲不要直接出片。写大纲前先读 [叙事结构](references/deck_patterns.md),做定制设计前读
[设计方法论](references/design.md)。内容纪律:

- 一页只讲一个论点,标题就是论点本身(写结论,不写「关于XX的说明」);
- 每页正文 ≤6 个要点、每要点一行为佳;塞不下就拆页或移到备注;
- 数据尽量用图(matplotlib 出 PNG → image 版式),避免数字堆表格墙。

把风格样单交给用户挑:

```bash
cp $SKILL/assets/design_gallery.html /workspace/风格样单.html
```

`persist` 它(HTML artifact 会渲染成页面),请用户回复主题名/编号;用户不在意时
按场景自选并说明理由(商务→曜石蓝,品牌→赭墨,政务→绛红,学术→极简…)。

写 `deck.json` 后一条命令生成:

```bash
python $SKILL/scripts/build_deck.py deck.json 输出.pptx
```

JSON 结构与七种版式(cover/section/bullets/two_col/table/image/closing)见脚本
头部 docstring(`head -40 $SKILL/scripts/build_deck.py`)。典型骨架:cover →
section×N(章节间隔页) → bullets/two_col/table/image(正文) → closing。
自定义配色传 token 对象(主色 primary 大面积用,accent 只点睛)。

`build_deck.py` 是快速生成器,不是模板编辑器。用户给了模板/参考 deck 时,除非明确要重做,
优先走“修改已有 pptx / 模板”。

## 质检

每次生成或修改后先跑静态质检:

```bash
python $SKILL/scripts/check_geometry.py 输出.pptx
```

报出的 issues(越界/重叠/超载/占位符/小字号/空页)逐条修。修改模板时通常是缩短文案/
拆页/换槽位;从零生成时通常是回去改 deck.json,不是临时挪坐标。

这是启发式检查,沙盒里没有渲染器,最终视觉效果以用户在 Office 里打开为准。不要声称
“已视觉确认”;只能说“静态检查通过/未发现明显几何问题”。

## 设计纪律

这里仅列最低纪律;详细方法读 [设计方法论](references/design.md)。

- 每页需要一个清晰视觉角色;避免纯标题+长 bullet。
- 配色要贴主题,不要默认蓝;一套 deck 只保留一个主色、一个辅助色、一个少量强调色。
- 同一 deck 里要变化版式,不要连续多页同款 bullet。
- 标题写结论,正文左对齐,文本框留足余量。

## 边界

- **沙盒无渲染器**:不能出缩略图/转 PDF/截图验证,质检靠 check_geometry 的
  静态启发式 + 用户实际打开确认。
- 不支持 SmartArt、动画、音视频;pptxgenjs/Node 类方案不可用(无 node)。
- 图表用 matplotlib 生成 PNG 走 image 版式,不生成原生可编辑图表对象。
- 删除/复制复杂成组对象、母版/版式继承手术属于 best-effort;优先保守编辑或重生成。
