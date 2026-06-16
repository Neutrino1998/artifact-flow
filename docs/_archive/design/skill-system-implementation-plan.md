# Skill 系统 + 工具渐进式披露 —— 实施计划

> 状态:规划完成,实现未启动
> 起草:2026-06-16 · 最后更新:2026-06-16
> 前序产物:
> - `sandbox-implementation-plan.md`(本目录)—— 沙盒主线(A/B/C/D 全完成);本 plan 的执行底座,skill 脚本/asset 全部跑在沙盒里。原则 7「依赖三层离线投递」直接被本 plan 的依赖模型继承。
> - `tool-result-artifact-mount.md`(本目录)—— 工具结果溢出转 artifact 的先例(`source` 字段 / 自动命名兜底),本 plan A 阶段是它的「具名一等通道」升级。
> - memory:`tool-ecosystem-positioning`(工具生态定位,两大引擎缺口)、`skill-standard-adoption-direction`(沙盒先→标准 Agent Skills、XML 非障碍、market 离线)、`skill-system-research`(Phase 9 早期研究)。
> 调研基线(2026-06-16,本 plan 起草前做):
> - **参考实现** `custom-claude-code/build-output`:确证 ① Skill 与工具披露是**两套独立机制**(`SkillTool` 零引用 deferral);② 工具披露 = deferred-tools + `ToolSearch`(按名注入 system-reminder,`select:`/关键词加载,`tool_reference` 块由 API 端展开);③ MCP 工具一律 deferred、`mcp__server__tool` 命名,走同一 `ToolSearch`;④ 统一 registry = 归一化到一个 `Tool` 形 + 合并函数,provider 区别仅留 `isMcp`/`source` flag。
> - **开放标准** agentskills.io 已多厂商(Cursor/Gemini/Codex/Copilot):`SKILL.md` 文件夹=单元,frontmatter **恰 6 字段**(`name`/`description`/`license`/`compatibility`/`metadata`/`allowed-tools`),`version` 归 `metadata`,绝大多数真实 skill 只用 `name`+`description`(触发逻辑塞进 description)。
> - **生态弱点 = 依赖与离线**:社区默认运行时 `pip install`(撞 `ModuleNotFoundError` 反应式联网装),对气隙网敌对 —— 本系统**领先于标准**:`compatibility` 声明 + 预置环境 + 自带 wheel + ZIP-of-folder 是更稳的路。validator 工具成熟(`skills-ref` 官方 / `skill-validator` 社区:孤儿文件/未闭合 fence/token 预算/链接解析),无 trust 层 = 导入门禁是我方责任。
> - **用户存货** `utils/claude-skills/anthropic-skills`(仅供参考,预装前先改):8 个 skill,5 脚本型(docx/pptx/xlsx/pdf/skill-creator)+ 3 纯 prose;docx/pptx/xlsx **三份重复** `scripts/office/` + ~40 `.xsd`;依赖全靠预置环境(lxml/openpyxl/pypdf/pandas/Pillow + libreoffice/poppler/pandoc/qpdf + node),无 manifest;无 `$ARGUMENTS`/`${SKILL_DIR}` 替换;frontmatter 实际只 `name`/`description`(+`license`)。schedule/setup-cowork 驱动 CC 产品 widget = **不可移植**,真实目标存货 = 4 文档 skill(+ skill-creator 改造)。

## 本文档定位

这是一份 **plan,不是详细设计**。讲清每个阶段做什么、为什么、什么算完成;**落实细节(schema 字段、具体改哪些代码)留到开工那个阶段再敲定**。同时是**跨 session 跟踪文档**:新 session 先读「进度」一节知道做到哪、下一步从哪续。每推进一阶段,更新状态 + 「变更日志」追加结论;方向有变也记日志。

本 plan 同时覆盖两件用户坚持**一起设计**的事:**skill 系统** 与 **工具渐进式披露**。一起设计的理由是「30-endpoint 平台怎么渐进式披露」这个问题,答案决定了 skill 与工具的职责边界(见原则 1)。

## 进度

