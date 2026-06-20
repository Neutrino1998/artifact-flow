# Skill 系统 + 工具渐进式披露 —— 实施计划

> 状态:规划完成,实现未启动
> 起草:2026-06-16 · 最后更新:2026-06-17
> 前序产物:
> - `sandbox-implementation-plan.md`(本目录)—— 沙盒主线(A/B/C/D 全完成);本 plan 的执行底座,skill 脚本/asset 全部跑在沙盒里。原则 7「依赖三层离线投递」直接被本 plan 的依赖模型继承。
> - `tool-result-artifact-mount.md`(本目录)—— 工具结果溢出转 artifact 的先例(`source` 字段 / 自动命名兜底),本 plan A 阶段是它的「具名一等通道」升级。
> - memory:`tool-ecosystem-positioning`(工具生态定位,两大引擎缺口)、`skill-standard-adoption-direction`(沙盒先→标准 Agent Skills、XML 非障碍、market 离线)、`skill-system-research`(Phase 9 早期研究)。
> 调研基线(2026-06-16,本 plan 起草前做):
> - **参考实现** `../custom-claude-code/build-output`(本仓库同级,非项目内):确证 ① Skill 与工具披露是**两套独立机制**(`SkillTool` 零引用 deferral);② 工具披露 = deferred-tools + `ToolSearch`(按名注入 system-reminder,`select:`/关键词加载,`tool_reference` 块由 API 端展开);③ MCP 工具一律 deferred、`mcp__server__tool` 命名,走同一 `ToolSearch`;④ 统一 registry = 归一化到一个 `Tool` 形 + 合并函数,provider 区别仅留 `isMcp`/`source` flag。
> - **开放标准** agentskills.io 已多厂商(Cursor/Gemini/Codex/Copilot):`SKILL.md` 文件夹=单元,frontmatter **恰 6 字段**(`name`/`description`/`license`/`compatibility`/`metadata`/`allowed-tools`),`version` 归 `metadata`,绝大多数真实 skill 只用 `name`+`description`(触发逻辑塞进 description)。
> - **生态弱点 = 依赖与离线**:社区默认运行时 `pip install`(撞 `ModuleNotFoundError` 反应式联网装),对气隙网敌对 —— 本系统**领先于标准**:`compatibility` 声明 + 预置环境 + 自带 wheel + ZIP-of-folder 是更稳的路。validator 工具成熟(`skills-ref` 官方 / `skill-validator` 社区:孤儿文件/未闭合 fence/token 预算/链接解析),无 trust 层 = 导入门禁是我方责任。
> - **用户存货** `../utils/claude-skills/anthropic-skills`(本仓库同级,非项目内;仅供参考,预装前先改):8 个 skill,5 脚本型(docx/pptx/xlsx/pdf/skill-creator)+ 3 纯 prose;docx/pptx/xlsx **三份重复** `scripts/office/` + ~40 `.xsd`;依赖全靠预置环境(lxml/openpyxl/pypdf/pandas/Pillow + libreoffice/poppler/pandoc/qpdf + node),无 manifest;无 `$ARGUMENTS`/`${SKILL_DIR}` 替换;frontmatter 实际只 `name`/`description`(+`license`)。schedule/setup-cowork 驱动 CC 产品 widget = **不可移植**,真实目标存货 = 4 文档 skill(+ skill-creator 改造)。

## 本文档定位

这是一份 **plan,不是详细设计**。讲清每个阶段做什么、为什么、什么算完成;**落实细节(schema 字段、具体改哪些代码)留到开工那个阶段再敲定**。同时是**跨 session 跟踪文档**:新 session 先读「进度」一节知道做到哪、下一步从哪续。每推进一阶段,更新状态 + 「变更日志」追加结论;方向有变也记日志。

本 plan 覆盖三件相互咬合的事:**skill 系统**、**工具渐进式披露** 与 **MCP client**。一起设计的理由是「30-endpoint 平台怎么渐进式披露」这个问题 —— 它的答案(披露归工具层 = tool-set provider + `search_tools`,见原则 1)既划定了 skill 与工具的职责边界,也直接成了 MCP 接入的地基(MCP server = 又一个 deferred tool-set provider)。三者共用同一套 provider/披露抽象。

**写作约定(给后续编辑,务必守)**:每条原则/决策 = **一句话主张(扫一眼就懂)** + 缩进子点(机制 / 数据 / 关键陷阱),必要时末尾一行 **「留待将来:…」**。**论证只讲一次,不反复重述**;某次怎么绕弯、reviewer 第几轮提的这类 war story **进变更日志、不进正文**。变更日志**只记「为什么改 + 解决/避免了什么」,不复述正文细节**。一条 bullet 滚到三四行就该拆子点或删绕圈。

## 进度

- **当前**:**规划完成,未开工。** 沙盒主线(底座)已收官。架构主分叉已收口(披露归工具层 / 可见性=visibility+default_enabled+稀疏覆盖 / 依赖三层离线 / 导入硬软双门 / read+mount 复用零新机制 / external 工具/tool-set/mcp + agent 全 DB 物化[通用 config-seed→DB reconciler、skill/tool/agent 一套种子机制、config 仅作启动种子、注册表每 turn 一次 DB 快照]→ 与 skill 同构、config-vs-DB 双轨消失 / 部门授权统一 `department_resource_rule` 一张 polymorphic 表[服务 skill/tool/tool-set/mcp 四类 DB 实体、builtin=for-everyone 不入表、方向归行显式 effect 列、visibility 仅定未列部门默认、全 DB 统一 app-side cascade] / 工具权限两轴[等级归工具定义 · 成员应用序=agent 宇宙 → dept 收窄宇宙 → skill-enable(dept 移除 key、skill 在收窄后宇宙内翻 disabled)] / **agent 宇宙 = `agent_unit` m2m[agent MD 种 seeded 行 + UI 挂 dynamic 行,解 DB-native 工具进宇宙]、skill 全 agent 可见[效果按 agent 宇宙收窄]、工具侧 dept 授权只到 unit 粒度[tool-set 与 MCP 对齐、无 member 例外 → 删 set-vs-member 冲突/override/检测整套机器;skill enable/agent MD 声明同样 unit 粒度,builtin=singleton unit]、MCP 只 server 粒度[不持久化动态成员]**),细节留各阶段开工敲定;**数据模型总览见专节**。
- **下一步**:**A 阶段(工具结果→富格式 artifact)** —— 引擎前置、独立可做、对沙盒数据入境有独立价值(memory `tool-ecosystem-positioning` 缺口①)。但注意:skill MVP(文档 skill 跑在用户上传上)**不阻塞于 A**(上传已是 artifact、mount 已通);只有「数据工具→artifact→沙盒」类场景(DB→CSV→分析)才硬依赖 A。排序仍 A 先,因为它最小、最独立、且是后续富数据流入的脊柱第一环。
- **分支策略(已定:走 main)**:与沙盒 plan 不同 —— 沙盒走 `feat/sandbox` 不增量合 main 是因为有「半迁移态(md→Word 过渡)漏到生产」的风险。本 plan **无此类破坏性中间态**,A/B/C 是纯加法引擎/存储特性,故**逐阶段直接合 main、再按既有策略 overlay intranet**(遵 `feedback-branch-strategy`),不开长命特性分支。

| 阶段 | 内容 | 状态 |
|---|---|---|
| A | 工具结果→富格式 artifact(`create_from_upload` 的第三调用方) | 未开始 |
| B | 工具渐进式披露(tool-set DB 模型[迁出 config]+ `search_tools` 内建工具;MCP 适配缝) | 未开始 |
| C | Skill 核心(存储 + L1 注入 + `read_skill` + 权限/上下文覆盖 + 部门授权解析地基) | 未开始 |
| D | Skill bundle 执行(L3 挂载进沙盒 + `compatibility` 依赖三层 + 离线 wheel) | 未开始 |
| E | 导入门禁与预装(硬门槛 validator + 软门槛 verify agent + pandoc-first 预装) | 未开始 |
| F | MCP client(传输/协议客户端 + JSON-Schema→XML 适配 + provider 接入 B 的 deferred 披露) | 未开始 |
| G | 部门作用域授权 + 管理 UI(统一 `department_resource_rule` + 引擎组合有效集 + skill/toolset/mcp/tool 四类接入) | 未开始 |

依赖:D 依赖 **C(skill 存储/激活)+ 沙盒底座**;**A 不是 D 的硬依赖**(reviewer P2,消解与 line 24/132 的矛盾)—— D 的典型闭环(上传 docx→artifact→mount→skill)走上传通路、不经 A,只有"backend tool→artifact→skill"(如 DB→CSV→skill)那类场景才依赖 A。C 依赖 B 吗?**依赖**(reviewer P2,修正旧「任意序」)——披露机制本身与 skill 正交(原则 1),但**通用 reconciler 在 B 落地、skill@C 复用它**(决策 5);C 先做就要么重写 seed→DB、要么缺种子,故 C 依赖 B 的 reconciler 基础(顺带 B 的 tool-set 也是 skill 编排散文的常见消费者)。E 是 skill 线的 last step(用户:存货预装前先改)。**F 依赖 B**(MCP = 又一个 deferred tool-set provider,B 的 provider 缝 + `search_tools` 是 F 的披露地基),与 C/D/E 的 skill 线正交、可并行。**G 横切**:`department_resource_rule` 表 + 祖先链解析地基随 **C** 落(skill 是首个消费者、可见性必需),tool/toolset/mcp 接入随 B/F 各自 wire-in,**管理 UI + 引擎有效集组合 + 四类齐活 = G**;故 G 依赖 C(地基)+ B/F(资源类型存在)。A、B 各自独立可先做。

## 目标与范围

给系统增加 **标准 Agent Skills**(场景级 prompt 修饰器 + 沙盒可执行 bundle)、**工具渐进式披露**(让 30-endpoint 平台不再 30 份描述常驻上下文)、**MCP client**(把 MCP server 作为又一个 deferred tool-set provider 接入)与 **部门作用域授权 + 管理 UI**(admin 按部门配 skill/tool/tool-set/mcp,一套统一机制),让社区 skill 尽可能「拿来就用」、内网气隙下离线分发,打通对接只说 MCP 的内网系统的通路,并支持按组织部门分发能力。

