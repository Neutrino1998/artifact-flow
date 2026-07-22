---
name: dataviz
description: >
  数据可视化与看板图表设计指导。用户要求创建、修改或评审图表、统计卡、KPI 行、
  仪表盘、数据驱动的 HTML/SVG artifact 或 matplotlib 图时激活；用于选择合适图形、确定颜色
  编码、检查可访问性与深浅色，并用沙盒内 Python validator 校验分类/有序调色板。
  适配 ArtifactFlow 静态 HTML artifact 和无网络 sandbox。
license: Apache-2.0
metadata:
  version: "0.1.2"
---

# 数据可视化

先 `mount_skill` 本技能，得到 `/workspace/.skills/dataviz/`，下文记作 `$SKILL`。
不要用 Node/npm；在本平台里用 Python validator。

运行环境：包内 validator 只依赖 Python 3.11 标准库；sandbox 已烤 matplotlib、
pandas、Noto Sans CJK 字体；无 node/npm。HTML artifact 不执行 script。

## 工作流

本技能处理定量数据图。流程图、架构图、时序图、状态图、ER 图需要 PNG 时改用
`mermaid-to-png` skill；仅在回复中展示 Mermaid 时直接输出 fenced code block。

1. **先定图形，不先选颜色。** 判断读者要比较大小、看趋势、识别系列、看目标偏差、
   看构成，还是只需要一个数字。细则见 [chart-selection.md](references/chart-selection.md)。
2. **给颜色分工。** 身份用分类色；大小用单色连续渐变；正负/高低两端用发散色；
   成功/警告/危险用状态色。不要用颜色重复编码已经由长度、位置表达的数值。
3. **分类色和有序色必须跑校验。**

```bash
python3 $SKILL/scripts/validate_palette.py "#2563eb,#d97706,#7c3aed" --mode light
python3 $SKILL/scripts/validate_palette.py "#60a5fa,#f59e0b,#a78bfa" --mode dark --surface "#111827"
```

- 散点、气泡、地图、小多图等任意两色可能相邻时，加 `--pairs all`。
- 有序类别(漏斗阶段、等级、年龄段)用单色阶，加 `--ordinal`。
- `FAIL` 要改颜色；`WARN` 要加直接标签、图例、表格视图或纹理等第二编码。

4. **再做图表结构。** 标题说清指标和范围；一条轴只承载一个尺度；多尺度拆成多个图。
   标记、标签、图例、表格视图和静态 HTML 约束见 [chart-patterns.md](references/chart-patterns.md)。
5. **最后渲染检查。** 看是否有标签碰撞、坐标轴被裁剪、颜色只在浅色可读、表格缺失、
   或移动端横向溢出。validator 只管颜色，不替你检查布局。

## 本平台约束

- HTML artifact 是静态页面：不要依赖 JS tooltip、D3、Plotly、Recharts、CDN 或外部字体。
  用内联 SVG/CSS、直接标签、图例、`<title>`、表格视图承载信息。
- Matplotlib 图表直接支持普通中文；必要时用 `MPLBACKEND=Agg` 的默认无头渲染。镜像不提供
  emoji 字体，不要在标题、轴标签或标注中用 emoji 充当图标，改用普通文字或 matplotlib
  marker/shape。`Glyph ... missing from font(s)` 警告表示成图确有方块缺字，不能忽略后交付；
  emoji 缺字不会连带破坏同行中文。
- 外部 Web 应用代码可以按目标栈实现交互，但 ArtifactFlow artifact 交付必须自包含。

## 默认资源

- [palette.md](references/palette.md)：可直接使用的中性调色板、状态色和 surfaces。
- [chart-selection.md](references/chart-selection.md)：按数据任务选择图形。
- [chart-patterns.md](references/chart-patterns.md)：图表结构、标签、静态 HTML 和可访问性规则。
- [validate_palette.py](scripts/validate_palette.py)：分类/有序调色板校验器。