- **当前**:**规划完成,未开工。** 沙盒主线(底座)已收官。架构五个分叉已全部收口(三 scope / 披露归工具层 / 依赖三层 / 导入门禁二分 / 预装 pandoc-first),细节留各阶段开工敲定。
- **下一步**:**A 阶段(工具结果→富格式 artifact)** —— 引擎前置、独立可做、对沙盒数据入境有独立价值(memory `tool-ecosystem-positioning` 缺口①)。但注意:skill MVP(文档 skill 跑在用户上传上)**不阻塞于 A**(上传已是 artifact、mount 已通);只有「数据工具→artifact→沙盒」类场景(DB→CSV→分析)才硬依赖 A。排序仍 A 先,因为它最小、最独立、且是后续富数据流入的脊柱第一环。
- **分支策略(待定,倾向 main)**:与沙盒 plan 不同 —— 沙盒走 `feat/sandbox` 不增量合 main 是因为有「半迁移态(md→Word 过渡)漏到生产」的风险。本 plan **无此类破坏性中间态**,A/B 是纯加法引擎特性,倾向**逐阶段合 main、再 overlay intranet**(遵 `feedback-branch-strategy`)。开工首阶段再定。

| 阶段 | 内容 | 状态 |
|---|---|---|
| A | 工具结果→富格式 artifact(`create_from_upload` 的第三调用方) | 未开始 |
| B | 工具渐进式披露(tool-set 文件格式 + `search_tools` 内建工具;MCP 适配缝) | 未开始 |
| C | Skill 核心(三 scope 存储 + L1/L2 注入 + skill 元工具 + 权限/上下文覆盖) | 未开始 |
| D | Skill bundle 执行(L3 挂载进沙盒 + `compatibility` 依赖三层 + 离线 wheel) | 未开始 |
| E | 导入门禁与预装(确定性 validator + 监督式 adapter skill + pandoc-first 预装) | 未开始 |

依赖:D 依赖 A(富数据流入)+ C(skill 存储/激活)+ 沙盒底座。C 依赖 B 吗?**不**——披露与 skill 正交(原则 1),C 可在 B 前后任意序;但 B 解决的 30-endpoint 场景是 skill 编排散文的常见消费者,故排在 C 前。E 是 last step(用户:存货预装前先改)。A、B 各自独立可先做。

## 目标与范围

给系统增加 **标准 Agent Skills**(场景级 prompt 修饰器 + 沙盒可执行 bundle)与 **工具渐进式披露**(让 30-endpoint 平台不再 30 份描述常驻上下文),让社区 skill 尽可能「拿来就用」、内网气隙下离线分发。

**Non-goals(本期明确不做)**:
- **联网 skill registry / market**(skills.sh/skild.sh 等)—— 气隙网不可达,market 降维成 `scope=marketplace` + 链接动作,全 DB/离线(见决策 1)。
- **`context:fork` ad-hoc 子 agent** —— 我方 subagent 是预定义 agent 非 ad-hoc fork,且「只 lead 需要 skill」;v0 标为不支持(skill-creator 那类 fork 存货改造时降级)。
- **substitution 全集**(`$ARGUMENTS`/`` !`cmd` ``/`${SESSION_ID}`)—— 存货零使用,v0 可只支持 `$ARGUMENTS` 子集或全不做。
- **`paths:` 条件 skill**(touch 文件 glob 激活)—— 我方无文件系统语义(artifact 是句柄非路径),v0 不做。
- **server 端 `tool_reference` beta** —— 我方跑任意 backend(qwen via litellm)+ 自有 XML 工具格式,披露走**纯 prompt 级模拟**(见 B)。
- **MCP client 接入** —— 暂不做,但 B 阶段的 tool-set/provider 抽象须留缝,使将来「MCP server = 又一个 deferred tool-set provider」是加法非重写(memory `tool-ecosystem-positioning` 第1点)。
- **skill 版本解析 / per-skill venv** —— 依赖只加不 re-pin(继承沙盒原则 7 护栏)。

## 贯穿原则

