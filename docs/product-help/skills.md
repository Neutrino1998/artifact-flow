# Skill 使用与管理

Skill 是按需加载的工作方法，可以包含指导、参考资料、脚本或模板；它不是一个 HTTP
操作，也不会取代 Agent。

## 普通用户

- 在“技能管理”中启用或停用自己可见的 Skill。
- 导入的个人 Skill 只有自己可见，并立即启用。
- 在输入框主动选择 Skill，或由 Model 根据问题调用 `read_skill`，都会加载同一份指导。
- Skill 附带的文件只在任务确实需要时按需加载，不会因为启用就全部进入上下文。

## 管理员

- 可以导入共享 Skill，并选择 `public` / `department` 可见性和是否默认启用。
- `public` 与 `department` 决定谁可以使用；部门例外规则在“部门授权”中管理。
- Seeded Skill 是只读内容，界面只能展示和使用，不能在线修改正文。
- 用户仍可按自己的需要启用或停用可见的共享 Skill。

## 同名与覆盖

个人 Skill 可以与共享 Skill 同名。对该用户而言，个人版本优先；界面会标出被覆盖的
共享版本。删除或重命名个人版本后，共享版本自动恢复。

## Skill、Agent 与 Tool

- Agent 定义长期角色、Model 和能力上限。
- Skill 提供当前任务的方法，并可以按需打开 Agent 已知但默认关闭的 Tool unit。
- Skill 不能把 Agent 完全不知道的 Tool 越权加入当前能力集。
- Tool unit 的部门可见性、Agent 成员关系和成员执行确认仍分别生效。

## 创建或检查 Skill

真正创建、打包或检查一个 Skill 时，直接在对话中要求“帮我创建 Skill”或“检查这个
Skill 能不能运行”。预装 `skill-creator` 会处理范围确认、实测和打包；产品字段、
导入规则、Tool/PAT 与权限问题继续由 `artifactflow-help` 解答。

不要在对话中粘贴真实 Token、工具凭证或只应由特定人员读取的内部资料。

返回[产品使用指南](index.md)。
