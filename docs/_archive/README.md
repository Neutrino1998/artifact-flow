# 内部工程文档

本目录保存不进入项目 Wiki 的内部设计、实施计划、评审、迁移和事故材料。`_archive` 表示“不发布”，不表示内容必然过时；每份计划的当前状态以文档开头和进度章节为准。

## 目录

- [`design/implementation-plan-template.md`](design/implementation-plan-template.md)：跨 session、分阶段实施计划的模板。
- [`design/plans/`](design/plans/)：只有一份主文档的实施计划。
- [`design/artifact/`](design/artifact/)：Artifact 生命周期与工具结果挂载设计。
- [`design/execution-target/`](design/execution-target/)：Execution Target、Workspace 与 Client 执行形态的产品假设和研究路线。
- [`design/native-tool-call/`](design/native-tool-call/)：Native tool call 迁移计划、cutover runbook 和探针报告。
- [`design/sandbox/`](design/sandbox/)：沙盒实施计划与运行时选型评估。
- [`design/skill-system/`](design/skill-system/)：Skill 系统主计划及阶段细化设计。
- [`design/legacy/`](design/legacy/)：已经被当前实现替代、但仍有历史参考价值的架构快照。
- [`migration/`](migration/)：迁移方案、交接记录和文档重写计划。
- [`ops/`](ops/)：事故复盘、修复计划和部署环境确认材料。
- [`reviews/`](reviews/)：阶段性高可用与安全评审记录。
- [`research/`](research/)：外部系统调研和工程工作流探索。

## 维护约定

- 按主题归档，不按 `active` / `completed` 分目录；状态变化只更新文档本身，避免目录与正文漂移。
- 一个主题只有一份主计划时放入 `design/plans/`；出现配套设计、报告或 runbook 后，为该主题建立独立目录。
- `design/legacy/` 中的内容是历史快照，不应作为当前实现契约；当前契约应写入活动 Wiki、`AGENTS.md` 或源码附近。
- 活动文档不能只引用本目录来说明关键行为。内部材料形成稳定结论后，应把必要的理由和约束同步到活动文档。
- 临时 TODO 和已知问题清单不放在这里长期维护；仍需执行的工作应进入明确的实施计划或项目跟踪系统。
