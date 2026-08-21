---
name: artifactflow-help
description: >
  解答 ArtifactFlow 产品自身的使用问题。当用户询问界面字段、Tool/Toolset/MCP、
  Skill 管理、PAT/API、权限边界、对话与 Artifact 或常见使用问题时使用。
  不用于一般编程 API 问题，也不替代真正创建和体检 Skill 的 skill-creator。
license: Apache-2.0
metadata:
  version: "1.0.1"
---

# ArtifactFlow 产品帮助

本技能是 ArtifactFlow 当前版本的公开产品使用指南。回答应先识别用户是在做普通
使用、管理员界面配置还是 PAT/API 调用，然后只读取与当前问题有关的参考文档。

部署与内部运维问题不属于本技能。遇到这类问题时说明应联系部署运维，不要从包内
资料推断或补充内部实现。

本技能包含 references。需要细节时，先用 `mount_skill` 挂载 `artifactflow-help`，再用
`bash` 读取对应文件；不要一次加载全部文档。

## 问题路由

- 不确定 Conversation、Message、Artifact、Agent、Tool、Skill 的关系，或不确定自己是
  普通用户、管理员还是 API 调用者：读
  [references/product-help/index.md](references/product-help/index.md)。
- 询问管理界面中的单工具、工具集、MCP、unit、参数配置、高级 JSON Schema、
  凭证、权限、可见性或 defer：读
  [references/product-help/tools.md](references/product-help/tools.md)。
- 询问 Skill 是什么、如何启用/导入/分享/覆盖，以及 Skill 与 Agent/Tool 的关系：
  读 [references/product-help/skills.md](references/product-help/skills.md)。如果用户真正要创建、
  打包或体检一个 Skill，再调用 `read_skill` 加载 `skill-creator`，不在这里重复创作流程。
- 询问 PAT/API Key、scope、Bearer Header、上传附件、SSE 或工具批准：读
  [references/product-help/pat-api.md](references/product-help/pat-api.md)。
- 询问 Skill/Tool/PAT 为什么不可用、Schema 为什么不能切换或 SSE 如何重连：读
  [references/product-help/troubleshooting.md](references/product-help/troubleshooting.md)。

## 回答方式

1. 先用一句话直接回答“应该选什么/为什么”，再给具体操作。
2. 使用界面上的中文字段名，首次出现时再附 singleton、toolset、scope 等英文术语。
3. 对被禁用、不可切换或只能用 JSON 编辑的项，说明它在保护什么信息，不只复述界面文字。
4. 给最小可用示例；Secret、PAT 和内网地址使用占位符，不要要求用户在对话中粘贴真实密钥。
5. 参考文档没有声明的能力不要猜测。区分“当前支持”、“建议做法”和“尚未支持”。