1. **渐进式披露归工具层,不归 skill —— 两者正交。** (参考实现实证:CC `SkillTool` 零引用 deferral 机制。)披露**机制** = tool-set(分组/披露单元)+ `search_tools`(发现工具);skill = **场景覆盖层**(注入 body 散文 + 权限覆盖),可**引用** tool-set 但不拥有披露。MCP 将来 = 又一个 deferred tool-set **provider**,进同一 registry、走同一 `search_tools`。把「30-endpoint 披露」这个职责钉死在工具层,skill 保持纯覆盖,是本 plan 的架构脊柱。
2. **Skill = 受信 prompt 修饰器 + 不可信 bundle 的二分,执行全归沙盒。** body/frontmatter 是**受信文本**(注入上下文、改权限);scripts/assets 是**不可信代码/数据**(只在 `--network=none` 沙盒里跑,绝不在 backend 执行)。skill bundle 是新的一类不可信输入,沿用沙盒不可信纪律(选品/审核门禁 = E 阶段)。这也是「先落地沙盒才做 skill」的根本原因 —— 沙盒是 skill 执行的底座。
3. **标准对齐优先于自造;body 原样搬,绝不静默改写。** 采 agentskills.io 开放标准(6 字段、文件夹=单元)。三块各自处理:**body = 自然语言、格式无关**,模型读完用 system prompt 教的 XML 格式发起调用,原样搬;**frontmatter = 映射层**(`allowed-tools`→权限模型、`model`→模型路由,其余 v0 多不支持);**substitution = 选择性实现**。社区 skill 的工具词表耦合(`Read`/`Grep`/`Edit` 等)在沙盒里多自然消解(= `cat`/`grep`/`sed`),残留硬耦合靠 **import lint 标记 + 人审 adapter 改,绝不静默 rewrite**(脆且错改比标记更坏)。
4. **依赖 ≠ 数据,沿用沙盒原则 7 的三层离线投递。** artifact 是用户拥有的**数据**(mount-in/persist、blob 进 DB);依赖是**执行环境**(① 镜像烤通用栈 / ② 离线 wheel bundle 固定位 / ③ skill 自带 asset)。②③同一套 `pip install --no-index --find-links` 机制、不同生命周期(常驻 vs 随 skill),别造两套。**护栏**:skill bundle 只做加法、不 re-pin 基础栈版本(否则一 turn 多 skill 版本冲突逼出版本解析机器,合「fix 复杂度超 feature 价值即退回 scope」)。标准的 `compatibility` 字段 = 声明层,导入时据此校验「需要的镜像没有且 asset 没带」→ 标记/拒。
5. **三态存储,config-种子与 DB-可变二分。** skill 不是 artifact(artifact session-scoped;skill 用户/系统-scoped 跨所有会话)。`preinstalled` = `config/skills/` 启动种子进 DB(运行时不可变,仿 agent/tool「config 即真相、运行时只读」哲学);`marketplace`/`private` = DB 拥有(可变,admin/user 经 UI 改)。skill 非 session-scoped,preinstalled bundle 一份共享(非每会话复制)。
6. **离线 ZIP-of-folder 是唯一分发形态。** 社区四种分发(git clone / plugin install / CLI 包管 / ZIP 上传)里,只有 ZIP-of-folder 不假设 registry 连接,且是 Claude.ai/API/open-skills 共同接受的事实格式 —— 选它做导入导出单元。联网 CLI 全出局。**依赖才是气隙真地雷,非分发**:文件夹随 ZIP 走没问题,运行时 `pip install` 联网约定破在离线 —— 故原则 4 的预置/自带是硬要求。
7. **资源/预算上限一律大声失败,阈值是隐藏常量。** L1 skill 索引预算(仿 CC ≈1% 上下文)、bundle 大小、token 预算超限即 loud-fail + 后果写进给模型的提示;不做模型可调参数(合「Minimize tool parameter surface」)。

## 已锁定的决策

