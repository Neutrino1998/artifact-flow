---
name: pptx
description: >
  创建与修改 PowerPoint 演示文稿(.pptx),内置六套设计主题与固定版式配方,
  含风格样单(先让用户选风格)与生成后的几何质检。当用户要做 PPT/幻灯片/
  演示材料,或要读取、修改已有 pptx 时激活。工作在沙盒中进行。
license: Apache-2.0
compatibility: 需要沙盒(bash/mount/persist)。镜像已烤 python-pptx、Pillow;无网络,无渲染器。
metadata:
  version: "1.0.0"
---

# 演示文稿(.pptx)

先 `mount_skill` 本技能(得到 `/workspace/.skills/pptx/`,记作 `$SKILL`)。
生成走「选风格 → 写内容 JSON → 脚本出片 → 质检」四步,版式与配色由脚本固化,
不要手排坐标。

包内文件:[build_deck.py](scripts/build_deck.py) ·
[check_geometry.py](scripts/check_geometry.py) ·
[风格样单](assets/design_gallery.html)

## 生成新演示文稿

**第 1 步 · 对齐内容**:先和用户确认大纲(讲什么、给谁看、大约几页)。没有大纲
不要直接出片。内容纪律:
- 一页只讲一个论点,标题就是论点本身(写结论,不写「关于XX的说明」);
- 每页正文 ≤6 个要点、每要点一行为佳;塞不下就拆页或移到备注;
- 数据尽量用图(matplotlib 出 PNG → image 版式),避免数字堆表格墙。

**第 2 步 · 选风格**:把风格样单交给用户挑——

```bash
cp $SKILL/assets/design_gallery.html /workspace/风格样单.html
```

`persist` 它(HTML artifact 会渲染成页面),请用户回复主题名/编号;用户不在意时
按场景自选并说明理由(商务→曜石蓝,品牌→赭墨,政务→绛红,学术→极简…)。

**第 3 步 · 出片**:写 `deck.json` 后一条命令生成——

```bash
python $SKILL/scripts/build_deck.py deck.json 输出.pptx
```

JSON 结构与七种版式(cover/section/bullets/two_col/table/image/closing)见脚本
头部 docstring(`head -40 $SKILL/scripts/build_deck.py`)。典型骨架:cover →
section×N(章节间隔页) → bullets/two_col/table/image(正文) → closing。
自定义配色传 token 对象(主色 primary 大面积用,accent 只点睛)。

**第 4 步 · 质检**:

```bash
python $SKILL/scripts/check_geometry.py 输出.pptx
```

报出的 issues(越界/重叠/超载/占位符/小字号/空页)逐条修——通常是回去改
deck.json(拆页、删字)再重新生成,不是去挪坐标。issues 为空再 `persist` 交付。
这是启发式检查,沙盒里没有渲染器,最终视觉效果以用户在 Office 里打开为准。

## 读取已有 pptx

```python
from pptx import Presentation
for i, slide in enumerate(Presentation("输入.pptx").slides, 1):
    print(f"--- 第{i}页")
    for shape in slide.shapes:
        if shape.has_text_frame:
            print(shape.text_frame.text)
```

页内图片:`shape.image.blob` 写出文件 → `persist` → 委派 vision_agent 识别。

## 修改已有 pptx

保留原设计、只改内容:python-pptx 遍历到目标 run 改 `run.text`(整段替换会丢
run 级格式,能改 run 不改 paragraph)。增删页、改主题这类结构性改动,优先提议
「按原内容重新生成」——pptx 的 XML 手术(版式/母版三层继承)费力易错,收益低。

## 边界

- **沙盒无渲染器**:不能出缩略图/转 PDF/截图验证,质检靠 check_geometry 的
  静态启发式 + 用户实际打开确认。
- 不支持 SmartArt、动画、音视频;pptxgenjs/Node 类方案不可用(无 node)。
- 图表用 matplotlib 生成 PNG 走 image 版式,不生成原生可编辑图表对象。