**Non-goals(本期明确不做)**:
- **联网 skill registry / market**(skills.sh/skild.sh 等)—— 气隙网不可达,market 降维成 `public`+`default_enabled=false` + 链接动作,全 DB/离线(见决策 1)。
- **`context:fork` ad-hoc 子 agent** —— 我方 subagent 是预定义 agent 非 ad-hoc fork;v0 标为不支持(skill-creator 那类 fork 存货改造时降级)。(注:skill 现已全 agent 可见、非 lead-only,见 Phase C / 决策 11。)
- **substitution 全集**(`$ARGUMENTS`/`` !`cmd` ``/`${SESSION_ID}`)—— 存货零使用,v0 可只支持 `$ARGUMENTS` 子集或全不做。
- **`paths:` 条件 skill**(touch 文件 glob 激活)—— 我方无文件系统语义(artifact 是句柄非路径),v0 不做。
- **`model:` / `effort:` 模型覆盖** —— 这俩是 CC 私有扩展(不在标准 6 字段)、改的是"激活时切调用模型/思考强度"。**不做**:① 它是状态变更型激活,撞原则 8(`read_skill` 纯读、无副作用);② agent 已拥有自己的模型(MD frontmatter,operator 设定),skill 覆盖 = 越权打架;③ 气隙网模型就几个、lead 本就够强、存货零使用,价值近零。
- **server 端 `tool_reference` beta** —— 我方跑任意 backend(qwen via litellm)+ 自有 XML 工具格式,披露走**纯 prompt 级模拟**(见 B)。
- **联网 MCP registry / 公网 MCP 生态自动发现** —— MCP **client 本身现在在范围内(F 阶段)**,但气隙网够不着公网 server;MCP server 由 operator 显式配置(同自定义工具),不做联网市场/自动发现。
- **skill 版本解析 / per-skill venv** —— 依赖只加不 re-pin(继承沙盒原则 7 护栏)。
- **用户身份/凭证透传给工具(B1 用户字段 + B2 OAuth 金库)** —— 与"能不能用工具"(本 plan 做的部门授权)正交的另一根轴:让工具出站请求带用户身份。B1(注入用户已有字段 `department_id`/`role` 到 HTTP 工具模板)+ B2(per-user OAuth token 金库,act-on-behalf-of)推迟到独立 plan。**红线已记**:即便将来做,**只对受信 backend 工具开,沙盒工具一律拿不到用户凭证**(沙盒原则 7 出网纪律)。
- **运行时可编辑 agent 定义(UI 编辑 + DB-native)** —— **agent 本轮随通用 reconciler 物化进 DB(决策 5),但 config 仍是唯一作者真相、`seeded` 不可变、不经 UI 编辑**(`agents are data` 不破:DB 是物化缓存、非作者面)。**物化后的三档不对称**:skill/tool = 种子 + DB-native 可变 + dept 化;**agent = 种子-only DB 物化**(无 UI-native、无 dept 消费者、不入 `resource_type` enum);builtin = 代码、不入 DB、不入部门表。部门"定制 agent"靠作用域涌现(部门 grant 的 external tool/toolset + 部门可见 skill,决策 10),**不克隆 agent**。仅当部门需要**不同 prompt/model**(非工具)才需可编辑 agent,届时单独 plan(权限编辑面需 audit);届时 `resource_type` 扩 `agent` = 表 + enum 一行(agent 已是 DB 行、零迁移,本轮已就绪)。

## 贯穿原则

1. **渐进式披露归工具层,不归 skill —— 两者正交。** (参考实现实证:CC `SkillTool` 零引用 deferral 机制。)披露**机制** = tool-set(分组/披露单元)+ `search_tools`(发现工具);skill = **场景覆盖层**(注入 body 散文 + 权限覆盖),可**引用** tool-set 但不拥有披露。MCP 将来 = 又一个 deferred tool-set **provider**,进同一 registry、走同一 `search_tools`。把「30-endpoint 披露」这个职责钉死在工具层,skill 保持纯覆盖,是本 plan 的架构脊柱。
2. **Skill = 受信 prompt 修饰器 + 不可信 bundle 的二分,执行全归沙盒。** body/frontmatter 是**受信文本**(注入上下文、改权限);scripts/assets 是**不可信代码/数据**(只在 `--network=none` 沙盒里跑,绝不在 backend 执行)。skill bundle 是新的一类不可信输入,沿用沙盒不可信纪律(选品/审核门禁 = E 阶段)。这也是「先落地沙盒才做 skill」的根本原因 —— 沙盒是 skill 执行的底座。
3. **标准对齐优先于自造;body 原样搬,绝不静默改写。** 采 agentskills.io 开放标准(6 字段、文件夹=单元)。三块各自处理:**body = 自然语言、格式无关**,模型读完用 system prompt 教的 XML 格式发起调用,原样搬;**frontmatter = 映射层**(只 `allowed-tools`→权限模型;CC 私有扩展 `model`/`effort`/`context`/`paths` 全不支持 —— 见 Non-goals,撞原则 8);**substitution = 选择性实现**(只 `$ARGUMENTS` 子集)。社区 skill 的工具词表耦合(`Read`/`Grep`/`Edit` 等)在沙盒里多自然消解(= `cat`/`grep`/`sed`),残留硬耦合靠 **import lint 标记 + 人手改,绝不静默 rewrite**(脆且错改比标记更坏;verify agent 只标记/评估、不改)。
4. **依赖 ≠ 数据,沿用沙盒原则 7 的三层离线投递。** artifact 是用户拥有的**数据**(mount-in/persist、blob 进 DB);依赖是**执行环境**(① 镜像烤通用栈 / ② 离线 wheel bundle 固定位 / ③ skill 自带 asset)。②③同一套 `pip install --no-index --find-links` 机制、不同生命周期(常驻 vs 随 skill),别造两套。**护栏**:skill bundle 只做加法、不 re-pin 基础栈版本(否则一 turn 多 skill 版本冲突逼出版本解析机器,合「fix 复杂度超 feature 价值即退回 scope」)。标准的 `compatibility` 字段 = 声明层,导入时据此校验「需要的镜像没有且 asset 没带」→ 标记/拒。
5. **种子机制统一:skill/tool/agent 三类共用一个 `config-seed→DB reconciler`,全部 DB 物化(用户拍 B)。** 三者都非 artifact(artifact session-scoped;它们 user/系统-scoped 跨会话),preinstalled bundle 一份共享。
   - **两类来源态**:`seeded`(config 种子、reconciler 拥有、UI 不可改;**config 即唯一作者真相、DB 是物化缓存、git 可版本化**)/ `dynamic`(UI 新建、DB 原生可变,仅 skill/tool;agent 暂 seed-only)。
   - **reconciler(新横切底座,DRY 掉三个 loader)**:扫 `config/{skills,tools,mcp,agents}/` → per-type 解析(skill zip→blob+列 / tool yaml→行 / agent md→frontmatter+body)→ 内容哈希幂等 upsert → 标 `seeded` → **撞名 loud-fail**(顺带关掉 `load_all_agents` 同名静默覆盖,`loader.py:111`)。**只有 ingest 通用,消费侧各走各的**(skill 注入 / tool 进 resolver / agent 进 registry)。
   - **跑在 `deploy/entrypoint.sh` 的 leader-only 槽**(复用 migration 的 PG advisory lock,migration 后 / 起 uvicorn 前;SQLite 单副本直接跑)。**绝不在 per-worker lifespan `init_globals`**(每副本跑会互写)。
   - **运维触发 = 零新增步骤**:config 是 bind-mount(改文件即被容器看见、无需 rebuild),既有 `pause.sh`→改→`resume.sh`(`up -d` recreate)重起容器即重跑 entrypoint → reconcile 自动跑(与今天 lifespan 重读 config 同触发点)。
   - **增量覆盖**:per-unit 内容哈希(只写改了的单元)、就地按 name UPDATE 定义列保留 id(FK/ABA 干净)、config 删→prune + cascade 其 dept 规则、seed 撞 DB-native→loud-fail;只动定义列、不碰 grant 表/统计列。
   - **注册表 = 每 turn 一次 DB 快照,非进程级长缓存**:引擎每个 loop 执行前从 DB 读一次快照 + 静态 builtin 合并,turn 内多读点共用、不反复查 DB。**为何不用长缓存 + CRUD 失效**:① 避跨 worker 缓存失效(否则要 pub/sub),UI CRUD 下个 turn 自然可见;② turn 内一致性(目录不因 mid-turn CRUD 抖)。代价 = 每 turn 一次小读,可忽略(skill 今天即此)。
   - **agent 物化(B 选项)**:config 仍唯一作者真相、`seeded` 不可变、**不经 UI 编辑**(运行时可编辑 agent 仍 Non-goal);物化只为统一存储 + 撞名检查 + `resource_type=agent` 将来零迁移(v0 无消费者、不 wire)。
   - **builtin 例外**:代码、无 `config/builtin/` 可扫 → 不经 reconciler、for-everyone 不入部门表。
6. **离线 ZIP-of-folder 是唯一分发形态。** 社区四种分发(git clone / plugin install / CLI 包管 / ZIP 上传)里,只有 ZIP-of-folder 不假设 registry 连接,且是 Claude.ai/API/open-skills 共同接受的事实格式 —— 选它做导入导出单元。联网 CLI 全出局。**依赖才是气隙真地雷,非分发**:文件夹随 ZIP 走没问题,运行时 `pip install` 联网约定破在离线 —— 故原则 4 的预置/自带是硬要求。
7. **唯一硬上限是 bundle 字节大小(复用现成上传挡板);不设任何调用前 token/索引预算。** 私有化部署**无本地 tokenizer**,token 判断全靠推理框架返回的 usage = 事后量,任何「调用前算预算、超了拦」都落不了地。
   - **token 预算不设**:skill body 注入溢出由现有 compaction 反应式吸收(它本就拿返回 usage 比 `COMPACTION_TOKEN_THRESHOLD`),无独立 skill token 闸。
   - **L1 索引预算不设**:按预算丢 skill = silent eviction、违 loud-fail;索引大小靠 **operator 策展**约束,真臃肿了答案是把披露机制套到 skill 上(可搜索而非全常驻,B/C),不是运行时预算。
   - **bundle 大小是唯一上传期可按字节事前判定的上限,且按信任分层**(配额防滥用闸、非正确性闸):用户传 private skill 走 per-user blob 配额 + 413;admin 策展 skill 无闸(preinstalled 走 config 种子不经上传,marketplace admin 上传豁免 per-user 配额)。loud-fail + config 隐藏常量。
8. **「激活」是轻量动作:能力持有复用 `always_allowed_tools` 的持久化,不扫历史、不建状态机、不豁免沙盒 per-turn 纪律。** 两参考实现印证激活 = 把正文塞进 context 的 tool_result,唯一副作用是改权限。
   - **两个动作**:① L2 body 进 context(复用 read 的 tool_result、随压缩进出);② skill 能力进有效集。
   - **只动权限轴 B(成员)、永不碰轴 A(等级)**(两轴详见决策 11):skill 在 dept 收窄后宇宙内 enable `disabled` unit(unit 粒度,builtin=singleton);授 `Bash` ≠ 自动批准,`confirm` 照样弹。等级唯一运行时变更 = 用户「始终允许」`confirm→auto`。
   - **能力持有 = `state["active_skills"]` slug list**,照抄 `always_allowed_tools` 生命周期(回合末写 `Message.metadata`、下回合父消息捞回,`controller.py:176/529`):O(1)、不扫 `EventHistory`。body 随压缩走(忘了再 `read_skill`)但能力轴不被压缩静默撤权 —— 两件事解耦。v0 = append-only sticky,显式停用 defer。
   - **关键护栏**:`active_skills` 只管能力轴、**不复活 L3 mount** —— 沙盒 per-turn 销毁,带能力跨 turn ≠ 带 mount 跨 turn,下回合仍须自己 mount(沙盒已为「跨轮 mount 一致性」打过仗收手,不让它偷渡回来)。

## 已锁定的决策