1. **三 scope + 链接表。** 一张 `Skill` 表带 `scope` 枚举:`private`(用户传、owner+admin 可见)/ `preinstalled`(admin 拥有、自动可见+自动链接全用户、`config/skills/` 种子)/ `marketplace`(admin 拥有、目录可见但**不**自动链接、用户显式 link)。加 `user_skill_links(user_id, skill_id, enabled)` 链接表承载:marketplace 的显式链接 + preinstalled 的 per-user 启用开关。**market = `scope=marketplace` + link 动作,全 DB/离线**,无联网 registry。
2. **披露 = tool-set 文件格式 + `search_tools` 内建工具,纯 prompt 级。** 新增「一文件多 tool」的 tool-set 格式(多 endpoint 平台、未来 openapi 生成脚本的落点),整组标 deferred:索引行常驻 `<available_tools>`、schema 不渲染;模型调 `search_tools`(`select:Name,Name` 或关键词)拿回完整 XML 工具描述作 tool_result。我方控 `ContextManager.build()`,故**追踪「已发现工具名」**(仿 CC `extractDiscoveredToolNames`)让其后续轮持续渲染 —— 比 CC 还干净(无需 server `tool_reference` 配合)。
3. **`Skill` 表 + bundle blob,复用 blob 存储。** body(L2 文本)入表;references/scripts/assets(L3)打 zip 进 bundle blob(复用 A 阶段/现有 blob 基础设施);`preinstalled` 同 bundle 全用户共享一份(非 session-scoped)。
4. **三级渐进式披露。** L1 = `<available_skills>`(name + description,每 lead turn 常驻、预算约束)/ L2 = 激活时注入 body(`skill` 元工具,body 作 system-reminder 注入,仿 CC「body=隐藏 user message、tool_result 仅『Launching skill X』」)/ L3 = 激活时把 bundle **挂进沙盒**已知路径(body 引 `${SKILL_DIR}/scripts/...`,模型 bash 按需跑)。
5. **bundle 激活 auto-mount(非模型驱动);persist 仍模型驱动。** body 假设脚本已就位,模型不该先 mount 自己的 skill;故激活即挂 bundle。结果回写仍走沙盒 `persist`→artifact(现成)。
6. **依赖 = `compatibility` 声明 + 镜像通用栈 + asset 离线 wheel。** 通用重栈烤镜像(python + lxml/openpyxl/pypdf/pdfplumber/pandas/Pillow + libreoffice/poppler/pandoc/qpdf,存货与调研同指一组);长尾走 asset `wheels/` + 激活时 `pip install --no-index --find-links` 离线装;import validator 据 `compatibility` 交叉校验(镜像栈 ∪ 自带 wheel),缺口标记/拒。
7. **导入 = 确定性 validator(每次)+ 监督式 adapter skill(admin 策展,last step)。** validator = plain code 阻塞门禁,跑每次导入含 user 私有上传(借 `skills-ref`/`skill-validator` 规则:6 字段 schema、name↔dir、孤儿文件、未闭合 fence、链接解析、token/bundle 大小、`compatibility`-vs-环境);adapter skill = 懂本系统约束的 agent,admin 策展时提改造建议(含 rewrite,因有人审)—— 「lint 不静默改写」在此:运行时禁静默 rewrite,人审 adapter 提 diff 是对的。
8. **预装 pandoc-first(瘦 bundle)。** 存货 4 文档 skill 三份重复 `office/` + 40 xsd 做 OOXML 手术;常见 Word 路径(读/转/简单生成)预装 skill **首选 pandoc/libreoffice**(镜像内,一条 CLI),OOXML 拆解只留给 pandoc 真做不到的(改痕/批注/精确版式)。删常见路径的三份 `office/` bundle。这正是 adapter skill 的策展动作。
9. **采开放标准 6 字段,v0 最小。** `name`/`description`/`license`/`compatibility`/`metadata`/`allowed-tools`;`version` 归 `metadata`;v0 实现只硬依赖 `name`+`description`(存货现状),其余按需。`allowed-tools` 标准里本就 Experimental → 映射到我方 per-tool 权限模型、不欠生态硬兼容。

## 阶段

### A — 工具结果→富格式 artifact(引擎前置,沙盒数据入境的受信通路)

**做什么**:让 backend 工具(受信/有网/有凭证,如 HTTP 工具 GET 回 CSV)能把结果存成**具名、带 content_type、可二进制**的 artifact —— 成为 `create_from_upload` 的**第三调用方**(前两个:用户上传走 engine staging、沙盒 `persist`)。这是「backend 工具 → artifact → mount → 沙盒 → persist」受信数据脊柱的入境第一环,补 `tool-result-artifact-mount.md` 先例的「只能溢出兜底(>50k、固定 text/plain、不能具名)」缺口。

