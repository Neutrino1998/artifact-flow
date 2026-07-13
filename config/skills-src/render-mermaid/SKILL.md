---
name: render-mermaid
description: >
  将 Mermaid 流程图、架构图、时序图、状态图、ER 图、类图或时间线渲染为 PNG、SVG 或 PDF。
  用户明确要求可下载的渲染文件、需要渲染已有 .mmd，或要把语义图嵌入 DOCX/PPTX/PDF/HTML
  时激活。仅要求聊天内图示或 Mermaid 源码时不要激活，直接返回 mermaid fenced code block。
license: Apache-2.0
compatibility: 需要沙盒(bash/mount/persist)；镜像已烤 merman-cli 和 Noto Sans CJK SC 字体。
metadata:
  version: "0.2.0"
---

# Mermaid 文件渲染

本技能只负责把 Mermaid 渲染成文件。仅在回复中画图或交付 Mermaid 源码时，直接输出
Mermaid fenced code block，不调用 `bash`、`mount` 或 `persist`。

## 路线选择

- 单独交付 PNG/SVG/PDF：写入 `.mmd`，用 `merman-cli` 渲染，再 `persist` 用户要求的文件。
- 渲染或修改已有 `.mmd` artifact：先 `mount`，渲染后 `persist` 用户要求的源码或输出文件。
- 嵌入 DOCX/PPTX/PDF/HTML：在父文档工作流中把 `.mmd` 和渲染结果作为 `/workspace`
  中间文件，插入后只 `persist` 最终文档；用户另行要求源码或图片时才单独持久化。
- 柱线饼、散点、热力、分布等定量数据图：用 `dataviz` skill + matplotlib，不用
  Mermaid 的 `xychart`。
- 自由排版的信息图或插画不适合 Mermaid；改用文档/演示文稿自身的形状与图像流程。

## 创建与渲染

长文本和中文标签用**单引号 heredoc**写入 `.mmd` 文件，不放进 shell 参数：

```bash
cat > /workspace/system-flow.mmd <<'MMD'
flowchart LR
  A["提交任务"] --> B{"校验通过？"}
  B -->|"是"| C["执行"]
  B -->|"否"| D["返回修改"]
MMD

merman-cli -i /workspace/system-flow.mmd \
  -o /workspace/system-flow.png --raster-fit-width 1600 -b white
```

- Office 嵌入优先 PNG；静态 HTML 优先 SVG；需要单独交付可直接输出 PDF。
- 节点文字含标点、括号或空格时使用 `A["文字"]` 这类引号标签；节点 ID 保持简短 ASCII。
- 直接修改 `.mmd` 并重新渲染，不编辑已生成的 SVG XML。需要后续可编辑或用户明确要求源码时，
  同时 `persist` 源 `.mmd` 和渲染结果。
- 不使用 `--raster-unbounded`、`--suppress-errors` 或在无网沙盒中加载远程 icon pack。解析失败应修正
  源文件，不交付错误占位图。

## 质检

渲染成功不代表布局合格。检查节点文字、连线、方向、截断、重叠和中文字形；过密时先减少
节点文字、分图或改变 `LR`/`TB` 方向。渲染 PNG 后用可用的视觉能力检查，不要只看退出码。
