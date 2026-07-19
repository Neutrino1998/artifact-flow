# Skill 配置

Skill 是按需加载的工作方法。它可以只有一份 `SKILL.md`，也可以携带脚本、参考资料和模板；激活时还可以临时授予相关 Tool unit。

## 纯文本 Skill

目录只能包含一份 `SKILL.md`：

```text
config/skills/report-writing/
└── SKILL.md
```

```markdown
---
name: report-writing
description: Use when preparing an executive report.
visibility: public
default_enabled: true
allowed-tools:
  - internal_search
---

# Executive report workflow

Start with the decision, separate evidence from inference, and finish with
owners and next actions.
```

## Bundle Skill

只要 Skill 还有其他文件，就把完整目录打成 `config/skills/<slug>.zip`，不要把解压目录直接放进 `config/skills/`：

```text
report-kit.zip
├── SKILL.md
├── references/style-guide.md
└── templates/report.docx
```

ZIP 中必须能唯一定位一份 `SKILL.md`。Reconcile 会校验路径、成员数量、声明解压大小和正文，然后原样保存 bundle；需要处理附件时，Agent 使用 `mount_skill` 把它显式挂入 Sandbox。

## Frontmatter

| 字段 | 默认 | 说明 |
|---|---|---|
| `name` | slug | 展示名称 |
| `description` | `""` | 告诉模型什么时候应激活 |
| `visibility` | `public` | Seeded Skill 可用 `public` / `department`；用户上传可为 `private` |
| `default_enabled` | `true` | 是否默认出现在用户可启用集合中 |
| `allowed-tools` | `[]` | 激活 Skill 时临时打开的 Builtin 或 external Tool unit |
| `compatibility` | — | Sandbox 依赖等兼容性声明，按原始 JSON/YAML 保存 |

`allowed-tools` 以 unit 为粒度。可以写 Builtin 名、external unit 名或成员完整名；成员完整名也会归一化到所属 unit。未知名称会告警并保留，等未来对应 unit 出现后再解析。

Seeded Skill 没有用户 owner，因此不能设置 `visibility: private`。正文不能为空。

## Skill 与 Agent 的关系

Skill 不取代 Agent：

- Agent 定义长期角色、模型和能力上限；
- Skill 提供某次任务所需的方法和按需能力；
- 激活 Skill 只能打开 Agent 已知但当前 disabled 的能力，不把任意新工具越权加入 Agent。

用户是否启用、部门可见性和管理员规则存放在数据库，不应回写到 Release 中的 Skill 文件。

## 开发与发布

可编辑源文件放在 `config/skills-src/` 时，应按项目既有构建流程产出 `config/skills/*.zip`；真正参与 reconcile 和 Release 的是 `config/skills/`。

配置完成后执行：

```bash
python scripts/reconcile_config.py --dry-run
```

生产环境由 Release gate 自动 reconcile。管理员和用户通过 UI 导入的 dynamic/private Skill 不属于 seeded 配置，后续 Release reconcile 不会把它当作源码配置覆盖。