**包含**:
- **声明式而非命令式**:`ToolResult` 加可选 `artifact: Optional[ArtifactSpec]`(`title`/建议 id、`content_type`、文本或 blob)。工具**声明**「把我的 data 存成这个 artifact」,**不**持 `ArtifactService` 句柄(守三层模型:通用工具保持哑,只有内建 artifact/sandbox 工具——它们本就是 manager 层——直接碰 service)。
- **引擎路由**:`_maybe_persist_tool_result`(`engine.py:667`)加分支:`result.artifact` 命中 → `ArtifactService.ingest_tool_result(...)`(具名、带类型、blob 可、配额闸);现有溢出路径降为**同函数的无名兜底**。两者共用 `create_from_upload` 的配额/blob/去重内核。
- **模型见句柄非 blob**:tool_result 变预览片 + `read_artifact(id=)`/`mount` 提示(同今溢出)。
- **HTTP 自定义工具接入(首个数据工具)**:`config/tools/*.md` 加 frontmatter(如 `persist_as_artifact: true` + content_type 由 HTTP 响应 `Content-Type` 派生,CSV→`text/csv`);`HttpTool` 据此置 `ToolResult.artifact`。**operator 声明、非模型参数**(合「Minimize tool parameter surface」:模型不调「是否 persist」,工具作者定)。

**到时再敲定**:`ArtifactSpec` 精确字段;具名 id 与现 `_normalize_filename_to_id`/去重的衔接;`source="tool"` 的配额归属(同上传走 per-user blob 配额);HTTP 工具 content_type 派生的边界(响应头缺失/撒谎时)。

### B — 工具渐进式披露(tool-set 文件格式 + `search_tools`;MCP 适配缝)

**做什么**:解决「30-endpoint 平台 = 30 份描述常驻 system prompt」。披露单位 = **场景/平台(tool-set)而非单工具**,机制 = deferred 索引 + 按需加载。**与 skill 正交**(原则 1)。

**包含**:
- **tool-set 文件格式**:`config/tools/` 新增「一文件多 tool」格式(YAML 定义一个平台的 N 个 endpoint + 一条 set 级描述/索引行)。这也是未来 `scripts/` 级 openapi→tool-set 生成脚本的落点(operator 挑子集+润色)。tool 定义粒度不变、权限模型不动,只是分组+授权单元变成 set。
- **deferred 渲染**:tool-set(或单工具标 `defer: true`)只在 `<available_tools>` 出索引行(set 名 + 一句描述),完整 XML 描述不渲染。接入 `ContextManager.build()`(静态可缓存段)或动态段。
- **`search_tools` 内建工具**:模型调它(`select:Name,Name` 直选 或关键词搜),返回完整 XML 工具描述作 tool_result;此后这些工具进「已发现集」、后续轮持续渲染。新内建工具注册进 `dependencies.py:_load_tools()`,改 `generate_tool_instruction` 让 deferred 集渲染成索引而非全描述。**纯 prompt 级**(无 server `tool_reference` 依赖)。
- **MCP 适配缝(只留缝、不实现)**:给 tool 模型加 `source`/`provider` flag(仿 CC `isMcp`);registry 归一化所有来源到一个 tool 形 + 合并函数。将来「MCP server = 又一个 deferred tool-set provider、`mcp__server__tool` 命名、走同一 `search_tools`」即加法。

**到时再敲定**:tool-set 文件 schema;deferred 阈值(是否如 CC `tst-auto` 按 token 预算自动 defer,还是 set 显式声明);「已发现工具名」的追踪载体(message metadata?per-conv?)+ compaction 存活;**两个待显式过的语意**(memory `tool-ecosystem-positioning` 已标):① 历史中 tool_result 引用了「已不可见(未发现/已卸载)工具」时的状态文案;② `always_allow` 跨 set 同名工具语意。

### C — Skill 核心(存储 + L1/L2 注入 + skill 元工具 + 权限/上下文覆盖)

**做什么**:落地标准 Agent Skills 的**纯 prompt 修饰器**部分(不含 bundle 执行,那归 D)—— 三 scope 存储、L1 索引常驻、L2 激活注入 body、`allowed-tools`→权限覆盖。纯 prose skill(consolidate-memory 类)C 完即可用;脚本型 skill 等 D。

