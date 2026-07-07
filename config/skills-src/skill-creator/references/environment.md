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

**系统工具(apt)**:`pandoc`(3.x,md↔docx/html 转换)、`ripgrep`、`zip`、`git`(仅本地
操作)、Noto Sans CJK SC 字体(matplotlib 中文已全局配置,画图直接写中文)。

**Python 包(pip)**:numpy、pandas、matplotlib、Pillow、openpyxl、pypdf、
python-docx、python-pptx、lxml、pdfplumber、pypdfium2(及其传递依赖;
`pip list` 可查全量)。**没有**:requests(无网也无用)、PyYAML、reportlab、
node/npm、LibreOffice、tesseract。

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
- 结构:zip 里恰好一个 SKILL.md(可带一层同名包装目录);成员 ≤2000;
  解压总量 ≤500MB;SKILL.md ≤5MB;禁止 `../`/绝对路径。
- 内容:frontmatter 合法 YAML;剥 frontmatter 后正文非空;``` 围栏配对。
- 单 zip ≤100MB;私有导入计入个人存储配额。
- frontmatter 的 `model`/`effort`/`context`/`paths` 是 CC 扩展,本平台忽略
  (导入时会给 warning);`visibility` 由导入通道决定,写了也忽略。