1. **Skill 可见性 = `visibility`+`default_enabled` 两正交字段(替不透明 scope)+ 稀疏 `user_skill` 覆盖;部门作用域走统一 `department_resource_rule`(决策 10)。** Skill 表带:`owner_user_id`(private 用、shared 为 null)+ `visibility`(`private` 仅 owner / `public` 全员 / `department` 按 grant)+ `default_enabled`(shared skill 默认是否注入)。**原三 scope 拆成这俩正交字段**:preinstalled = `public`+`default_enabled=true`(config 种子);marketplace = `public`+`default_enabled=false`(目录可见、opt-in);**market = link 动作全 DB/离线,无联网 registry**。**per-user 覆盖 = 稀疏 `user_skill(user_id, skill_id, enabled)`**:无行=走 visibility/default,有行=用户显式开关 —— **marketplace 选用=enabled 行、关掉预装=disabled 行,link 与 toggle 是同一机制**(不是两套)。部门可见(`visibility=department`)= 用户祖先链 ∩ `department_resource_rule`(决策 10)。**`visibility` 现是 skill/external-tool/tool-set/mcp 四类共享字段**(原只 skill 有;external tool/tool-set/mcp 随本轮 DB 化获得 DB 列,见 Phase B/F + 决策 10):它定每个单元**未列出部门的默认姿态**(`public`=默认 allow / `department`=默认 deny);**一行的方向看规则行显式 `effect` 列、非由 visibility 派生**(决策 10)—— 四类 `visibility` 都是 DB 列(不再 config frontmatter)。`private` 仅 skill 有(owner-only);`private` 与 builtin 都**不进 `department_resource_rule`**(决策 10)。
2. **披露 = tool-set(DB 模型)+ `search_tools` 内建工具,纯 prompt 级。** 新增「一组多 tool」的 tool-set(一平台多 endpoint,DB 存储[config 仅种子]、未来 openapi 生成脚本的落点),整组标 deferred:索引行常驻 `<available_tools>`、schema 不渲染;模型调 `search_tools`(`select:Name,Name` 或关键词)拿回完整 XML 工具描述作 tool_result。**返回值复用 `generate_tool_instruction`**(= 渲染系统提示词可用工具那套);**描述随 tool_result 留在历史、后续轮模型自然可见 —— 不维护「已发现集」、不重渲染进 system prompt**(比 CC `extractDiscoveredToolNames` 追踪更简,且无 server `tool_reference` 依赖)。**为何这里靠历史、`active_skills` 却要 metadata-state(决策 11/原则 8)**:发现是**上下文**问题(描述在不在 context),非**权限**问题(可调与否由 resolver 按 unit 成员判、**与发现无关** —— deferred 只是描述没渲染、工具在授权 unit 里闸照过);上下文的家是历史,被压缩则模型见常驻索引行**自己再 search 一次**(loud-fail 自纠),无需 durable state。**附带**:发现动作不改写 system prompt —— 索引/catalog 已挪动态 reminder、只 grammar 留前缀(决策 11/Phase B line 103),前缀稳定可缓存、不被发现动作击穿。
3. **存储 = 消费列 + `metadata` JSON 杂项列 + `skill_md` 全文列 + 完整原始 blob(含 SKILL.md),复用 blob 存储。6 标准字段全部 DB 落位、按"消费与否"分流。** 四处:① **消费列**(`name`/`description`/`allowed_tools`/`compatibility`/`slug`/`visibility`/`default_enabled`/`owner_user_id`,**`visibility`+`default_enabled` 替原 `scope`,见决策 1**)—— 系统要查询/消费的字段反规范化出来(`compatibility` 供气隙依赖校验,决策 6),L1 批量列举 + 权限 + 校验,**不解 blob**;② **`metadata` JSON 列** —— frontmatter 里**系统不单独消费的字段全归这**(`license` + 标准 `metadata` 容器[含 `version`] + 任何未知扩展),"用不上的扔 metadata"、`license` 不开独立列,免解 blob 的杂项读取层;③ **`skill_md` TEXT 列** —— SKILL.md 全文,L2 `read_skill` 直接返回(免解 blob);④ **`bundle` BLOB** —— **完整原始 zip(含 SKILL.md + references + scripts + assets)**,L3 mount + 导出。**关键:blob 是真相源、存验证通过的原始上传整包**,①②③ 都只是反规范化的查询/读取副本(**不碰原 frontmatter 结构** —— `license` 在原 SKILL.md 顶层就还在顶层,DB `metadata` 列只是副本归类、非挪字段);故**导出无损仍是 construction 保证的**(直接吐原 blob,不从列重序列化 → 未知字段/格式不丢);代价是 SKILL.md 三处冗余(小文本,换无损+简单导入,值)。`preinstalled` 同 bundle 全用户共享一份(非 session-scoped)。
4. **三级渐进式披露,「激活」= 一次普通 read + 一次普通 mount,无独立机制。判别线:SKILL.md 走读通路,bundle 里任何东西走沙盒。** L1 =「有哪些 skill」由 **ContextManager 注入** `<available_skills>`(name + description,仿 artifact inventory 先例 —— 列表是 ContextManager 职责非工具职责,故**不学 opencode/CC 把 L1 塞进工具描述**)。L2 = 模型调 **独立 `read_skill(slug)` 工具**返回 `skill_md` 全文作 tool_result(纯文本、provider-agnostic,opencode 实证此为可移植形态;**死简单——按 slug 返回 skill_md,无 path 参数、后端不解 zip**;**不合并进 `read_artifact`** —— 身份空间不同[user-scoped slug vs session-scoped id]、store/Manager 不同,合并要么加 type 参数[违 legibility]要么藏第二个 Manager;**镜像 read_artifact 契约**:句柄进/内容出、`max_result_size_chars=inf` 永不二次 persist、`AUTO`)。正文之外的一切(references/scripts/assets)**不走读通路、一律去沙盒读**(`read_skill` 输出附「其余细节含 references 须 mount 进沙盒读」提示)。L3 = 模型按需调 **现有沙盒 `mount` 工具**(复用,非新机制)把完整 bundle 挂进沙盒**固定约定路径** `/workspace/.skills/<slug>/`,模型用 bash `cat`/`python` 读跑;`${SKILL_DIR}` 因路径由 slug 确定**在 mount 前即可解**(替成约定路径),mount 返回真实路径作运行时确认 —— **占位符问题就此关掉**。
5. **bundle 走模型驱动 per-turn mount(复用现有 `mount`),不 auto-mount、不维持跨轮一致性。** 激活**无沙盒副作用**;`read_skill` 是纯读。bundle 内容(references/scripts)在哪轮用就哪轮 mount(同 artifact mount),受沙盒既有 per-turn ephemeral 纪律 + `<sandbox_status>` + bash file-not-found loud-fail 自纠管 —— **绝不为 skill 重建跨轮 mount 一致性机器**(沙盒 plan 已打过这仗并收手,见原则 8)。persist 仍模型驱动(沙盒 `persist`→artifact 现成)。
6. **依赖 = `compatibility` 声明 + 镜像通用栈 + asset 离线 wheel。** 通用重栈烤镜像(python + lxml/openpyxl/pypdf/pdfplumber/pandas/Pillow + libreoffice/poppler/pandoc/qpdf,存货与调研同指一组);长尾走 asset `wheels/`,**模型在沙盒里按需** `pip install --no-index --find-links` 离线装(**非激活自动**,守原则 8;详见 Phase D + changelog 06-18);import validator 据 `compatibility` 交叉校验(镜像栈 ∪ 自带 wheel),缺口标记/拒。
7. **导入 = 单管线两道门:`upload → unzip → 硬门槛(确定性 validator,阻塞)→ 软门槛(verify agent,可强制通过)→ 解析 frontmatter + 入库`。** **硬门槛** = plain code 阻塞门禁(便宜确定、先跑快速失败),借 `skills-ref`/`skill-validator` 规则:6 字段 schema、name↔dir、孤儿文件、未闭合 fence、链接解析、SKILL.md 体量(legibility 警告)、`compatibility`-vs-环境。**bundle 字节上限不在此** —— 那是上传路由的配额闸、按信任分层(原则 7③,admin 无闸)。**软门槛** = verify agent(懂本系统约束的 agent,贵=LLM 调用、后跑、**可 override**):**职责单一 = 评估「这个 skill 能不能在本系统跑」**,就三查 —— ① 用了没有的工具/harness?② 需要装依赖(`compatibility` 声明 vs 镜像栈 ∪ wheel)?③ asset 放好没?**输出诊断(能跑/缺什么),不改 body、不产 rewrite 或改造 diff**(改造/简化是人的事,见决策 8)。**用户私有上传时可选/可跳过**(LLM 成本)。「lint 不静默改写」在此:残留硬耦合由确定性 lint 标记 + **人**审手改,运行时禁静默 rewrite。入库 = 索引列 + `skill_md` + 完整原始 blob(决策 3)。
8. **预装 pandoc-first(瘦 bundle)。** 存货 4 文档 skill 三份重复 `office/` + 40 xsd 做 OOXML 手术;常见 Word 路径(读/转/简单生成)预装 skill **首选 pandoc/libreoffice**(镜像内,一条 CLI),OOXML 拆解只留给 pandoc 真做不到的(改痕/批注/精确版式)。删常见路径的三份 `office/` bundle。**这个分析简化是「我们」在 E 阶段人工做的工程动作**(预装集就 4 个、值得手工调),**不交给 verify agent**(后者只评估能不能跑、不改 skill,见决策 7)。
9. **采开放标准 6 字段,DB 全部落位、按消费分流。** `name`/`description`/`license`/`compatibility`/`metadata`/`allowed-tools`;`version` 归 `metadata`。**6 字段在 DB 都有落位(非只 name+description),按"系统消费与否"分流**(决策 3):消费的开独立列(`name`/`description`/`allowed_tools` 必做,`compatibility` 供气隙校验),**系统用不上的(`license`/`metadata`/`version`/未知扩展)全归 `metadata` JSON 杂项列**;真相源仍是原始 blob(**不改 frontmatter 结构、导出无损**)。**硬依赖只 `name`+`description`**(存货现状、其余常空),其余字段消费宽容缺失。`allowed-tools` 标准里本就 Experimental → 映射到我方 **unit 粒度**权限模型(决策 11 轴 B:builtin=singleton unit 故逐工具名原样工作,多工具 unit 成员名解析到整 unit)、不欠生态硬兼容。
10. **部门授权 = 一张 polymorphic `department_resource_rule` 表,服务 skill/tool/toolset/mcp 四类 DB 资源;builtin for-everyone 不入表,agent MD 不动、引擎运行时组合。**
    - **表**:`(department_id, resource_type∈{skill,tool,toolset,mcp}, resource_id, effect∈{grant,deny})`,一资源多部门=多行。四类全 DB 行、各带 `visibility`(external tool/set/mcp 随本轮 DB 化获得,见 Phase B/F + 决策 5)。
    - **方向 = 行显式 `effect` 列,非从 visibility 派生**(reviewer P1):派生态下 operator 改 visibility(`public`→`department`)会把既有 deny 行静默翻成 grant、反授权本想禁的部门。存显式 effect 后,改 visibility 只动「未列出部门的默认值」(`public`=默认 allow / `department`=默认 deny)、**永不翻已有行**;UI 按 visibility 预填 effect,但存显式值抗漂移。
    - **解析**:user `department_id` 走 `parent_id` 祖先链 ∩ 规则集(父覆盖整子树);命中→行 effect,未命中→单元 `visibility` 默认。各方向只需 1 行(树覆盖子树)。
    - **工具侧只到 unit 粒度**(与 MCP 对齐):`tool`=独立工具 unit、`toolset`=整 set、`mcp`=整 server,只整 unit grant/deny,**无点名 set 成员的跨粒度规则**(原 `tool` 可指 set 成员的破例已删 → 连带删 set-vs-member 冲突/override/检测,见变更日志)。要切 set 子集 = 拆 set。`skill` 行不是 unit、按整 skill 授权。
    - **builtin + private 不入表**:builtin for-everyone(与 agent MD 对称、跨部门一致,部门级杠杆将来=agent 级);private owner-only。Manager/UI 硬拒写入。
    - **为何一张 polymorphic 表(非四张 FK)**:四类异构但共一条 resolver 路径 + 一套 UI + 一致 cascade。无单 FK → 删资源时各 Manager 同事务 app-side cascade **仅指向它的行**(`skill`→`user_skill`;`tool_unit`→`agent_unit`+成员行;四类共有 `department_resource_rule`);悬空规则解析成空、无害。
    - **agent** 虽随决策 5 物化为 DB 行,但 v0 无 dept 消费者 → 不入 `resource_type` enum;将来 dept 化加一行零迁移。
    - **命名** `department_resource_rule`(中性:默认姿态在资源 `visibility`、不在表名)。
    - 留待将来:参数级粒度(`permission-param-granularity-direction`)、树 override 优先级、grant 存不可复用 id 根治 ABA。