**包含**:
- **`Skill` 表 + 链接表**(决策 1/3):`id`/`slug`、`scope`、`owner_user_id`(preinstalled 为 null)、`name`、`description`(+ `when_to_use` 折进 description)、`allowed_tools`、frontmatter 其余字段、`body`(L2 文本)、bundle blob 引用(L3,D 用);`user_skill_links(user_id, skill_id, enabled)`。Repo/Manager/Router 三层(skill 非 session-scoped,Manager 做 ownership/可见性/序列化)。
- **API + 前端**:CRUD + scope/link/toggle 端点;前端 = 设置/管理页(非对话流内),admin 管 preinstalled/marketplace、user 管 private + 链接 marketplace。admin scope 守 `feedback-admin-scope-user-mgmt`(管共享资源,不碰用户数据)。
- **L1 注入**:`<available_skills>`(name + description),每 lead turn 渲染、预算约束(仿 CC ≈1% 上下文,超预算截断 description)。接入 `ContextManager.build()`,仿现 `_build_available_subagents` 模板。**只 lead 需要 skill**(subagent 不注入)。
- **L2 + `skill` 元工具**:模型调 `skill`(或用户 `/skill`),激活时注入 SKILL.md body 作 system-reminder(仿 CC);`disable-model-invocation` 的从索引隐藏 + 拒模型调用。
- **`allowed-tools`→权限覆盖**:映射到现有 `always_allowed_tools` 机制(`engine.py:658`),scoped 到 skill 激活。**两个待显式过的语意**(memory 已标):① 历史 tool_result 引用已不可见工具;② 跨 skill 同名工具 always_allow。
- **config 种子**:`config/skills/<name>/` 启动时 zip→blob→upsert 进 DB(`scope=preinstalled`、owner=null),内容哈希幂等防重(决策 5)。

**到时再敲定**:frontmatter 精确字段子集(v0 = `name`/`description`/`allowed-tools`?);`skill` 元工具 vs 用户 `/skill` 路由的统一;skill 与 B 的 tool-set 是否联动(**开放分叉**:CC 完全分离;我方**可选**让 skill 的 `allowed-tools` 激活时 auto-`search_tools` 预载其 set —— v0 倾向保持分离、再议);L1 预算具体值。

### D — Skill bundle 执行(L3 挂载进沙盒 + `compatibility` 依赖三层 + 离线 wheel)

**做什么**:让脚本型 skill(docx/pptx/xlsx/pdf 类)真能跑 —— 激活时把 bundle 挂进沙盒,模型 bash 跑其 scripts;依赖走原则 4 三层离线投递。这是「沙盒先于 skill」的兑现点。

**包含**:
- **bundle→沙盒挂载**:skill 激活时把 bundle blob 解到沙盒已知路径(如 `/workspace/.skills/<name>/`),body 的 `${SKILL_DIR}` 替换成该路径。注意这是**新挂载路径**(skill bundle,非 artifact)—— 与 artifact mount(C 沙盒既有)并存。auto-mount(决策 5)。
- **依赖三层兑现**(原则 4 / 决策 6):① 通用重栈进沙盒镜像(pandoc/libreoffice/poppler/qpdf + 科学栈,沙盒 plan B 段镜像扩容);② 离线 wheel bundle 固定位常驻 extras;③ skill asset `wheels/` 激活时 `pip install --no-index --find-links` 离线装。`compatibility` 字段声明需求。
- **典型闭环跑通**:用户传 docx → artifact(blob)→ mount → skill python 拆/改 OOXML 或 pandoc 转 → persist 回 artifact。这一条同时练 A(可选,数据工具变体)、C-mount、skill 执行。
- **`substitution` 子集**(可选):若存货/预装需要,实现 `$ARGUMENTS`;`` !`cmd` ``/`${SESSION_ID}` 暂不做。

**到时再敲定**:bundle 挂载点与 artifact mount 的命名空间隔离;沙盒镜像扩容清单(node?LaTeX?权衡镜像大小);wheel bundle 的 arch 化(沿沙盒 plan per-arch 纪律);`pip install` 离线在沙盒激活期的耗时/失败处理;CPU 纪律(解压炸弹/大 zip,沿沙盒原则)。

### E — 导入门禁与预装(确定性 validator + 监督式 adapter skill + pandoc-first 预装)

