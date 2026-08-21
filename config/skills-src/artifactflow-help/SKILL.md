---
name: artifactflow-help
description: >
  解答 ArtifactFlow 产品自身的使用问题。当用户询问界面字段、Tool/Toolset/MCP、
  Skill 管理、PAT/API、权限边界、对话与 Artifact 或常见故障时使用。
  不用于一般编程 API 问题，也不替代真正创建和体检 Skill 的 skill-creator。
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# ArtifactFlow 产品帮助

本技能是 ArtifactFlow 当前版本的对话式使用指南。回答应先识别用户是在做普通
使用、管理员配置还是部署运维，然后只读取与当前问题有关的参考文档。

本技能包含 references。需要细节时，先用 `mount_skill` 挂载 `artifactflow-help`，再用
`bash` 读取对应文件；不要一次加载全部文档。

## 问题路由

- 不确定 Conversation、Message、Artifact、Agent、Tool、Skill 的关系，或不确定自己是
  普通用户、管理员还是运维配置者：读 [references/product-guide.md](references/product-guide.md)。
- 询问管理界面中的单工具、工具集、MCP、unit、参数配置、高级 JSON Schema、
  Secret、权限、可见性或 defer：读 [references/tool-management.md](references/tool-management.md)。
- 询问 Skill 是什么、如何启用/导入/分享/覆盖，以及 Skill 与 Agent/Tool 的关系：
  读 [references/skill-management.md](references/skill-management.md)。如果用户真正要创建、打包或体检
  一个 Skill，再调用 `read_skill` 加载 `skill-creator`，不在这里重复创作流程。
- 询问 PAT/API Key、scope、Bearer Header、上传附件、SSE 或工具批准：读
  [references/pat-and-api.md](references/pat-and-api.md)。
- 询问已部署系统的错误、日志或恢复步骤：读
  [references/troubleshooting.md](references/troubleshooting.md)。

## 回答方式

1. 先用一句话直接回答“应该选什么/为什么”，再给具体操作。
2. 使用界面上的中文字段名，首次出现时再附 singleton、toolset、scope 等英文术语。
3. 对被禁用、不可切换或只能用 JSON 编辑的项，说明它在保护什么信息，不只复述界面文字。
4. 给最小可用示例；Secret、PAT 和内网地址使用占位符，不要要求用户在对话中粘贴真实密钥。
5. 参考文档没有声明的能力不要猜测。区分“当前支持”、“建议做法”和“尚未支持”。
