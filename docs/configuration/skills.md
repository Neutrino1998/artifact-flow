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

ZIP 中必须能唯一定位一份 `SKILL.md`。Reconcile 会校验路径、成员数量、声明解压大小和正文，然后原样保存 bundle；需要使用 bundle 中的附属文件时，Agent 使用 `mount_skill` 把它显式挂入 Sandbox。

## 激活与 Sandbox

用户在输入框选择 Skill，以及模型自行调用 `read_skill`，是同一个激活语义的两个入口：两者都会向当前 Agent 提供完整 `SKILL.md` 指导、打开该 Skill 声明的可用能力，并根据是否含附属文件给出相同的条件化提示。

激活本身不会启动 Sandbox，也不会自动挂载文件。只有 bundle 含附属文件且当前任务确实需要它们时，Agent 才调用 `mount_skill`，将目录解到 `/workspace/.skills/<slug>/`；Sandbox 按 turn 销毁，后续 turn 如需再次访问，应重新挂载。只有用户明确选择的 Skill 会作为 chip 显示在该条用户消息上；模型自行 `read_skill` 不会被记成用户选择。

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

## 同名与覆盖

共享 Skill 的 slug 在共享目录中唯一；个人 Skill 的 slug 只需在该用户自己的空间内唯一。因此，不同用户可以分别导入同名个人 Skill，个人 Skill 也可以与共享 Skill 同名。

当某个用户的个人 Skill 与一个对其可见的共享 Skill 同名时，运行时优先使用个人 Skill。技能管理页会同时保留两项，并用红色边框标出当前被覆盖的共享 Skill；删除或重命名个人 Skill 后，共享 Skill 自动恢复，无需同步或迁移开关状态。

## Skill 与 Agent 的关系

Skill 不取代 Agent：

- Agent 定义长期角色、模型和能力上限；
- Skill 提供某次任务所需的方法和按需能力；
- 激活 Skill 只能打开 Agent 已知但当前 disabled 的能力，不把任意新工具越权加入 Agent。

用户是否启用、部门可见性和管理员规则存放在数据库，不应回写到 Release 中的 Skill 文件。

## 开发与发布

可编辑源文件放在 `config/skills-src/` 时，应按项目既有构建流程产出 `config/skills/*.zip`；真正参与 reconcile 和 Release 的是 `config/skills/`。

配置完成后按[本地配置工作流](index.md#本地配置工作流)检查并生效。

生产环境由 Release gate 自动 reconcile。管理员和用户通过 UI 导入的 dynamic/private Skill 不属于 seeded 配置，后续 Release reconcile 不会把它当作源码配置覆盖。