11. **工具权限 = 两条正交轴,经统一 `EffectiveToolset` resolver 单点解析、多处消费。**
    - **轴 A 等级 `{auto, confirm}`**:唯一来源 = 工具定义(`tool.permission`),config/agent MD/skill 一律不可改;唯一运行时变更 = 用户「始终允许」`confirm→auto`(`always_allowed_tools`)。
    - **轴 B 成员 `{enabled, disabled}`(+absent)**:agent MD 写 / m2m 减 / skill enable —— 与等级是不同枚举(`disabled` 不进 `ToolPermission`)。
    - **四层链(逐层只收窄/翻开,应用序 = 层号序)**:① 工具定义 = 能跑什么 + 各自等级,雷打不动;② **agent 宇宙 = agent MD builtin(直读)∪ `agent_unit` 绑定的 external 单元** = 天花板(每项 enabled/`disabled`,absent=不在宇宙);③ **dept 收窄宇宙**(决策 10)= 把部门未授权的 external 单元从宇宙移除 key、不在成员态上加闸,builtin 绝缘;④ **skill**(`active_skills`)= enable-only,翻开收窄后宇宙里的 `disabled`,不加删 key、不碰等级。
    - **`agent_unit` m2m 统一静态+动态**:agent MD external 声明经 reconciler 种 `seeded` 行、UI 挂载加 `dynamic` 行 —— 解了「冻结 config 天花板容不下 UI 新建的 DB 工具」(reviewer P1#1);builtin 留 MD 直读、不入 m2m。
    - **dept 在 skill 之前收窄 → private skill 翻不开 dept-denied 是 by-construction**(非末端 AND 闸;解析详见数据模型总览「工具有效集」)。
    - **P0 信任边界**:skill 只能翻 agent 宇宙内成员态 `disabled` 的 unit,做不到引入 `absent`(MD 未声明且 `agent_unit` 未挂)、碰等级、翻 dept-denied。故 operator「任何 skill 都别碰」= 让它 `absent`(不是 `disabled`);敏感工具靠 `confirm` 等级(skill 不可改、用户每次在环)兜底 → 私有 skill 无法静默提权,故 verify 门禁非权限边界、私有上传可跳过 verify。
    - **粒度统一 = unit**:agent MD 声明 / m2m 减 / skill enable / dept grant-deny 一律在 unit 上,一套 match 函数(按 unit 名)、**无第二套语义**(reviewer P2)。**builtin = singleton unit**(对 builtin 即逐工具,标准 `allowed-tools` 逐工具名原样工作);多工具 unit(set/MCP)要更细 = 拆 unit。**等级是唯一 per-tool 量**。over-grant(skill 开整 unit ⇒ 成员全开)兜底 = `confirm` 等级 + 拆 unit。
    - **命名 `<unit>__<tool>`**:`__` 唯一分隔、恰好一次,unit/tool 名只用单 `_`;MCP = `<server>__<tool>`(不带 `mcp__` 前缀,否则两个 `__` 撑破 `<unit>__*` 通配)。**resolver 只按已知 unit 名前缀匹配、不 split `__` 反推**(reviewer P2);unit 名跨 tool-set/MCP 全局唯一、启动期撞名 loud-fail(见 B)。
    - **tool-set ↔ MCP 同型**:授权/披露/命名整链对齐,唯一别 = 静态(config 已知)vs 动态(连接时灌)→ B 把缝按 MCP 形状留对,F 纯加法。
    - **存储 + 解析**:agent MD `tools:` 存单元引用 + 成员态(不摊平);resolver 解析时展开成扁平 `{tool: level}`(builtin 直读 / external 从 `agent_unit` 取 / tool-set 静态展开 / MCP 运行时填),每项套「成员性 + 工具定义等级」。
    - **resolver = 唯一解析点**,所有读点读同一输出:渲染 `ctx:89`、条件提示词段 `ctx:204/215`、执行闸 `engine:759`、等级 `engine:844`、`search_tools` 可见集过滤 —— 由构造消灭多消费点漂移。
    - **迁移 + schema**:现有 agent MD `{name: auto/confirm}` 一律按 enabled 读(等级来自工具定义),只在「关但可被 skill 开」处写 `disabled`;未知成员态字面量启动期 loud-fail。
    - 留待将来:per-skill ACL(哪个 skill 能开哪个 `disabled` unit)—— 窄,不为它加 skill trust-tier 状态机。

## 数据模型总览(表关系)

> 汇总决策 1/3/5/10/11 散落提到的表;exact schema 各阶段开工再定,此处定**表形状与关系**。`seeded` flag 横切定义表(`seeded`=config 种子、reconciler 拥有、UI 不可改;`dynamic`=UI 增删改、UI 拥有)。

**定义表(「东西」本身,带 `seeded` flag)**

| 表 | 装什么 | seeded(config 种子) | dynamic(UI) |
|---|---|---|---|
| `skill` | 定义 + `skill_md` + bundle blob + `visibility`/`default_enabled` | preinstalled | marketplace / private |
| `tool_unit` | external 工具单元(tool-set / mcp-server / 独立工具)定义 + `visibility` | operator 出厂 | UI 新建 |
| `agent` | agent 定义(model / prompt / 声明的 builtin) | `config/agents` 全部 | 无(v0 不开) |

builtin = **代码、无表**(agent MD 点名、for-everyone);tool-set/mcp 成员用 `<unit>__<tool>` 寻址。

**绑定表(「谁能用」,m2m)**

| 表 | 连接 | 回答 | 谁写 |
|---|---|---|---|
| `agent_unit` | agent ⟷ tool_unit | 这 agent **暴露**哪些 external 单元(= 宇宙) | `seeded`=agent MD 经 reconciler 种;`dynamic`=UI 勾选挂载(绑定入口见 Phase B) |
| `department_resource_rule` | department ⟷ {skill/tool/toolset/mcp}(polymorphic) | 这部门**能用**哪些(显式 `effect` grant/deny) | admin UI |
| `user_skill` | user ⟷ skill | 用户对 skill 的个人开关(覆盖默认) | 用户 |

**已有组织表**:`user`(`department_id` / `role`)、`department`(`parent_id` → 部门树,祖先链解析靠它)。

**引用关系**(箭头 = 外键/引用方向):

```
user ─department_id─> department ─parent_id─> department      (部门树)
agent_unit ─agent_id─> agent ;  ─unit_id─> tool_unit          (真 FK,可 DB cascade)
department_resource_rule ─department_id─> department ;
                         ─(resource_type, resource_id)─> skill / tool_unit   (polymorphic 软引用、无单 FK → app-side cascade)
user_skill ─user_id─> user ;  ─skill_id─> skill              (真 FK,可 DB cascade)
```

**一个 turn 怎么算有效集**(resolver,决策 11;每 turn 从 DB 读快照):用户 U∈部门 D、用 agent A、激活 skills S →

- **工具有效集**(三步、**dept 收窄宇宙本身、不是末端 AND 闸** —— 部门 `deny` 的工具直接从宇宙移除 key,skill 物理上够不到):
  - ① **宇宙(ceiling)** = builtin(A,从 `agent`,for-everyone 恒在)**∪** external 单元(`agent_unit` 挂给 A 的),每项带成员态 enabled/`disabled`;agent MD 与 `agent_unit` 都没有的工具 = **不在宇宙**(absent)。
  - ② **dept 收窄宇宙** = 对 external 单元按 visibility 默认 + `department_resource_rule`(D 祖先链)显式 `effect` 解析,**部门未授权的直接移出宇宙**(key 删掉、连 enabled/disabled 状态都不再有;`public` 无规则行=授权**保留**、`department` 无 grant=**移除**,规则行只做 grant/deny 例外);**builtin 不过此步**(for-everyone、无 visibility、不进 dept 表,恒在宇宙)。
  - ③ **成员轴(在收窄后的宇宙内)** = 宇宙内 enabled 的项 **∪** S enable 的 `disabled`;skill **只翻幸存宇宙里**的 `disabled`。
  - **关键(reviewer P1)**:`deny` 的工具在 ② 已被移出宇宙、③ 里 skill **无 key 可翻** → private skill 绕不过部门授权是 **by-construction**(非靠末端 AND);与决策 10 / Phase G「dept 是 resolver 最后一个输入层」一致(dept 收窄在 skill enable 之前)。每工具**等级**从其定义查(`tool_unit` 行 / builtin 代码),绑定表不存等级。
- **可见 skill** = `skill` 的 visibility/default + `user_skill(U)` 覆盖 + `department_resource_rule(D, skill)`;**全 agent 可见、不分 agent**(效果按各 agent 宇宙收窄,决策 11)。

**删除 cascade**(决策 10):删 dynamic 资源 → 同事务只 cascade **指向它的**关联行(按类型,不是全删四张):**`skill`** → `department_resource_rule` + `user_skill`;**`tool_unit`(tool/toolset/mcp)** → `department_resource_rule` + `agent_unit` + tool-set 成员行;静态删(reconciler prune)同样按类型 cascade。

## 阶段

### A — 工具结果→富格式 artifact(引擎前置,沙盒数据入境的受信通路)

**做什么**:让 backend 工具(受信/有网/有凭证,如 HTTP 工具 GET 回 CSV)能把结果存成**具名、带 content_type、可二进制**的 artifact —— 成为 `create_from_upload` 的**第三调用方**(前两个:用户上传走 engine staging、沙盒 `persist`)。首个消费者 = `web_fetch` 文件旁路(见下)。这是「backend 工具 → artifact → mount → 沙盒 → persist」受信数据脊柱的入境第一环,补 `tool-result-artifact-mount.md` 先例的「只能溢出兜底(>50k、固定 text/plain、不能具名)」缺口。

**包含**:
- **声明式而非命令式**:`ToolResult` 加可选 `artifact: Optional[ArtifactSpec]`(`title`/建议 id、`content_type`、文本或 blob)。工具**声明**「把我的 data 存成这个 artifact」,**不**持 `ArtifactService` 句柄(守三层模型:通用工具保持哑,只有内建 artifact/sandbox 工具——它们本就是 manager 层——直接碰 service)。
- **引擎路由**:`_maybe_persist_tool_result`(`engine.py:667`)加分支:`result.artifact` 命中 → `ArtifactService.ingest_tool_result(...)`(具名、带类型、blob 可、配额闸);现有溢出路径降为**同函数的无名兜底**。两者共用 `create_from_upload` 的配额/blob/去重内核。
- **模型见句柄非 blob**:tool_result 变预览片 + `read_artifact(id=)`/`mount` 提示(同今溢出)。
- **web_fetch 文件旁路(首个数据工具)**:现有 `web_fetch` 走 jina 提网页正文、对 PDF/二进制 URL 本就坏 → 加旁路。
  - **按 URL 尾缀路由**(零成本、免 Content-Type 探测卡顿):文件类(`.pdf` 等)在 Jina 之前分流、直连下载置 `ToolResult.artifact`(blob + content_type 读响应头、尾缀兜底),网页类照旧。复用 `_fetch_pdf` 直连 + `_read_capped` 封顶,按 bytes 存 blob、绝不 `.text`(避编码探测)。
  - **运行时工具内自决、非模型参数**(合「Minimize tool parameter surface」)= 决策 A line 88 声明式的另一形态(静态声明 persist 留给未来 DB-配置 API 工具)。
  - **SSRF 必须自带**(reviewer P1):现有 `validate_public_url`(`_fetch_single_url:202`)仅 Jina 失败后才跑,旁路在 Jina **之前**会绕过它 → 旁路直连前必须自己调一次 `validate_public_url` + 保 `allow_redirects=False`。
  - 仅联网部署有效(内网 web 工具禁用)。PDF 从「抽文本」变「blob 句柄」(对齐上传 blob-only)。

**到时再敲定**:`ArtifactSpec` 精确字段;具名 id 与现 `_normalize_filename_to_id`/去重的衔接;`source="tool"` 的配额归属(同上传走 per-user blob 配额);HTTP 工具 content_type 派生的边界(响应头缺失/撒谎时)。**验收项**:旁路 SSRF —— 每次直连前再校验、`allow_redirects=False`、覆盖 302→内网/元数据地址(`169.254.169.254` 等)被拦的测试。

### B — 工具渐进式披露(tool-set DB 模型[迁出 config]+ `search_tools`;MCP 适配缝)

**做什么**:解决「30-endpoint 平台 = 30 份描述常驻 system prompt」。披露单位 = **场景/平台(tool-set)而非单工具**,机制 = deferred 索引 + 按需加载。**与 skill 正交**(原则 1)。

**包含**:
- **tool-set DB 模型 + 命名空间**:
  - external 工具/tool-set 本轮**从 config 迁入 DB + 前端 CRUD**(决策 5/10:`config/tools/` 种子 = 不可变 seeded,UI 新建 = dynamic,无论来源都是 DB 行)。存储 = 「一平台多 endpoint + set 级描述/索引行 + `visibility` 列」。**凭证不进 DB**:只存「引用哪个 secret 名」,`{{TOOL_SECRET_*}}` 值留 env(见 Phase F)。
  - **成员命名 `<setname>__<tool>`**(`__` 唯一分隔、恰好一次,MCP 同型 `<server>__<tool>` 不带 `mcp__`),loader 据 set 名自动加前缀(作者写裸名)。`__`=分隔、单 `_`=名内,故扁平 builtin 仍可区分、`<unit>__*` 通配无歧义。
  - **为何命名空间**:① 关键 = 让 unit 粒度授权按前缀 `<setname>__*` 整组工作(与 MCP `<server>__*` 同构);② 跨 set 同名 endpoint 不撞;③ 模型可读。**用 `__` 不用 `:`**:参数是 XML 标签、`_repair_tool_name_as_tag` 会把名当标签,冒号撞 XML namespace。
  - **unit 名全局唯一、启动期撞名 loud-fail**:扩现有 `build_tool_map`(`base.py:279`)认 tool-set/MCP unit 名(非现成机制);顺带由 reconciler loud-fail 关掉 `load_all_agents` 同名静默覆盖。这也是未来 openapi→tool-set 生成脚本的落点。
- **`agent_unit` 绑定 API/UI 在此落地(决策 11,reviewer P2)**:UI 新建的 `tool_unit` 进 DB、甚至被部门 grant 后,**仍须挂到某 agent 的宇宙才可用**(否则对所有 agent 都 `absent`)—— 这个挂载入口就在 B。operator 在工具管理页勾选「此 unit 挂给哪些 agent」→ 写 `agent_unit` 的 `dynamic` 行(seeded 行由 reconciler 从 agent MD 种,见上)。**这只是给 agent 挂能力单元、不是编辑 agent 的 prompt/model**(运行时可编辑 agent 仍 Non-goal);故它是 operator 资源管理动作、与 G 的部门授权正交(宇宙=该 agent 暴露什么,dept=哪个部门能用)。**必须在 B(非 G):** 没有这个绑定,B 之后 UI 建的工具对任何 agent 都不可达 = 比 dept 更基础的一环;dept 是宇宙之上的额外闸。
- **通用 `config-seed→DB reconciler` 在此落地(横切底座,决策 5)**:tool 的「config 文件→DB」不是 tool 专属,是 **skill/tool/agent 共用的种子 ingest**(扫目录 + per-type 解析 + 内容哈希幂等 upsert + `seeded` 不可变 + 撞名 loud-fail)。**随第一个消费者 tool 在 B 建好**,skill@C、agent retrofit 复用同一件(agent retrofit 顺手关掉 `load_all_agents` 撞名缺口)。**澄清「G 是不是把 tool 表也做了」**:不是 —— tool 表在 B、mcp 在 F、agent 物化随 reconciler;**G 一张注册表都不建,只消费 reconciler 产出的 DB 行**(查 grant + 树解析)。引擎**每 turn 执行前从 DB 读一次注册表快照**(+ 启动期静态 builtin),turn 内读快照不反复查 DB(详见决策 5:per-turn 快照避开跨 worker 缓存失效 + 保 turn 内一致)。
- **`EffectiveToolset` resolver 骨架在此立(基础设施,决策 11)**:把现散在 4 处各自直读 agent MD dict 的读点(渲染 `ctx:89`、条件提示词段 `ctx:204/215`、执行闸 `engine:759`、等级 `engine:844`)收成唯一解析点。**必须在 B 收口**:B 是第一个改变工具集形状的阶段,收口越晚同一组读点被反复 refactor 越多次(B/C/G 三轮碰同 4 点 = 退回架构信号)。
  - **B 立骨架时输入只有静态两样**:① agent 宇宙(agent MD builtin ∪ `agent_unit` external)② tool-set 单元展开(`<set>__*`,tool-set 要可调本就必须展开 = B-native 职责)。输出 = 扁平 `{tool: level}`。
  - **之后每阶段只加一个输入层、不再碰读点**:C 加 `active_skills`、F 加 MCP 运行时填充、G 加 dept 规则。`search_tools` 是第 5 读点(B 新建、非返工)。
- **deferred 渲染(走 CC 激进路线)**:tool-set(或单工具标 `defer: true`)只在 `<available_tools>` 出索引行,完整 XML 描述(含 `parameters`)不渲染。**索引行 = tool-set 一条 set 级描述 + 成员工具名列表(光名字、不给每工具 desc);单工具 deferred = 光工具名**(对齐 CC —— CC 连描述都不给、赌名字自解释;我方 tool-set 名字外包一层 **set desc 做语境**,比 CC 孤立名字更稳)。完整描述由 `search_tools` 经 `generate_tool_instruction` 按需补全。**defer 是显式开关、不按 token 自动**:config 配了 `defer: true` 才 defer(只出 name)、没配则完整描述照常注入;私有化无 tokenizer 算不了预算,故**不学 CC `tst-auto` 的 token 预算自动 defer**(合原则 7、一切 operator 显式配)。
- **tool list 挪进动态 reminder(prompt-caching)**:工具描述现在坐 system prompt 前缀(`context_manager.py:92`),工具列表会话内动态后每次变化打掉整段历史 cache。**改:拆 `generate_tool_instruction` 两段** —— 稳定的 tool-call 协议语法(`xml_formatter.py:17-42` 的 `<format>` 块)留前缀保可缓存;动态的工具 catalog(`:44` 循环)挪进 `_build_dynamic_context`、与 `artifacts_inventory` 同级(历史末尾 reminder,`ctx:77` 早立此原则,catalog 是唯一没跟上的)。catalog 变化只失效末尾、grammar 前缀恒稳。reminder 有序:`task_plan` 打头,tool list 挨 artifact inventory。当前生产无 APC = 收益暂 0 但无害,prompt 全可控故零成本可挪。
- **单元非 discovery 边界(两层够、无第三层)**:MCP `tools/list` 扁平一发返全部、无原生渐进发现;tool-set 同理。故 unit 只做授权 + 生命周期边界,披露就两层:① 检索注册表(`search_tools`)= 全成员扁平注册(MCP 连接灌 / tool-set 启动灌);② L1 索引 = enabled 工具 name 全注入(unit = set desc + 成员光名字,不做自动折叠)。怕大 unit(50 工具 MCP)撑爆 L1 → 不靠折叠,靠 agent MD 配 `disabled` + skill 按需 enable(operator 显式控制)。「server 里有啥」= `search_tools(unit=x)` 普通查询,不做 `list_members` 第三层。
- **`search_tools` 内建工具**:`select:Name,Name` 直选或关键词搜,返回完整 XML 描述作 tool_result(复用 `generate_tool_instruction`)。**结果必须过滤到当前 EffectiveToolset 可调集**(reviewer P1):含 enabled-but-deferred(defer 的意义),排除 disabled/absent/已减/未授,否则泄露不可调工具 + 模型反复试 → 故 `search_tools` 是 resolver 又一读点。描述随 tool_result 留历史,不维护已发现集(被压缩则模型见索引行自己再 search,详见决策 2)。注册进 `dependencies.py:_load_tools()`,纯 prompt 级、无 server `tool_reference` 依赖。
- **provider 抽象(F 的地基,B 建好)**:给 tool 模型加 `source`/`provider` flag(仿 CC `isMcp`);registry 归一化所有来源到一个 tool 形 + 合并函数。这样「MCP server = 又一个 deferred tool-set provider、`<server>__<tool>` 命名(`__` 唯一分隔、无 `mcp__` 前缀,决策 11)、走同一 `search_tools`」在 F 是纯加法。B 须按 MCP 的形状把缝留对(命名空间、按 server 名搜、动态 set),F 才接得干净。
- **部门作用域 wire-in 的边界(消费全留 G)**:tool-set 将来接入 `department_resource_rule`(`resource_type=toolset`),方向 = 规则行显式 `effect`(tool-set `visibility` 只定未列部门默认,决策 10)。**B 只负责两件准备**:① tool-set DB 模型带 `visibility` 列(随本轮 DB 化,非 config frontmatter)、② 让 tool-set 有稳定 unit name 作 `resource_id`。**部门规则的实际消费(查表 + 树解析 + 进 resolver)全在 G**(规则表 C 才建、dept 输入层 G 才加,见 line 101 分阶段输入);**B 的 resolver 骨架输入只有静态两样、不认部门规则**(修正:原写"resolver 减法认 grant"与 line 101 矛盾)。授权 UI 归 G。

**到时再敲定**:tool-set DB schema + `config/tools/` 种子加载细节;**两个待显式过的语意**(memory `tool-ecosystem-positioning` 已标):① 历史中 tool_result 引用了「描述已随压缩掉出 context」的工具时的提示文案(自纠靠模型重 search,但要确保索引行点明可重取);② `always_allow` 跨 set 同名工具语意。

### C — Skill 核心(存储 + L1 注入 + `read_skill` 工具 + 权限/上下文覆盖)

**做什么**:落地标准 Agent Skills 的**纯 prompt 修饰器**部分(不含 bundle 执行,那归 D)—— 可见性存储(决策 1)、L1 索引常驻(ContextManager)、L2 `read_skill` 返回 `skill_md`、`allowed-tools`→权限覆盖;**并落 `department_resource_rule` 表 + 祖先链解析地基**(决策 10,skill 是首个消费者,tool/toolset/mcp 接入 + UI 归 G)。**C/D 阶段线 = 有没有 bundle**:单 SKILL.md(无 bundle,如 consolidate-memory)C 完即可用、不碰沙盒;**有 bundle(references 或 scripts)的等 D**(bundle 任何内容都走沙盒读,见决策 4 判别线)。

**包含**:
- **`Skill` 表 + 覆盖表 + grant 地基**(决策 1/3/10):**消费列** `slug`/`owner_user_id`(shared 为 null)/`visibility`(private/public/department)/`default_enabled`/`name`/`description`(`when_to_use` 折进)/`allowed_tools`/`compatibility` + **`metadata` JSON**(`license`/`version`/未知扩展等系统不消费字段,决策 3/9)+ **`skill_md` TEXT**(全文,L2 读)+ **`bundle` BLOB**(完整原始 zip 含 SKILL.md,L3/导出);`user_skill(user_id, skill_id, enabled)` 稀疏覆盖;**`department_resource_rule(department_id, resource_type, resource_id, effect)`**(`effect ∈ {grant,deny}` 显式存、非派生,决策 10;此阶段先建表 + 祖先链解析,skill 用 `resource_type=skill`)。Repo/Manager/Router 三层(skill 非 session-scoped,Manager 做 ownership/可见性/序列化)。
- **API + 前端**:CRUD + 可见性/启用 toggle 端点;前端 = 设置/管理页(非对话流内),admin 管 public(preinstalled/marketplace)、user 管 private + opt-in 链接。admin scope 守 `feedback-admin-scope-user-mgmt`(管共享资源,不碰用户数据)。部门 grant 的授权 UI 归 G。
- **L1 注入(ContextManager 职责)**:`<available_skills>`(name + description),每 turn 全量渲染(**无预算闸 —— 见原则 7**;索引大小靠 operator 策展约束,非运行时截断)。接入 `ContextManager.build()`,仿现 `_build_available_subagents`/artifact inventory 模板。**全 agent 可见 skill(去 agent 维度,改前述 lead-only)**:skill 可见度只走 user/dept visibility 轴、**不分 agent** —— 安全由「skill 的 `allowed-tools` 只能在该 agent 自己的、且按该 user 部门 **dept 收窄后**的工具宇宙(`agent_unit`)内 enable」兜住,故 skill 全局可见而**能力效果按 agent 宇宙 + 部门双重收窄**(判别:工具=能力需 per-agent 范围,skill=软引导无需,见决策 11)。`disable-model-invocation` 的从索引隐藏。
- **L2 = `read_skill(slug)` 工具**(决策 4):独立工具、镜像 read_artifact 契约(`inf` 不二次 persist、`AUTO`),**死简单——返回 `skill_md` 全文、无 path 参数、后端不解 zip**;输出附「其余细节(含 references)须 mount 进沙盒读」提示;**不合并进 read_artifact**(身份空间/store/Manager 不同)。用户 `/skill` = 等价入口(同走 read_skill 取正文注入,多一个 UI 触发)。激活无副作用、非状态切换(原则 8)。
- **`allowed-tools`→成员轴 B 的 enable-only(决策 11)**:
  - **激活持久化**:slug 加进 `state["active_skills"]`,照抄 `always_allowed_tools`(回合末写 `Message.metadata`、父消息捞回,`controller.py:176/529`,不扫历史)。resolver 据此**在 dept 收窄后宇宙内**翻开 skill 点名的 `disabled` unit(不引入宇宙外/dept-denied unit、不碰等级)。
  - **条目→unit 解析 = 纯 exact match、无模糊**(reviewer P2,import + runtime 共用一个函数):① exact-match 已注册 unit 名 → 该 unit;② 否则 exact-match 已注册全名 `<unit>__<tool>`(`<unit>` 须已知,按 unit 名前缀、不 split `__` 反推)→ 归属该 unit;③ 裸成员名(无 `<known-unit>__` 前缀、又非 unit 名)不接受 → import warn / runtime 忽略。**`search` ≠ `github__search`**(不同 key,裸 `search` 永不命中 set 成员;多 unit 重名 `search`/`create` 时防启错整 unit)。命中后 enable 整 unit。
  - **resolver 只加一个输入层 `active_skills`**(骨架 B 已立),不重碰 4 读点;dept/MCP/UI 同理各阶段加层。与 `always_allowed_tools`(等级轴)两条独立轴。
  - **import vs runtime 校验基准**:skill 全 agent 可见、不绑 agent,故 import 期无 user/dept/agent → 只把条目解析到 unit、对全局 ceiling 校验 unit 存在,可选 warn「当前无 agent 挂载此 unit」(`agent_unit` 后续可挂、非永久悬空);**别在 import 期做 dept 收窄**。runtime enable 才在「具体 agent × user 部门收窄后」生效。
  - 留待将来(memory 已标):① 历史 tool_result 引用已不可见工具的状态文案;② `allowed-tools` 点名宇宙外工具 → 忽略 + import warn。
- **config 种子**:`config/skills/<name>/` 启动时 zip→完整 blob + 解析索引列/skill_md→upsert 进 DB(`visibility=public`、`default_enabled=true`、owner=null),内容哈希幂等防重(决策 3)。

**到时再敲定**:frontmatter 精确字段子集(v0 = `name`/`description`/`allowed-tools`?);skill 与 B 的 tool-set 是否联动(**开放分叉**:CC/opencode 完全分离;我方**可选**让 skill 的 `allowed-tools` 调用时 auto-`search_tools` 预载其 set —— v0 倾向保持分离、再议)。

### D — Skill bundle 执行(L3 挂载进沙盒 + `compatibility` 依赖三层 + 离线 wheel)

**做什么**:让带 bundle 的 skill(docx/pptx/xlsx/pdf 类)真能用 —— 模型按需把完整 bundle mount 进沙盒(复用现有 `mount`)、bash `cat` 读 references / `python` 跑 scripts;依赖走原则 4 三层离线投递。这是「沙盒先于 skill」的兑现点。**bundle 里一切(references + scripts)都在沙盒读**(决策 4 判别线:SKILL.md 走读通路、bundle 走沙盒)。

**包含**:
- **bundle 经现有 `mount` 进沙盒(复用,非新机制)**:扩 `mount` 让它能引用一个 skill 的 bundle(类比引用 artifact id),解**完整 zip**(含 SKILL.md/references/scripts/assets)到**固定约定路径** `/workspace/.skills/<slug>/`。`${SKILL_DIR}` 因路径由 slug 确定**在 read_skill 时即可解**(替成约定路径),mount 返回真实路径作运行时确认 —— **占位符问题关掉**(决策 4)。**模型驱动、per-turn**(决策 5/原则 8):`read_skill` 提示"其余去沙盒读",模型那轮自己调 mount;**不 auto-mount、不跨轮维持**,受沙盒 ephemeral 纪律 + `<sandbox_status>` + loud-fail 管。
- **依赖三层兑现**(原则 4 / 决策 6):① 通用重栈进沙盒镜像(pandoc/libreoffice/poppler/qpdf + 科学栈);② 离线 wheel bundle 常驻 extras;③ skill asset `wheels/`(长尾兜底)= **模型在沙盒里按需 `pip install --no-index --find-links` 离线装、非系统自动**(守原则 8,同 `cat`/`grep` 词表自然消解)。**`compatibility` = 声明式提示、非触发器**:告诉模型「需要什么 + 装不到用 `wheels/`」,模型 probe 环境、不满足才装;`ImportError` loud-fail → 模型补装。前两层已覆盖绝大多数 → 第三层触发频率低,auto 无条件装 = 反向重机制。
- **典型闭环跑通**:用户传 docx → artifact(blob)→ mount → skill python 拆/改 OOXML 或 pandoc 转 → persist 回 artifact。这一条同时练 A(可选,数据工具变体)、C-mount、skill 执行。
- **`substitution` 子集**(可选):若存货/预装需要,实现 `$ARGUMENTS`;`` !`cmd` ``/`${SESSION_ID}` 暂不做。

**到时再敲定**:skill bundle 挂载点(`/workspace/.skills/`)与 artifact mount(`/workspace/<id>`)的命名空间隔离;沙盒镜像扩容清单(node?LaTeX?权衡镜像大小);wheel bundle 的 arch 化(沿沙盒 plan per-arch 纪律);`pip install` 离线在沙盒激活期的耗时/失败处理;CPU 纪律(解压炸弹/大 zip,沿沙盒原则)。

### E — 导入门禁与预装(硬门槛 validator + 软门槛 verify agent + pandoc-first 预装)

**做什么**:skill 的不可信输入门禁(原则 2)+ 把用户存货改造成预装集。**last step**(用户:存货预装前先改)。

**包含**(导入单管线两道门,决策 7):
- **硬门槛 = 确定性 validator**(plain code,**阻塞**,跑每次导入含 user 私有上传):借 `skills-ref`/`skill-validator` 规则 —— 6 字段 schema、name↔dir、孤儿文件、未闭合 fence、内部链接解析、SKILL.md 体量(legibility 警告)、`compatibility`-vs-(镜像栈 ∪ 自带 wheel)交叉校验(气隙网:声明需网即拒/标)。bundle 字节上限归上传路由配额闸(原则 7③,按信任分层、admin 无闸),非 validator 项。
- **软门槛 = verify agent**(**可强制通过**):**职责单一 = 判定「能不能在本系统跑」**,懂本系统约束(沙盒工具词表、无网、mount/persist、tool-set 披露)→ 三查:① 用了没有的工具/harness?② 需要装依赖(`compatibility` 声明 vs 镜像栈 ∪ wheel)?③ asset 放好没?**输出诊断报告(能跑/缺什么),不产 rewrite、不改 body**(改造/简化是人的事,见下条预装集简化 + 原则 3)。**用户私有上传时可选/可跳过**(LLM 成本)。其「系统知识」指向**活文档**(本 plan / skill-authoring 参考),不硬编工具名防漂移。
- **预装集简化(人工,非 verify agent)**(决策 8):预装集就 4 个文档 skill,**开发者在 E 阶段手工分析简化** —— 改 pandoc-first、删三份重复 `office/`、补 `compatibility` 声明、补 asset wheel(若长尾依赖);skill-creator 改造(去 CC widget/subagent-fork 假设)或暂不预装;schedule/setup-cowork **不移植**(驱动 CC 产品 widget)。改完进 `config/skills/`。verify agent 在此只回答「改完这版能不能跑」,**不替人做简化**。

**到时再敲定**:validator 借哪些具体规则、阈值;verify agent 自身是不是个预装 skill(自举);预装集最终名单;ZIP 导入导出端点(决策 6)。

### F — MCP client(把 MCP server 接成又一个 deferred tool-set provider)

**做什么**:落地 MCP 客户端,让系统能对接只暴露 MCP 接口的内网系统。**架构上 B 已把难的那半(registry/披露)铺好** —— F 是 B 的 provider 抽象的第一个外部消费者(自定义 HTTP 工具/tool-set 是内部消费者),MCP 工具 = 又一个 deferred tool-set,走同一 `search_tools`,不重做工具系统。reference 实现 = `../opencode/packages/opencode/src/mcp/index.ts`(本仓库同级,非项目内;provider-agnostic、无 server 依赖,比 CC 更贴我方)。

**包含**:
- **传输 + 协议客户端**(净新,体力活):用 Python 官方 MCP SDK 接 stdio(`StdioClientTransport` 等价)+ http/sse;JSON-RPC 握手、`tools/list`/`tools/call`、`list_changed` 动态刷新、连接生命周期/重连。照 opencode 那套搬。
- **JSON-Schema → 我方 XML 工具描述适配器**(我方特有的一道):MCP 工具的 `inputSchema`(JSON Schema)+ description 渲染成我方 prompt 级 XML 工具描述 —— CC/opencode 把 JSON schema 直接交原生 function-calling,我方是 XML,故须这层。`callTool` 包成我方 `BaseTool.execute` 形。**外部 tool 名 sanitize(reviewer P2)**:`tools/list` 返回的 tool 名是**外部输入**、可能含 `__`/非法字符,撞我方 `<unit>__<tool>` 单分隔规则(server 名 operator 可校验、tool 名控不住)→ 适配器做**确定性可逆 sanitize**(清洗成合法 `<tool>` 段给模型看 + 注册,F 保留原名→`tools/call` 时映射回真名),loud-log 清洗动作。保住工具不丢,优于拒整个 server(太狠)或跳过该 tool(丢能力)。**清洗须 `(server, sanitized_name)` injective**(reviewer P2):两个外部 tool 名清洗后撞同一内部名 → **fail-fast**(拒该 server 加载、要 operator 介入),绝不让两工具映射到一个名(否则 `tools/call` 路由歧义)。
- **接入 provider 抽象**(B 的回报,近乎免费):打 `source="mcp"` flag、`<server>__<tool>` 命名(`__` 唯一分隔、不带 `mcp__` 前缀,决策 11;server 名跨 provider 全局唯一、撞名 loud-fail)进统一 registry;整 server = 一个 deferred tool-set,索引行常驻、`search_tools` 按 server 名搜加载。**MCP 工具太多的披露问题在 B 落地即解决,F 不再碰**。
- **server 配置(DB + 前端)**:MCP server 随本轮 DB 化 = **DB 实体 + 前端 CRUD**(决策 5/10:operator 出厂 server = `config/mcp/` 启动种子进 DB、不可变;UI 新建 = DB 原生),带 `visibility` DB 列;per-deployment;无联网发现(Non-goal)。**凭证仍留 env**(`{{TOOL_SECRET_*}}`,见 line 160),DB 只存 server 定义。
- **部门作用域 wire-in(G 的消费者)**:MCP server 接入 `department_resource_rule`(`resource_type=mcp`),方向 = 规则行显式 `effect`(server `visibility` 只定未列出部门的默认,决策 10;**server 的 `visibility` 是 DB 列**,随本轮 DB 化)。整 server 单元粒度(声明/减/加/enable 都在 server 粒度)= 决策 11 解 MCP 动态工具的同一抓手。消费(查表 + 树解析)在 G。
- **动态工具的权限粒度**(决策 11 已定调):MCP 工具动态、没法逐个静态枚举 → 权限锚在 **server 单元**(agent MD 静态声明 server 单元、m2m 减整个 server、运行时发现的工具填进**已授权单元**),即 `<server>__*` 整组粒度、**不逐工具**。比 memory `permission-param-granularity-direction`(工具名+参数模式、仿 CC allow-rules)粗一层:server 粒度够 v0,更细留将来。**关键(reviewer P1#2)**:`resource_type=tool` **不覆盖 MCP 成员** —— **不把发现的 MCP 工具持久化成 DB 行**(否则复活已杀的「已发现集状态机」+ 引入「哪个容器的 `tools/list` 权威」HA 真相问题);MCP 的 dept 控制只在 `resource_type=mcp` server 粒度,逐 MCP 工具 defer。

**到时再敲定**:MCP server DB schema + `config/mcp/` 种子加载细节(落点已定 = DB + 前端,本轮 DB 化);stdio vs http 在内网的取舍(DooD/容器内进程 vs 网络服务);权限 set 粒度的具体语法(与 B 的 `always_allow` 跨 set 语意对齐);`list_changed` 刷新后历史里旧 tool 描述如何过期/自纠(**无「已发现集」状态机**,决策 2;靠常驻索引行变化 + 模型重 `search_tools` 自纠);凭证注入(同 HttpTool 的 `{{TOOL_SECRET_*}}` env 模板,operator 级密钥;**用户级身份透传是另一根轴、推迟**,见 Non-goals)。

### G — 部门作用域授权 + 管理 UI(横切:skill/tool-set/mcp/tool 四类一套机制)

**做什么**:把"哪个部门能用哪些资源"做成**一套统一的部门作用域授权层**(决策 10),服务 skill/tool-set/mcp/tool 四类;agent MD 不动,引擎运行时组合有效集。这是用户要的"admin 给指定部门配特定工具"——**靠作用域涌现,不克隆 agent**。

**包含**:
- **统一 `department_resource_rule` 表 + 解析**(地基随 C 落):`(department_id, resource_type, resource_id, effect)` polymorphic(决策 10 已论证为何一张表对而非将就);判定 = 用户 `department_id` 走 `parent_id` 祖先链 ∩ 规则集(指父覆盖子树;命中后方向 = 行显式 `effect`,未命中走单元 `visibility` 默认),解析与已加载资源取交集(陈旧规则自然成空)。
- **`EffectiveToolset` resolver:G 只加 dept 规则输入(骨架在 B、决策 11)**:骨架 B 已立、C 加 `active_skills`、F 加 MCP 运行时,G 只追加最后一个输入层 `dept 规则`(dept-on-tools),不重碰读点。
  - **「G 是最后接入的实现阶段输入」≠「运行时应用次序」**(reviewer P1):运行时序固定 = universe 展开 → dept 收窄(移除 user 部门未授权 unit、删 key)→ skill enable,**dept 收窄在 skill enable 之前** → private skill 翻不开 dept-denied。输入集合 `(agent_config, active_skills, registry+MCP, dept 规则)` 是列举非次序。
  - **dept 是 resolver 唯一双向(grant/deny)输入**(C/F 都是 enable/填充),在单元展开后、skill enable 之前应用、方向 = 行显式 `effect`。
  - **工具侧一律 unit 粒度**:`resource_id` = unit 名,`toolset`/`mcp` = `<unit>__*` 整组,`tool` = 裸名;`skill` 行按整 skill 授权。一条「按 unit 名匹配整组」路径,无点名 set 成员 → 永不产生 set-vs-member 冲突/override/检测(删 resolver 的成员名 exact-match 分支)。
- **四类接入**:skill(C,`visibility=department` 消费规则)、tool-set(B wire-in)、mcp(F wire-in)、**tool(独立 external 工具 = 自成 unit,一等 DB 行、grant/deny 皆可,unit 存在即可消费;**非** tool-set 成员 —— member 粒度已与 MCP 对齐删除,决策 10)**;builtin 不接入(for-everyone,决策 10/11);参数级粒度(工具名+参数模式)留将来(决策 10)。
- **管理 UI**:admin 按部门分配资源(grant 增删)+ 看每部门有效能力;前端复用 skill 管理页骨架(决策 1 的设置/管理页),按 `resource_type` 分 tab。admin scope 守 `feedback-admin-scope-user-mgmt`(管共享资源/授权,不碰用户数据)。
- **规则体检 check 端点(admin 只读审计)**:扫 `department_resource_rule` 报两类卫生问题——① **孤儿**(`resource_id` 已不存在,app-side cascade 兜底后应极罕见);② **冗余/失效**(行 `effect` == 资源 `visibility` 默认方向 = visibility 漂移后的 dead row;四类都有 DB visibility 列、一视同仁)。**`effect` 显式列是前提**(方向派生时永远"自洽"、测不出漂移)。**只读非门禁**(正确性已由 effect 列 + cascade 按构造保住),清理是独立 admin 动作。冗余判断绑定 v0 no-override 模型,将来启用「更具体规则胜」须同步改。

**到时再敲定**:**grant 删除同步落点**(决策 10:**全 DB 后统一 app-side cascade** —— 删 skill/external-tool/tool-set/mcp 的 Manager 都同事务 cascade `department_resource_rule`、绑业务事件、DB 单一真相;原"config 类系统不碰 m2m + operator 运维契约"已作废;实现随各资源 Manager + UI 孤儿提示随 C/G;ABA-id 根治是否做);引擎"组合有效集"的缓存粒度(部门/用户级,资源变更少、可缓存);UI 是否支持"部门继承"可视化(指父覆盖子的展示);**参数级粒度**(工具名+参数模式、CC allow-rules 式,需调用时 arg 匹配)留将来(`permission-param-granularity-direction`;**unit 整体** grant/deny 已在 v0 = `resource_type ∈ {tool 独立工具, toolset, mcp}`,unit 之下的逐工具/参数粒度才是将来);**树规则 override 优先级**(深层更具体部门能否翻案祖先规则)—— v0 不做、祖先一刀覆盖整子树(决策 10"指父覆盖子树"),将来要"二级禁/某三级特批"再引入"最具体规则胜"。

## 关键风险

- **披露与 skill 职责漂移**(B/C)—— 一旦把披露塞进 skill,就回到「动态工具对小模型 legibility 负资产」的老坑。守原则 1:披露是工具层、skill 是覆盖层,review 时盯死边界。
- **依赖气隙地雷**(D)—— 社区 skill 默认运行时联网 `pip install`,搬进气隙网必断。`compatibility` 声明 + validator 交叉校验是唯一防线;存货零 manifest = 预装集须人工补全依赖声明。
- **bundle = 新不可信输入类**(E)—— skill 脚本在沙盒跑(隔离 OK),但「哪些 skill 准入」是 trust 决策,生态无 signing 层 → 门禁(确定性 validator + verify agent 可运行性评估 + 人审/人手改)是我方唯一 trust 边界,不可省。
- **body 静默改写诱惑**(C/E)—— 工具词表对不齐时,regex rewrite body 看似省事实则脆(改坏比标记坏)。守原则 3:lint 标记 + 人审,绝不运行时静默改。
- **`search_tools` 无 server `tool_reference`**(B)—— 我方纯 prompt 级模拟。**不维护「已发现集」状态机**(决策 2 / changelog 06-17;修正旧表述"追踪须自建"误导实现者造状态机):描述随 tool_result 留历史、被压缩则模型见索引行自己再 search(自纠);避开 server beta 依赖、也不引入状态管理。
- **沙盒镜像膨胀**(D)—— pandoc/libreoffice/科学栈 + 可能 node 烤进镜像,镜像大小与构建时间。权衡「通用即烤、长尾走 wheel」,别把长尾也烤进去。
- **MCP server 粒度授权偏粗**(F/G)—— 决策 11 已定调把 MCP 当 **server 单元**(声明/减/enable 整组、resolver 展开),解了"动态工具无法静态枚举"。**残留风险 = 粒度只到整 server**:授/减一个 server = 它所有工具一起动,**没法逐工具选**(逐工具静态列举对动态发现不可能);更细的工具名+参数模式授权(`permission-param-granularity-direction`)留将来。**与 skill/部门同根**:三方都要引擎从"静态 MD dict 查"改"resolver 解析"——统一解 = 决策 10/11 的 `EffectiveToolset` resolver(**骨架 B 立、C/F/G 各加一个输入层、读点只收口一次**,见 Phase B),别三处各修读点。
- **MCP server = backend 侧网络/凭证边界**(F)—— MCP client 跑在受信 backend(非沙盒),http MCP server 是真实出网 + 凭证持有点,与沙盒「全禁网」正交。须按现有 web 工具的出网纪律(凭证 env 模板、operator 显式配、内网链路审计)管,别让 MCP 成为绕过沙盒网络边界的后门。

## 变更日志

- 2026-06-16 **起草**。三件前置调研(CC 工具披露+MCP、agentskills.io 标准、用户存货)后锁 9 决策 + 7 原则。核心 = 原则 1「披露归工具层、skill 正交」(CC 实证 `SkillTool` 零引用 deferral)。5 阶段 A–E。
- 2026-06-16 **分支策略走 main**:本 plan 无破坏性中间态(不同沙盒 plan 的 md→Word 半迁移),纯加法逐阶段合 main。
- 2026-06-16 **激活机制收敛**(opencode 调研):印证激活 = 返回正文的 tool_result、非状态切换,唯一副作用改权限。据此 L2=独立 `read_skill`、L1 归 ContextManager、bundle 走现有 `mount` 模型驱动 per-turn(删 auto-mount + 跨轮一致性)。新增**原则 8**:激活退化成普通 read+mount、零新机制。
- 2026-06-16 **加 F 阶段 MCP client**:opencode 证明 provider-agnostic 世界无 30-endpoint 披露现成解 → 我方 B 补此,MCP = 又一个 deferred tool-set provider(架构层近乎免费、剩体力活)。
- 2026-06-17 **存储/读取/导入收敛**:存储 = blob 真相源 + 反规范化列(无损导出靠 construction);判别线 = SKILL.md 走读通路 / bundle 一切走沙盒 → C/D 阶段线 = 有没有 bundle;mount 用固定约定路径关掉占位符问题;导入 = 单管线硬门槛(validator)+ 软门槛(verify,可跳过)。
- 2026-06-17 **部门授权入主线**(新 G + 决策 10 + 决策 1 重写):解「按部门定制能力」—— **不克隆 agent**,靠作用域涌现;一套 polymorphic `department_resource_rule` 服务四类。决策 1 三 scope 拆成 `visibility` + `default_enabled` 两正交字段。推迟:用户凭证透传(红线=沙盒永不拿凭证)、运行时可编辑 agent。
- 2026-06-17 **skill 能力持有 = 复用 `always_allowed_tools` 持久化**(`state["active_skills"]`,不扫历史):纠正「无跨 turn 状态」误判(始终允许本就跨 turn)。避免的坑 = 派生版让压缩静默撤权 → 独立持有让能力轴不被压缩误伤。**原则 7 收缩**:私有化无 tokenizer → 删调用前 token/索引预算,唯一硬上限 = bundle 字节按信任分层。
- 2026-06-17 **工具权限模型定稿**(新决策 11):两正交轴(等级=工具定义不可改 / 成员=enable-only)+ 四层链 + `EffectiveToolset` resolver。避免的坑 = `disabled` 误塞进等级枚举(实为独立成员轴)、confirm 洗 auto(全链无人改等级 → 按构造消失)。迁移≈零(现有 `name:auto/confirm` 按 enabled 读)。
- 2026-06-17 **`search_tools` 跨 turn 靠历史、非追踪状态**:发现是上下文问题(家在历史)、`active_skills` 是权限问题(要 metadata-state)—— 看似同类、结论相反。避免造「已发现集状态机」。
- 2026-06-17 **tool-set 命名 `<setname>__<tool>`**:核心理由不是防撞名,是让 unit 粒度授权按前缀 `<unit>__*` 落地。用 `__` 不用 `:`(`_repair_tool_name_as_tag` 把名当 XML 标签、冒号崩)。
- 2026-06-18 **披露两层定调 + tool-set↔MCP 同型**:MCP `tools/list` 扁平返全部、无原生渐进发现 → unit 是授权边界非 discovery 边界,不做 `list_members` 第三层。
- 2026-06-18 **依赖第三层 = 模型驱动按需装、非激活自动**:auto pip 是沙盒副作用(撞原则 8)+ 重机制回潮;`compatibility` = 声明式提示、非触发器。
- 2026-06-18 **verify agent 职责收窄 = 纯「能不能跑」评估**:纠正我把它拔高成会 rewrite 的 adapter 之误。改造/简化归人(原则 3 绝不静默 rewrite)。
- 2026-06-18 **6 字段全 DB 落位、按消费分流**:澄清「v0 最小」≠ 只解析两字段;系统不消费的归一个 `metadata` JSON 列,不改原 frontmatter 结构(挪字段=silent rewrite)。
- 2026-06-18 **config 类孤儿管理:5 轮打补丁后退回 scope**(step-back design-creep)。软引用无 DB CASCADE → 试 reconcile-delete(HA 滚动更新各副本视图不一致、破坏性删基于局部视图)→ 试持久化 orphan flag(同陷阱变体)→ 试读时实时算(admin 请求打到哪个容器抖动)→ 戳破:config 类源 = 各容器内存 registry、物理无单一真相。**定调**:系统只对 skill(DB 实体)做同事务 cascade;config 类系统侧**零机制**= operator 运维契约,功能正确性靠解析 `∩ 已加载` 兜底(悬空无害)。〔注:此段整体被 06-19 DB 化作废 —— 全 DB 后统一 app-side cascade。〕
- 2026-06-18 **Phase A 首个数据工具改 web_fetch 文件旁路**(替假想 HTTP 工具):零新工具 + 补真实缺口(jina 对 PDF/二进制本就坏)+ 端到端串 A→mount→D。路由用 URL 尾缀非 Content-Type(核实代码:现状本就用尾缀、避开「解 content-type 卡顿」坑);persist = 内建工具运行时自决。**SSRF**:旁路在 Jina 前会绕过 `_fetch_single_url:202` 的 `validate_public_url` → 旁路直连前必须自校验 + `allow_redirects=False`。
- 2026-06-18 **工具披露走 CC 激进路线 + tool list 挪动态 reminder**:索引行 = set desc + 成员光名字(完整描述 `search_tools` 按需补);tool list 从 system prompt 前缀挪进 reminder,避免会话内动态时每变一次打掉整段 prompt cache(grammar 前缀留住、只挪 catalog)。L1 索引 name 全注入、删折叠机制(撑爆靠 agent MD `disabled` + skill enable);`defer` 改显式声明(`defer:true`)、不按 token 自动(无 tokenizer)。
- 2026-06-18 **resolver 骨架从 C 提前到 B**:resolver 是纯基础设施,收口拖到 C/G 则同 4 读点被 refactor 三轮(退回架构信号)→ B 立骨架、之后每阶段只加一个输入层。tool-set 单元展开是 B-native(静态)、非 G 泛化项。
- 2026-06-18 **部门授权方向归单元 `visibility`、表中性化**(收敛过 deny↔grant↔visibility 三轮):试「整表 deny」「整表 grant」都逼出枚举全树 → 关键洞察(用户)= 工具缺 visibility 字段,resolver 无从知一行是加是减。定:visibility 升为四类共享字段定默认、行定例外,各方向只需 1 行。〔注:visibility-derived 方向于 06-19 被显式 `effect` 列取代。〕
- 2026-06-19 **reviewer 1–2 轮(docs 一致性 + 边界,逐条拍)**:要点 = ① **P0 私有 skill 提权**降级为明示边界、不加机制(只能翻 `disabled`、敏感靠 `confirm` 兜底 → 无法静默提权,verify 非权限边界);② **MCP 命名两次反转**最终定 `<unit>__<tool>` 无 `mcp__` 前缀(`mcp__server__tool` 两个 `__` 撑破 `<unit>__*` 通配),撞名靠 registry 启动 loud-fail;③ **`search_tools` 必须过滤到可调集**(否则泄露不可调工具)= resolver 第 5 读点;④ **grammar 不降权**(拆 `generate_tool_instruction` 两段);⑤ **web_fetch SSRF** 自校验 + `allow_redirects=False`;⑥ `private` 不进 dept 表;⑦ 成员态 schema(`disabled` 唯一禁用值、未知值 loud-fail)。
- 2026-06-19 **规则方向改存显式 `effect` 列**(取代 visibility 派生,reviewer P1 三轮):派生态下改 visibility 会静默翻转既有规则行、反授权。退回架构 = 存显式 effect,改 visibility 只动默认、永不翻已有行(DB/config 一视同仁,比 snapshot/mirror 都轻)。解锁 admin 只读 check 端点(派生方向永远「自洽」、测不出漂移)。附带:MCP tool 名 sanitize 须 `(server,name)` injective、撞名 fail-fast。
- 2026-06-19 **架构退步:external 工具/tool-set/mcp 全 DB 化**(step-back,溶解 reviewer 五轮在 config-vs-DB 双轨上的补丁)。根因 = `tool` 退化（无 visibility）只因背后是 config、拿不到 DB 字段;reviewer 在让我为退化情况反复补特例(deny-only/check invalid/HA 分层)= 同形 bug 反复 → 退回架构。**定调**:仿 skill 把 external 工具也 DB 化(config 仅种子),builtin = for-everyone 不入表。**按构造溶解三条**:`tool` 不再退化、check 端点无须特判、config-vs-DB 双轨连根拔(软引用同步/运维契约/HA 分层整段消解 → 统一 app-side cascade)。
- 2026-06-19 **通用 `config-seed→DB reconciler` + agent 物化进 DB**(用户拍 B):「种子→DB」不该三类各写一遍 → 抽成通用横切底座(只 ingest 通用、消费侧各走各)。agent 物化只为统一存储 + 撞名检查 + 将来零迁移,config 仍唯一作者真相。**注册表 = 每 turn 一次 DB 快照**(用户纠正我的「长缓存 + CRUD 失效」):避跨 worker 失效 + 保 turn 内一致。
- 2026-06-19 **reconciler 落地 = entrypoint leader 槽**(Explore 核实:复用 migration 的 PG advisory lock)+ 增量覆盖语义(per-unit 哈希、就地 UPDATE 保 id、prune+cascade)。运维零新增步骤(既有 pause/resume recreate 自动重跑)。admin 自动化 = plan 外待办。
- 2026-06-19 **reviewer 五轮(架构层)**:**P1#1**(用户点破「种子→db 不优雅」)= 冻结的 agent MD 天花板容不下 DB-native 运行时工具 → 新增 **`agent_unit` m2m** 作 agent 宇宙,统一静态(reconciler 种 seeded)+ 动态(UI 挂 dynamic),主流注册表+绑定+grant 解析模式。**skill 去 agent 维度**(全 agent 可见、效果按宇宙收窄)。**P1#2** = MCP 只 server 粒度、不持久化发现的工具(避复活已发现集状态机 + HA 真相问题)。**P2** = C 依赖 B。新增「数据模型总览」ER section。
- 2026-06-19 **dept 授权模型收紧:从「末端 AND 闸」改「dept 收窄宇宙本身」**(用户拍)。AND 闸结果对但模型脏(让即将被移除的工具还带成员态去 intersect)→ 正确心智 = dept `deny` 直接把 key 从宇宙移除,skill 物理够不到、绕不过部门授权是 by-construction。应用序 = ②宇宙 → ③dept 收窄 → ④skill enable。〔此前后几条 docs-only 收尾(agent_unit 旧措辞、授权顺序安全 bug、模型传播到 C/G、import 校验基准、原则 8 对齐)均为把此模型铺到各角落,不再单列。〕
- 2026-06-19 **dept 授权对齐到 unit 粒度**(tool-set 跟 MCP 看齐,用户拍):原 `tool` 能点名 set 单个成员 = 授权层唯一跨粒度规则,也是 reviewer P1a 冲突/P1b/override/check 互斥整套机器的唯一来源 → 删。代价 = 切 set 子集需拆 set(per-dept 子集本不该在 agent 授权层)。`feedback-step-back-on-design-creep`。
- 2026-06-20 **粒度统一收口 = unit-everywhere**(reviewer P2):d592 只收 dept,skill enable/agent MD 声明仍 per-tool → 三处不一致。定调 = 所有成员操作一律 unit 粒度、一套 match 无第二语义;**builtin = singleton unit**(对 builtin 即逐工具、标准 allowed-tools 原样工作),等级仍唯一 per-tool。
- 2026-06-20 **补确定性 name-resolution 规则**(用户「命中啥是啥」):`allowed-tools` 条目→unit = 纯 exact match(unit 名 / 全名 `<unit>__<tool>` / 裸成员名不接受),`search` ≠ `github__search` 防重名启错整 unit;import + runtime 共用同一函数。
- 2026-06-20 **可读性瘦身**(用户提:决策/原则臃肿、日志该记 why 不复述正文):加「写作约定」(一句话主张 + 子点、论证只讲一次、war story 进日志);原则 5/7/8、决策 10/11、阶段长 bullet 全部拆子点;变更日志按「why + 解决/避免什么」大幅压缩、合并多轮 docs-only 收尾。只删冗余,决策/约束/安全项一字不丢。
- 2026-06-20 **补 `agent_unit` dynamic 绑定的阶段归属(reviewer P2,真 gap)+ 术语小修**:数据模型/决策 11 都说 UI 写 `dynamic` agent_unit,但没有任何阶段落这个入口 → UI 新建工具能进 DB、被 dept grant,却挂不到任何 agent 宇宙 = 对所有 agent 永远 `absent`、不可用。**定:绑定 API/UI 归 Phase B**(非 G)—— 它是「让 UI 工具可用 at all」的基础环,dept 是宇宙之上的额外闸;放 G 会让 B→G 间所有 UI 工具不可达。明确它=operator 给 agent 挂能力单元、**非编辑 agent prompt/model**(后者仍 Non-goal),与部门授权正交。**P3**:原则 5「三态」实际只列 seeded/dynamic 两态 → 改「两类来源态」。
<!-- 新日志按日期顺序追加到此行上方 -->
