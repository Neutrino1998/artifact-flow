---
name: mermaid-to-png
description: >
  将 Mermaid 图在无浏览器沙盒中渲染为 PNG，供用户下载或插入 DOCX/PPTX。
  仅在需要 PNG 文件时激活；聊天展示、Mermaid 源码和 SVG 下载不激活。
license: Apache-2.0
compatibility: 需要沙盒(bash/mount/persist)；镜像已烤 merman-cli 和 Noto Sans CJK SC 字体。
metadata:
  version: "1.0.1"
---

# Mermaid 转 PNG

本技能只负责生成 PNG。聊天中展示图、提供 Mermaid 源码或下载 SVG 时，直接输出 Mermaid
fenced code block，由前端渲染和提供 SVG 下载，不调用 `bash`、`mount` 或 `persist`。

`.mmd` 是 `/workspace` 中的临时渲染输入，不是交付物，不要 `persist`。

## 路线选择

- 下载独立 PNG：临时写入 `.mmd`，渲染后只 `persist` PNG。
- 把图插入 DOCX/PPTX：在父文档工作流中临时生成 `.mmd` 和 PNG，插入后只 `persist`
  最终文档；用户同时明确要求独立 PNG 时才额外持久化 PNG。
- 把已有 `.mmd` 转成 PNG：先 `mount` 源文件，渲染后只 `persist` PNG，不回写源码。
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

- 节点文字含标点、括号或空格时使用 `A["文字"]` 这类引号标签；节点 ID 保持简短 ASCII。
- 不要在 PNG 标签中使用 emoji（如 🚀、📦、🔍）充当图标。镜像支持普通中英文，
  但不提供 emoji 字体；`merman-cli` 的字体回退还可能让 emoji 同行的中文一起变成方块。
  转换已有 Mermaid 时先把装饰性 emoji 换成普通文字，不要把中文整体改成英文。
- 不使用 `--raster-unbounded`、`--suppress-errors` 或在无网沙盒中加载远程 icon pack。解析失败应修正
  源文件，不交付错误占位图。

## 质检

渲染成功不代表布局合格。检查节点文字、连线、方向、截断、重叠和中文字形；过密时先减少
节点文字、分图或改变 `LR`/`TB` 方向。出现方块时先检查并移除 emoji，不要尝试 `apt-get`
或 `fc-cache`——字体已烤入只读、无网镜像，运行时安装或刷新缓存无效。渲染 PNG 后用可用的
视觉能力检查，不要只看退出码。
