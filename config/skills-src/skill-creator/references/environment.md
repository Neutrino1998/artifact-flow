# 运行环境事实单(活文档)

技能的脚本都在这个环境里执行。**镜像每次变更须同步更新本页**(与
`sandbox/Dockerfile`、`sandbox/requirements.txt` 同 PR)。

## 沙盒

- Python 3.11(python:3.11-slim 基底),非 root(uid 1000)。
- **完全无网络**(`--network=none`):任何 `pip install`(不带 `--no-index`)、
  `requests`、`git clone` 都会失败。这是设计,不是故障。
- `/workspace` 在一个回合内跨 bash 调用持续;回合结束即回收——要保留的产物
  必须 `persist` 成 artifact。
- 技能 bundle 挂载在 `/workspace/.skills/<slug>/`(只读姿态使用)。

## 已烤入镜像

**系统工具(apt)**:`libreoffice-core`、`libreoffice-writer`、`libreoffice-calc`、
`libreoffice-impress`(统一用 `artifactflow-office` 做 Office 转换/渲染/重算)、
`fonts-liberation2`、`fonts-crosextra-carlito`、`fonts-crosextra-caladea`、
`pandoc`(md↔docx/html 转换)、`ripgrep`、`zip`、`unrar-free`(常见未加密 RAR
的 best-effort 解压,不保证密码包/少见特性)、`git`(仅本地操作);另有
Noto Sans CJK SC 字体(matplotlib 中文已全局配置,画图直接写中文)。镜像另提供
`text-edit`，用于从 old/new UTF-8 文件执行有命中数校验的多行替换；源码和配置只用 exact。
另有无浏览器的 `merman-cli`，按 `mermaid-to-png` skill 将 Mermaid 渲染为 PNG。

**Python 包(pip)**:numpy、pandas、matplotlib、Pillow、openpyxl、pypdf、RapidFuzz、
python-docx、python-pptx、lxml、pdfplumber、pypdfium2(及其传递依赖;
`pip list` 可查全量)。**没有**:requests(无网也无用)、PyYAML、reportlab、
node/npm、tesseract。

## 依赖超出烤入集怎么办

技能 zip 里带 `wheels/` 目录(纯离线安装源),SKILL.md 里写明:

```bash
pip install --no-index --find-links /workspace/.skills/<slug>/wheels 包名
```

wheel 必须匹配 linux + cp311(纯 Python 的 `py3-none-any` 最稳);在有网环境
用 `pip download 包名 --dest wheels/` 备齐**含传递依赖**的全套。

## 平台工具词表

技能 frontmatter 的 `allowed-tools` 只能引用本平台注册的工具。当前内置:
`create_artifact` / `update_artifact` / `rewrite_artifact` / `read_artifact` /
`grep_artifact` / `bash` / `mount` / `persist` / `read_skill` / `mount_skill` /
`call_subagent` / `web_search` / `web_fetch`(外网部署才可用)。管理员配置的
外部 API 工具以其注册名为准(前端「工具管理」可查)。

**不存在**这些 Claude Code 工具:Edit、Write、Read、Glob、Grep、TodoWrite、
WebFetch、Task —— 从 CC 生态移植的 skill 里出现这些词表时要改写或删除。

## 导入硬门槛(平台侧自动执行)

- slug:`name` 槽化后须匹配 `^[a-z0-9][a-z0-9_-]{0,63}$`。
- 结构:zip 里恰好一个 SKILL.md;推荐放在同名包装目录 `<name>/SKILL.md`
  (直接放 zip 根部也兼容),附属文件必须与它在同一技能目录内。成员 ≤2000;
  解压总量 ≤500MB;SKILL.md ≤5MB;禁止 `../`、绝对路径与符号链接。
- 内容:SKILL.md 是 UTF-8,frontmatter 是合法 YAML mapping,剥 frontmatter 后正文
  非空。`name`/`description` 按标准骨架填写;未闭合代码围栏等启发式检查只 warning,
  不阻断导入。
- 私有 skill 的单 zip 默认 ≤200MB(部署可调整),并计入个人存储配额。
- frontmatter 的 `model`/`effort`/`context`/`paths` 是 CC 扩展,本平台忽略
  (导入时会给 warning);`visibility` 由导入通道决定,写了也忽略。