**做什么**:skill 的不可信输入门禁(原则 2)+ 把用户存货改造成预装集。**last step**(用户:存货预装前先改)。

**包含**:
- **确定性 validator**(plain code,跑每次导入含 user 私有上传):借 `skills-ref`/`skill-validator` 规则 —— 6 字段 schema、name↔dir、孤儿文件、未闭合 fence、内部链接解析、token/bundle 大小帽、`compatibility`-vs-(镜像栈 ∪ 自带 wheel)交叉校验(气隙网:声明需网即拒/标)。阻塞门禁。
- **监督式 adapter skill**(agent,admin 策展期跑,= skill-creator 的本系统改造版):懂本系统约束(沙盒工具词表、无网、mount/persist、tool-set 披露),读候选 skill 产**改造报告/建议 diff**(含 rewrite,因有人审)。其「系统知识」指向**活文档**(本 plan / skill-authoring 参考),不硬编工具名防漂移。
- **预装集策展**(决策 8):4 文档 skill 改 pandoc-first、删三份重复 `office/`、补 `compatibility` 声明、补 asset wheel(若长尾依赖);skill-creator 改造(去 CC widget/subagent-fork 假设)或暂不预装;schedule/setup-cowork **不移植**(驱动 CC 产品 widget)。改完进 `config/skills/`。

**到时再敲定**:validator 借哪些具体规则、阈值;adapter skill 自身是不是个预装 skill(自举);预装集最终名单;ZIP 导入导出端点(决策 6)。

## 关键风险

- **披露与 skill 职责漂移**(B/C)—— 一旦把披露塞进 skill,就回到「动态工具对小模型 legibility 负资产」的老坑。守原则 1:披露是工具层、skill 是覆盖层,review 时盯死边界。
- **依赖气隙地雷**(D)—— 社区 skill 默认运行时联网 `pip install`,搬进气隙网必断。`compatibility` 声明 + validator 交叉校验是唯一防线;存货零 manifest = 预装集须人工补全依赖声明。
- **bundle = 新不可信输入类**(E)—— skill 脚本在沙盒跑(隔离 OK),但「哪些 skill 准入」是 trust 决策,生态无 signing 层 → 门禁(validator + 人审 adapter)是我方唯一 trust 边界,不可省。
- **body 静默改写诱惑**(C/E)—— 工具词表对不齐时,regex rewrite body 看似省事实则脆(改坏比标记坏)。守原则 3:lint 标记 + 人审,绝不运行时静默改。
- **`search_tools` 无 server `tool_reference`**(B)—— 我方纯 prompt 级模拟,「已发现工具」追踪 + compaction 存活须自建;比 CC 多一份状态管理,但避开了 server beta 依赖。
- **沙盒镜像膨胀**(D)—— pandoc/libreoffice/科学栈 + 可能 node 烤进镜像,镜像大小与构建时间。权衡「通用即烤、长尾走 wheel」,别把长尾也烤进去。

## 变更日志

- 2026-06-16 起草。前置调研三件(参考实现 CC 工具披露+MCP、开放标准 agentskills.io 现状、用户存货)做完后定调。锁定 9 决策 + 7 原则,核心 = **原则 1「披露归工具层、skill 正交」**(CC 实证:`SkillTool` 零引用 deferral;披露 = deferred-tools+`ToolSearch`)。三 scope(private/preinstalled/marketplace)= DB + config 种子,market 降维成离线 link 动作。依赖继承沙盒原则 7 三层离线投递 + `compatibility` 声明。导入门禁二分(确定性 validator 每次 + 监督式 adapter skill 策展)。采标准 6 字段、v0 最小(name+description)。5 阶段:A 工具结果→artifact(独立前置)/ B 工具披露(tool-set+search_tools,留 MCP provider 缝)/ C skill 核心(存储+L1/L2+权限覆盖)/ D bundle 执行(L3 挂沙盒+依赖三层)/ E 门禁+预装(pandoc-first)。粒度 = plan 级。**开放分叉**:skill 是否联动预载 tool-set(v0 倾向分离)、分支策略(倾向逐阶段合 main,无破坏性中间态)。
<!-- 新日志按日期顺序追加到此行上方 -->
