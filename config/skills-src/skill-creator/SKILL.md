---
name: skill-creator
description: >
  创建新技能与体检已导入技能。当用户想把一套重复流程沉淀成技能包、要求
  "帮我写一个 skill",或导入的技能不工作/想在使用前验证能否运行时激活。
  创建流:访谈→沙盒实写实测→打包→交付导入;体检流:挂载目标技能→真实
  运行依赖与脚本→给出能跑/缺什么的报告。
license: Apache-2.0
compatibility: 需要沙盒(bash/mount/persist)。体检与实测在真实运行环境中进行。
metadata:
  version: "1.0.0"
---

# 技能创建与体检

一个技能 = 一个 zip:根部 `SKILL.md`(YAML frontmatter + 正文指引),可选
`scripts/`(固化操作)、`references/`(按需查阅的深料)、`assets/`(模板/数据)、
`wheels/`(离线依赖)。环境事实(烤了什么、缺什么、硬门槛)见
[references/environment.md](references/environment.md) —— 两条流程都以它为准。
打包工具:[package_skill.py](scripts/package_skill.py)。

## 创建流

**1 · 访谈定范围。**在动手前问清:解决什么任务?什么话该触发它?输入输出长
什么样?一个技能只做一类事——"处理合同"太宽,"合同关键条款抽取与风险标注"
刚好。

**2 · 写 SKILL.md。**
- `description` 是唯一常驻模型上下文的部分,决定"会不会被想起来用":写
  **什么时候用 + 能做什么**,一到三句,含用户会说出口的触发词。
- 正文写"怎么做":步骤化、给可直接运行的命令/代码块、写明边界与降级
  (不能做的事直说)。正文保持一屏到三屏;深料拆进 `references/`,正文只留
  链接——按需加载,不占常驻上下文。
- 重复、脆弱、可验证、参数面稳定的固定操作**固化成脚本**放 `scripts/`
  (带 docstring 用法),正文只写一行调用命令。判断型/创意型/探索型流程留在
  正文或 `references/`;不要为了"脚本化"把一次性的取舍硬编码。

**3 · 沙盒实测(必做)。**技能的脚本就在本沙盒同款环境运行——写完直接跑:
依赖 `python -c "import 包"` 逐个验;脚本用典型输入真跑一遍;需要烤入集外的
依赖,按 environment.md 的 wheels 约定备齐并实测 `--no-index` 安装。**没实测
过的技能不交付。**

**4 · 打包交付。**

```bash
python /workspace/.skills/skill-creator/scripts/package_skill.py 技能目录/
```

预检通过后 `persist` 生成的 zip,告诉用户:下载 → 前端「技能管理 → 导入技能」
上传。导入器还会跑一轮硬校验,有 findings 时逐条回来修——warning 可放行,
error 必须清零。

## 体检流(检查已导入的技能)

用户已把技能导入平台,怀疑或想确认它能不能跑:

1. `mount_skill` 目标技能 → 读它的 SKILL.md,列出它声称的能力与依赖。
2. **依赖实测**:对每个 `import` 的包跑 `python -c "import 包"`;缺的先看
   bundle 有没有 `wheels/` 能离线装,没有则记为缺口。
3. **脚本实测**:每个脚本先 `--help`/无参跑看用法,再构造最小典型输入真跑
   一条主路径。只读不改:别对用户数据做破坏性操作。
4. **环境适配扫描**:对照 environment.md 找移植残留——CC 工具词表(Edit/
   TodoWrite/WebFetch…)、网络调用、`soffice`/node 依赖、`claude` CLI 调用。
5. **交报告**,按严重度分层:
   - ✅ 可用:实测通过的能力;
   - ⚠️ 降级可用:哪条路径缺什么、有无 wheels/改写可补;
   - ❌ 不可用:硬依赖本环境不存在(如需要联网/LibreOffice),给替代建议。
   每条结论都注明实测证据(跑了什么命令、什么输出),不做纸面推断。

## 常见坑

- description 写成功能清单而没有触发场景 → 模型永远想不起来用它。
- 正文塞满背景知识 → 拆 `references/`,正文只留操作。
- 脚本 hardcode 绝对路径/依赖当前目录 → 用参数与 `Path(__file__)` 相对定位。
- 从 CC 生态搬运时保留了 `model`/`context` frontmatter 或 CC 工具名 → 平台
  忽略/不存在,按 environment.md 词表改写。
