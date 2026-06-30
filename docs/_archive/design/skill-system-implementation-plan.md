# Skill 系统 + 工具渐进式披露 —— 实施计划

> 状态:规划完成;A 合 main,B 进行中(B-1/B-2/B-3 + B-4 后端[含 reviewer 两轮收口 + 乙2 部署门禁] + B-4 前端 + B-5[退役 turn-long session] + B-6[依赖 CVE]已合 main;B 收尾验收 dev-Mac docker 部分已过,剩真机 nginx LB + 跨副本执行续接)
> 起草:2026-06-16 · 最后更新:2026-06-30
> 前序产物:
> - `sandbox-implementation-plan.md`(本目录)—— 沙盒主线(A/B/C/D 全完成);本 plan 的执行底座,skill 脚本/asset 全部跑在沙盒里。原则 7「依赖三层离线投递」直接被本 plan 的依赖模型继承。
> - `tool-result-artifact-mount.md`(本目录)—— 工具结果溢出转 artifact 的先例(`source` 字段 / 自动命名兜底),本 plan A 阶段是它的「具名一等通道」升级。
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

- **当前**:**A 合 main;B 进行中 —— B-1/B-2/B-3 + B-4 后端已合 main(落地见 Phase B「进展」+ `skill-system-phase-b-design.md`)。** B-3 = deferred 索引行 + `search_tools` + catalog 挪 reminder(grammar 留 system 前缀);**B-4 后端**(凭证统一加密落库 `tool_credential` + Fernet resolver 接进 HttpTool + external 工具 CRUD/agent_unit 挂载/凭证写-only API + provider 缝)两笔合 main。B 内 4 片增量合 main(B-1→B-2→B-3→B-4),**B-4 又切 2 片:后端全先行(已合)+ 前端(待开工)**。架构主分叉已**全部收口** —— 披露归工具层 / 可见性两正交字段 / 依赖三层离线 / 导入硬软双门 / read+mount 零新机制 / external 工具·tool-set·mcp·agent 全 DB 物化(通用 reconciler)/ 部门授权两张 FK 表 / 工具权限两正交轴(等级 + 成员)—— **逐条见「已锁定的决策」+「数据模型总览」,此处不复述**。
- **B-4 后端 reviewer 收口已合**:两轮 reviewer(15 findings + 复审 N1-N4)+ 乙2 部署门禁,共 7 commit(`cc5ca36`→`59d4205`,见变更日志 06-29)。
- **下一步(B 剩余片,按序)**:
  1. **B-4 前端** —— `export_openapi`(已最新)+ `npm run generate-types`;管理页 = 工具 unit 列表/编辑(seeded 只读)+ agent_unit 挂载勾选 + 凭证写-only 配置;`visibility` 列展示但 G 才接 dept 授权 UI。后端 API 已就绪(`/api/v1/admin/tools/*`)。
  2. **B-5 退役 turn-long session** —— controller 改吃 `db_manager` 工厂而非预开整轮 session;snapshot 短 session 读完即关;artifact 读改短 retrying session;credential 退回 lazy(execute 期短 session 读密文解密,替 slice 甲的 eager 预解、顺手消 N1 明文驻留)。消 idle-in-transaction + 连接整轮被占(等 LLM/授权白占、压并发)+ 补 retry 缺口。闸:审所有 bound-session 消费者无 loop 中段直读 + 无 ORM 逃逸。
  3. **B-6 依赖 CVE** —— starlette / python-multipart / pydantic-settings 已知漏洞升级(B-4 时明确未并入)+ 按 DEP-02 重生成 `requirements.lock` + CVE 复审。
  4. **B 收尾验收** —— 开发 Mac docker `--scale backend=2`(intranet compose + `--profile infra`,本地 build 镜像)跑 `deploy/MULTI-REPLICA.md` 清单(尤其 nginx 变量 proxy_pass 是否真轮询、单副本不回归、Redis 跨副本 cancel)+ tool unit CRUD/管理端到端 smoke(建 unit→挂 agent→配凭证→该 http 工具真跑通)。
- **B 残留延后**(非阻塞):#7-full 抽共享 builder(seeds↔manager,本轮只对齐漂移)/ #11 `list_units` 冷路径 N+1 / #8 `provider!=http` 成员 advertise-but-build → 归 F(MCP)。
- **分支策略(已定:走 main)**:与沙盒 plan 不同 —— 沙盒走 `feat/sandbox` 不增量合 main 是因为有「半迁移态(md→Word 过渡)漏到生产」的风险。本 plan **无此类破坏性中间态**,A/B/C 是纯加法引擎/存储特性,故**逐阶段直接合 main、再按既有策略 overlay intranet**(遵 `feedback-branch-strategy`),不开长命特性分支。

| 阶段 | 内容 | 状态 |
|---|---|---|
| A | 工具结果→富格式 artifact(`create_from_upload` 的第三调用方) | **已完成**(合 main,落地见 A「进展」) |
| B | 工具渐进式披露(tool-set DB 模型[迁出 config]+ `search_tools` 内建工具;MCP 适配缝) | **进行中**(B-1/B-2/B-3 + B-4 后端合 main;B-4 前端待开工) |
| C | Skill 核心(存储 + L1 注入 + `read_skill` + 权限/上下文覆盖 + 部门授权解析地基) | 未开始 |
| D | Skill bundle 执行(L3 挂载进沙盒 + `compatibility` 依赖三层 + 离线 wheel) | 未开始 |
| E | 导入门禁与预装(硬门槛 validator + 软门槛 verify agent + pandoc-first 预装) | 未开始 |
| F | MCP client(传输/协议客户端 + JSON-Schema→XML 适配 + provider 接入 B 的 deferred 披露) | 未开始 |
| G | 部门作用域授权 + 管理 UI(两张 dept rule 表[skill/unit] + 引擎组合有效集 + skill/toolset/mcp/tool 接入) | 未开始 |

依赖:D 依赖 **C(skill 存储/激活)+ 沙盒底座**;**A 不是 D 的硬依赖**(reviewer P2,消解与 line 24/132 的矛盾)—— D 的典型闭环(上传 docx→artifact→mount→skill)走上传通路、不经 A,只有"backend tool→artifact→skill"(如 DB→CSV→skill)那类场景才依赖 A。C 依赖 B 吗?**依赖**(reviewer P2,修正旧「任意序」)——披露机制本身与 skill 正交(原则 1),但**通用 reconciler 在 B 落地、skill@C 复用它**(原则 5);C 先做就要么重写 seed→DB、要么缺种子,故 C 依赖 B 的 reconciler 基础(顺带 B 的 tool-set 也是 skill 编排散文的常见消费者)。E 是 skill 线的 last step(用户:存货预装前先改)。**F 依赖 B**(MCP = 又一个 deferred tool-set provider,B 的 provider 缝 + `search_tools` 是 F 的披露地基),与 C/D/E 的 skill 线正交、可并行。**G 横切**:dept rule 表 + 祖先链解析地基随 **C** 落(`department_skill_rule` 先,`department_unit_rule` 随 unit wire-in)(skill 是首个消费者、可见性必需),tool/toolset/mcp 接入随 B/F 各自 wire-in,**管理 UI + 引擎有效集组合 + 四类齐活 = G**;故 G 依赖 C(地基)+ B/F(资源类型存在)。A、B 各自独立可先做。

## 目标与范围

给系统增加 **标准 Agent Skills**(场景级 prompt 修饰器 + 沙盒可执行 bundle)、**工具渐进式披露**(让 30-endpoint 平台不再 30 份描述常驻上下文)、**MCP client**(把 MCP server 作为又一个 deferred tool-set provider 接入)与 **部门作用域授权 + 管理 UI**(admin 按部门配 skill/tool/tool-set/mcp,一套统一机制),让社区 skill 尽可能「拿来就用」、内网气隙下离线分发,打通对接只说 MCP 的内网系统的通路,并支持按组织部门分发能力。

**Non-goals(本期明确不做)**:
- **联网 skill registry / market**(skills.sh/skild.sh 等)—— 气隙网不可达,market 降维成 `public`+`default_enabled=false` + 链接动作,全 DB/离线(见决策 1)。
- **`context:fork` ad-hoc 子 agent** —— 我方 subagent 是预定义 agent 非 ad-hoc fork;v0 标为不支持(skill-creator 那类 fork 存货改造时降级)。(注:skill 现已全 agent 可见、非 lead-only,见 Phase C / 决策 11。)
- **substitution 全集**(`$ARGUMENTS`/`` !`cmd` ``/`${SESSION_ID}`)—— **全不做**(纯 chat UI 无命令行式传参口、存货零使用;命令替换还撞原则 8)。正文若出现占位符 = 保留字面量(模型按上下文自解),预装集人工去掉。
- **`paths:` 条件 skill**(touch 文件 glob 激活)—— 我方无文件系统语义(artifact 是句柄非路径),v0 不做。
- **`model:` / `effort:` 模型覆盖** —— 这俩是 CC 私有扩展(不在标准 6 字段)、改的是"激活时切调用模型/思考强度"。**不做**:① 它是状态变更型激活,撞原则 8(`read_skill` 纯读、无副作用);② agent 已拥有自己的模型(MD frontmatter,operator 设定),skill 覆盖 = 越权打架;③ 气隙网模型就几个、lead 本就够强、存货零使用,价值近零。
- **server 端 `tool_reference` beta** —— 我方跑任意 backend(qwen via litellm)+ 自有 XML 工具格式,披露走**纯 prompt 级模拟**(见 B)。
- **联网 MCP registry / 公网 MCP 生态自动发现** —— MCP **client 本身现在在范围内(F 阶段)**,但气隙网够不着公网 server;MCP server 由 operator 显式配置(同自定义工具),不做联网市场/自动发现。
- **skill 版本解析 / per-skill venv** —— 依赖只加不 re-pin(继承沙盒原则 7 护栏)。
- **用户身份/凭证透传给工具(B1 用户字段 + B2 OAuth 金库)** —— 与"能不能用工具"(本 plan 做的部门授权)正交的另一根轴:让工具出站请求带用户身份。B1(注入用户已有字段 `department_id`/`role` 到 HTTP 工具模板)+ B2(per-user OAuth token 金库,act-on-behalf-of)推迟到独立 plan。**红线已记**:即便将来做,**只对受信 backend 工具开,沙盒工具一律拿不到用户凭证**(沙盒原则 7 出网纪律)。
- **运行时可编辑 agent 定义(UI 编辑 + DB-native)** —— **agent 本轮随通用 reconciler 物化进 DB(原则 5),但 config 仍是唯一作者真相、`seeded` 不可变、不经 UI 编辑**(`agents are data` 不破:DB 是物化缓存、非作者面)。**物化后的三档不对称**:skill/tool = 种子 + DB-native 可变 + dept 化;**agent = 种子-only DB 物化**(无 UI-native、无 dept 消费者、无 dept rule 表);builtin = 代码、不入 DB、不入部门表。部门"定制 agent"靠作用域涌现(部门 grant 的 external tool/toolset + 部门可见 skill,决策 10),**不克隆 agent**。仅当部门需要**不同 prompt/model**(非工具)才需可编辑 agent,届时单独 plan(权限编辑面需 audit);届时加一张 `department_agent_rule` FK→agent(agent 已是 DB 行、加表即可,本轮已就绪)。

## 贯穿原则

1. **渐进式披露归工具层,不归 skill —— 两者正交。** (参考实现实证:CC `SkillTool` 零引用 deferral 机制。)披露**机制** = tool-set(分组/披露单元)+ `search_tools`(发现工具);skill = **场景覆盖层**(注入 body 散文 + 权限覆盖),可**引用** tool-set 但不拥有披露。MCP 将来 = 又一个 deferred tool-set **provider**,进同一 registry、走同一 `search_tools`。把「30-endpoint 披露」这个职责钉死在工具层,skill 保持纯覆盖,是本 plan 的架构脊柱。
2. **Skill = 受信 prompt 修饰器 + 不可信 bundle 的二分,执行全归沙盒。** body/frontmatter 是**受信文本**(注入上下文、改权限);scripts/assets 是**不可信代码/数据**(只在 `--network=none` 沙盒里跑,绝不在 backend 执行)。skill bundle 是新的一类不可信输入,沿用沙盒不可信纪律(选品/审核门禁 = E 阶段)。这也是「先落地沙盒才做 skill」的根本原因 —— 沙盒是 skill 执行的底座。
3. **标准对齐优先于自造;body 原样搬,绝不静默改写。** 采 agentskills.io 开放标准(6 字段、文件夹=单元)。三块各自处理:**body = 自然语言、格式无关**,模型读完用 system prompt 教的 XML 格式发起调用,原样搬;**frontmatter = 映射层**(只 `allowed-tools`→权限模型;CC 私有扩展 `model`/`effort`/`context`/`paths` 全不支持 —— 见 Non-goals,撞原则 8);**substitution = 不实现**(纯 chat UI 无传参口,见 Non-goals)。社区 skill 的工具词表耦合(`Read`/`Grep`/`Edit` 等)在沙盒里多自然消解(= `cat`/`grep`/`sed`),残留硬耦合靠 **import lint 标记 + 人手改,绝不静默 rewrite**(脆且错改比标记更坏;verify agent 只标记/评估、不改)。
4. **依赖 ≠ 数据,沿用沙盒原则 7 的三层离线投递。** artifact 是用户拥有的**数据**(mount-in/persist、blob 进 DB);依赖是**执行环境**(① 镜像烤通用栈 / ② 离线 wheel bundle 固定位 / ③ skill 自带 asset)。②③同一套 `pip install --no-index --find-links` 机制、不同生命周期(常驻 vs 随 skill),别造两套。**护栏**:skill bundle 只做加法、不 re-pin 基础栈版本(否则一 turn 多 skill 版本冲突逼出版本解析机器,合「fix 复杂度超 feature 价值即退回 scope」)。标准的 `compatibility` 字段 = 声明层,导入时据此校验「需要的镜像没有且 asset 没带」→ 标记/拒。
5. **skill/tool/agent 是跨会话的定义资源:真相源是 config、DB 只是物化缓存,三类同构、共用一条种子→DB 通路。** 判断框:凡 user/系统-scoped 跨会话的定义(非 session-scoped artifact),都走「config 作者真相 + DB 物化缓存」,且不为它单独写 loader —— 这就是为什么 skill/tool/agent 共一个 reconciler、不各造一套。
   - **两类来源态**:`seeded`(config 种子、reconciler 拥有、UI 不可改、git 可版本化)/ `dynamic`(UI 新建、DB 原生可变;仅 skill/tool,agent 暂 seed-only)。
   - **agent 只到 seed-only 物化**:config 仍唯一作者真相、不经 UI 编辑(运行时可编辑 agent 仍 Non-goal);物化只为统一存储 + 撞名检查 + 将来 dept 化加 `department_agent_rule`(v0 无消费者)。
   - **builtin 例外**:代码、无 `config/builtin/` 可扫 → 不经 reconciler、for-everyone 不入部门表。
   - **具体机制**(reconciler ingest / entrypoint leader 槽 / 增量覆盖 / 每 turn DB 快照)**落 Phase B**,见该节 + `EffectiveToolset` resolver。
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

1. **Skill 可见性 = `visibility`+`default_enabled` 两正交字段(替不透明 scope)+ 稀疏 `user_skill` 覆盖;部门作用域走 dept rule 表(决策 10)。** Skill 表带:`owner_user_id`(private 用、shared 为 null)+ `visibility`(`private` 仅 owner / `public` 全员 / `department` 按 grant)+ `default_enabled`(shared skill 默认是否注入)。**原三 scope 拆成这俩正交字段**:preinstalled = `public`+`default_enabled=true`(config 种子);marketplace = `public`+`default_enabled=false`(目录可见、opt-in);**market = link 动作全 DB/离线,无联网 registry**。**per-user 覆盖 = 稀疏 `user_skill(user_id, skill_slug, enabled)`**:无行=走 visibility/default,有行=用户显式开关 —— **marketplace 选用=enabled 行、关掉预装=disabled 行,link 与 toggle 是同一机制**(不是两套)。部门可见(`visibility=department`)= 用户祖先链 ∩ `department_skill_rule`(决策 10)。**`visibility` 现是 skill/external-tool/tool-set/mcp 四类共享字段**(原只 skill 有;external tool/tool-set/mcp 随本轮 DB 化获得 DB 列,见 Phase B/F + 决策 10):它定每个单元**未列出部门的默认姿态**(`public`=默认 allow / `department`=默认 deny);**一行 = 例外、方向由资源 `visibility` 派生**(public→deny、department→grant;无 `effect` 列,改 visibility 清规则,决策 10)—— 四类 `visibility` 都是 DB 列(不再 config frontmatter)。`private` 仅 skill 有(owner-only);`private` 与 builtin 都**不进 dept rule 表**(决策 10)。
2. **披露 = tool-set(DB 模型)+ `search_tools` 内建工具,纯 prompt 级。** 新增「一组多 tool」的 tool-set(一平台多 endpoint,DB 存储[config 仅种子]、未来 openapi 生成脚本的落点),整组标 deferred:索引行常驻 `<available_tools>`、schema 不渲染;模型调 `search_tools`(`select:Name,Name` 或关键词)拿回完整 XML 工具描述作 tool_result。**返回值复用 `generate_tool_instruction`**(= 渲染系统提示词可用工具那套);**描述随 tool_result 留在历史、后续轮模型自然可见 —— 不维护「已发现集」、不重渲染进 system prompt**(比 CC `extractDiscoveredToolNames` 追踪更简,且无 server `tool_reference` 依赖)。**为何这里靠历史、`active_skills` 却要 metadata-state(决策 11/原则 8)**:发现是**上下文**问题(描述在不在 context),非**权限**问题(可调与否由 resolver 按 unit 成员判、**与发现无关** —— deferred 只是描述没渲染、工具在授权 unit 里闸照过);上下文的家是历史,被压缩则模型见常驻索引行**自己再 search 一次**(loud-fail 自纠),无需 durable state。**附带**:发现动作不改写 system prompt —— 索引/catalog 已挪动态 reminder、只 grammar 留前缀(决策 11/Phase B line 103),前缀稳定可缓存、不被发现动作击穿。
3. **存储 = 消费列 + `metadata` JSON 杂项列 + `skill_md` 全文列 + 完整原始 blob(含 SKILL.md),复用 blob 存储。6 标准字段全部 DB 落位、按"消费与否"分流。** 四处:① **消费列**(`name`/`description`/`allowed_tools`/`compatibility`/`slug`/`visibility`/`default_enabled`/`owner_user_id`,**`visibility`+`default_enabled` 替原 `scope`,见决策 1**)—— 系统要查询/消费的字段反规范化出来(`compatibility` 供气隙依赖校验,决策 6),L1 批量列举 + 权限 + 校验,**不解 blob**;② **`metadata` JSON 列** —— frontmatter 里**系统不单独消费的字段全归这**(`license` + 标准 `metadata` 容器[含 `version`] + 任何未知扩展),"用不上的扔 metadata"、`license` 不开独立列,免解 blob 的杂项读取层;③ **`skill_md` TEXT 列** —— SKILL.md **正文(frontmatter 已剥离)**,L2 `read_skill` 直接返回(免解 blob;frontmatter 字段已在消费列/`metadata`、正文才是注入模型的指令,common practice = 不重渲 frontmatter,name/description L1 已有);④ **`bundle` BLOB** —— **完整原始 zip(含 SKILL.md + references + scripts + assets)**,L3 mount + 导出。**关键:blob 是真相源、存验证通过的原始上传整包**,①②③ 都只是反规范化的查询/读取副本(**不碰原 frontmatter 结构** —— `license` 在原 SKILL.md 顶层就还在顶层,DB `metadata` 列只是副本归类、非挪字段);故**导出无损仍是 construction 保证的**(直接吐原 blob,不从列重序列化 → 未知字段/格式不丢);代价是正文在 `skill_md` 与 blob 两处冗余(小文本,换无损+简单导入,值)。`preinstalled` 同 bundle 全用户共享一份(非 session-scoped)。
4. **三级渐进式披露,「激活」= 一次普通 read + 一次普通 mount,无独立机制。判别线:SKILL.md 走读通路,bundle 里任何东西走沙盒。** L1 =「有哪些 skill」由 **ContextManager 注入** `<available_skills>`(name + description,仿 artifact inventory 先例 —— 列表是 ContextManager 职责非工具职责,故**不学 opencode/CC 把 L1 塞进工具描述**)。L2 = 模型调 **独立 `read_skill(slug)` 工具**返回 `skill_md`(SKILL.md 正文、frontmatter 已剥离)作 tool_result(纯文本、provider-agnostic,opencode 实证此为可移植形态;**死简单——按 slug 返回 skill_md,无 path 参数、后端不解 zip**;**不合并进 `read_artifact`** —— 身份空间不同[user-scoped slug vs session-scoped id]、store/Manager 不同,合并要么加 type 参数[违 legibility]要么藏第二个 Manager;**镜像 read_artifact 契约**:句柄进/内容出、`max_result_size_chars=inf` 永不二次 persist、`AUTO`)。正文之外的一切(references/scripts/assets)**不走读通路、一律去沙盒读**(`read_skill` 输出附「其余细节含 references 须 mount 进沙盒读」提示)。L3 = 模型按需调 **现有沙盒 `mount` 工具**(复用,非新机制)把完整 bundle 挂进沙盒**固定约定路径** `/workspace/.skills/<slug>/`,模型用 bash `cat`/`python` 读跑;`${SKILL_DIR}` 因路径由 slug 确定**在 mount 前即可解**(替成约定路径),mount 返回真实路径作运行时确认 —— **占位符问题就此关掉**。
5. **bundle 走模型驱动 per-turn mount(复用现有 `mount`),不 auto-mount、不维持跨轮一致性。** 激活**无沙盒副作用**;`read_skill` 是纯读。bundle 内容(references/scripts)在哪轮用就哪轮 mount(同 artifact mount),受沙盒既有 per-turn ephemeral 纪律 + `<sandbox_status>` + bash file-not-found loud-fail 自纠管 —— **绝不为 skill 重建跨轮 mount 一致性机器**(沙盒 plan 已打过这仗并收手,见原则 8)。persist 仍模型驱动(沙盒 `persist`→artifact 现成)。
6. **依赖 = `compatibility` 声明 + 镜像通用栈 + asset 离线 wheel。** 通用重栈烤镜像(python + lxml/openpyxl/pypdf/pdfplumber/pandas/Pillow + libreoffice/poppler/pandoc/qpdf,存货与调研同指一组);长尾走 asset `wheels/`,**模型在沙盒里按需** `pip install --no-index --find-links` 离线装(**非激活自动**,守原则 8;详见 Phase D + changelog 06-18);import **软门槛**(verify agent)据 `compatibility`(缺则从内容推断)交叉校验镜像栈 ∪ 自带 wheel、缺口标记(best-effort;真兜底 = 运行时 `--no-index` 响失败,决策 7)。
7. **导入 = 单管线两道门:`upload → unzip → 硬门槛(确定性 validator,阻塞)→ 软门槛(verify agent,可强制通过)→ 解析 frontmatter + 入库`;两道门皆 best-effort 过滤、非正确性闸(软门槛可 override、容漏判),正确性兜底在运行时(`--network=none` + `pip --no-index` 响失败)。** **硬门槛** = plain code 阻塞门禁(便宜确定、先跑快速失败),只查 skill 本身良构,借 `skills-ref`/`skill-validator` 规则:6 字段 schema(在场字段合规、缺失宽容,决策 9)、name↔dir、孤儿文件、未闭合 fence、链接解析(含引用的 asset/script 路径)、SKILL.md 体量(legibility 警告)。**bundle 字节上限不在此** —— 那是上传路由的配额闸、按信任分层(原则 7③,admin 无闸)。**软门槛** = verify agent(懂本系统约束的 agent,贵=LLM 调用、后跑、**可 override**):**职责单一 = 评估「这个 skill 能不能在本系统跑」(系统兼容性归这)**,就两查 —— ① 用了没有的工具/harness?② 依赖能否满足:有 `compatibility` 声明则据其校验、**缺则从 body/scripts 推断**,vs 镜像栈 ∪ wheel(气隙网:需网即拒/标)?**输出诊断(能跑/缺什么),不改 body、不产 rewrite 或改造 diff**(改造/简化是人的事,见决策 8)。**用户私有上传时可选/可跳过**(LLM 成本)。「lint 不静默改写」在此:残留硬耦合由确定性 lint 标记 + **人**审手改,运行时禁静默 rewrite。入库 = 索引列 + `skill_md` + 完整原始 blob(决策 3)。
8. **预装 pandoc-first(瘦 bundle)。** 存货 4 文档 skill 三份重复 `office/` + 40 xsd 做 OOXML 手术;常见 Word 路径(读/转/简单生成)预装 skill **首选 pandoc/libreoffice**(镜像内,一条 CLI),OOXML 拆解只留给 pandoc 真做不到的(改痕/批注/精确版式)。删常见路径的三份 `office/` bundle。**这个分析简化是「我们」在 E 阶段人工做的工程动作**(预装集就 4 个、值得手工调),**不交给 verify agent**(后者只评估能不能跑、不改 skill,见决策 7)。
9. **采开放标准 6 字段,DB 全部落位、按消费分流。** `name`/`description`/`license`/`compatibility`/`metadata`/`allowed-tools`;`version` 归 `metadata`。**6 字段在 DB 都有落位(非只 name+description),按"系统消费与否"分流**(决策 3):消费的开独立列(`name`/`description`/`allowed_tools` 必做,`compatibility` 供气隙校验),**系统用不上的(`license`/`metadata`/`version`/未知扩展)全归 `metadata` JSON 杂项列**;真相源仍是原始 blob(**不改 frontmatter 结构、导出无损**)。**硬依赖只 `name`+`description`**(存货现状、其余常空),其余字段消费宽容缺失。`allowed-tools` 标准里本就 Experimental → 映射到我方 **unit 粒度**权限模型(决策 11 轴 B:builtin=singleton unit 故逐工具名原样工作,多工具 unit 成员名解析到整 unit)、不欠生态硬兼容。
10. **部门授权 = 两张 FK 表 `department_skill_rule`(FK→skill)+ `department_unit_rule`(FK→tool_unit,含 tool/toolset/mcp);builtin for-everyone 不入表,agent MD 不动、引擎运行时组合。**
    - **两张表**:`department_skill_rule(department_id, skill_slug)` FK→`skill`;`department_unit_rule(department_id, unit_name)` FK→`tool_unit`。均 `ON DELETE CASCADE`,一资源多部门=多行;**无 `effect` 列** —— 一行 = 该部门是默认的「例外」,方向从资源 `visibility` 派生(见下条)。资源各带 `visibility`(external tool/set/mcp 随本轮 DB 化获得,见 Phase B/F + 原则 5);**unit 的 tool/toolset/mcp 细分在 `tool_unit.kind`,规则表不存类型列**。
    - **方向 = 从资源 `visibility` 派生(行 = 例外成员、无 `effect` 列)**:`public`(默认 allow)→ 列出部门 = **deny**;`department`(默认 deny)→ 列出部门 = **grant**。一资源在某 visibility 下例外只一个方向(另一向 = 默认、不建行)→ **冗余结构上不可表达**。安全前提 = 下条「改 visibility 清规则」:行不熬过 visibility 变更 → 旧「派生方向随 visibility 静默翻转、反授权」的 bug(原 reviewer P1 存显式 effect 的唯一理由)失去载体。〔安全反转 311,见 changelog 06-23〕
    - **改 visibility = app-side 清该资源 dept 规则行**(同事务、定向、**留 `user_skill`**):visibility 是 UPDATE 非 DELETE → DB `ON DELETE CASCADE` 不触发;资源级 cascade 又会误伤 user_skill/agent_unit → 不用 trigger,在**每条改 visibility 的写路径**里定向删 dept 规则 —— **Manager UI 更新**(UI loud-warn「将清除 N 条例外」)**+ reconciler 从 config 更新 seeded 资源**(检测到 `visibility` 列变更同事务清,见 Phase B line 192);**两路都清**,否则 seeded 资源经 config 改 visibility 时旧例外熬过 UPDATE、方向反转 = 把本轮消的 bug 带回。换得:无 check 端点、admin/operator 改后在新 regime 重授。
    - **解析**:user `department_id` 走 `parent_id` 祖先链 ∩ 规则集(父覆盖整子树);命中 = 例外(方向 = `visibility` 默认反向),未命中 = `visibility` 默认。各方向只需 1 行(树覆盖子树)。
    - **工具侧只到 unit 粒度**(与 MCP 对齐):`tool`=独立工具 unit、`toolset`=整 set、`mcp`=整 server,只整 unit grant/deny,**无点名 set 成员的跨粒度规则**(原 `tool` 可指 set 成员的破例已删 → 连带删 set-vs-member 冲突/override/检测,见变更日志)。要切 set 子集 = 拆 set。`skill` 行不是 unit、按整 skill 授权。
    - **builtin + private 不入表**:builtin for-everyone(与 agent MD 对称、跨部门一致,部门级杠杆将来=agent 级);private owner-only。Manager/UI 硬拒写入。
    - **为何两张 FK 表(非一张 polymorphic)**:物理目标只两类(`skill` / `tool_unit`,后者已含 tool/toolset/mcp,unit-everywhere)→ 拆两张即可各上真 FK + DB `ON DELETE CASCADE`,**ABA 由构造消失、删整套 app-side cascade/孤儿处理**(polymorphic 无单 FK 才被迫 app-side)。消费侧本就分型(skill 可见性 vs unit 宇宙收窄是两条路),polymorphic 反在每读点按 type 过滤;两张表 = 预先分好 + `resource_type` 列消失。代价仅多一张 near-identical 表(解析/UI 共用泛型 helper)。
    - **agent** 虽随原则 5 物化为 DB 行,但 v0 无 dept 消费者 → 无 dept rule 表;将来 dept 化 = 加一张 `department_agent_rule`(FK→agent,agent 已是 DB 行)。
    - **命名** `department_{skill,unit}_rule`(中性:默认姿态在资源 `visibility`、不在表名)。
    - **identity = name,无 surrogate id**:skill/tool_unit/agent 本就不可重名(撞名 loud-fail)→ 以各自 natural key(skill=`slug`、unit/agent=`name`)作 PK;全系统 agent MD/`allowed-tools`/`<unit>__<tool>`/resolver 本就按名寻址,内外统一。**所有 m2m 均 natural-key 真 FK + DB `ON DELETE CASCADE`**(agent_unit / user_skill / `department_skill_rule` / `department_unit_rule` —— dept 拆两张正为此,见本决策「为何两张」)→ **ABA 由构造消失**:删名即 DB 级联删规则、无孤儿、无 surrogate id 可复用复活,不需「不可复用 id」机制。**代价 = 改名不保规则**:改名 = 删旧名(DB cascade 其 agent_unit/dept/user_skill 规则)+ 建新名(规则人工重授,reconciler loud-log 丢弃项);改内容(同名)仍 UPDATE 保规则。罕见操作,值。
    - 留待将来:参数级粒度(工具名+参数模式)、树 override 优先级(**启用「最具体规则胜」时需把 per-row 方向/`effect` 列加回** —— 嵌套反向例外、派生单方向表达不了)。

11. **工具权限 = 两条正交轴,经统一 `EffectiveToolset` resolver 单点解析、多处消费。**
    - **轴 A 等级 `{auto, confirm}`**:唯一来源 = 工具定义(`tool.permission`),config/agent MD/skill 一律不可改;唯一运行时变更 = 用户「始终允许」`confirm→auto`(`always_allowed_tools`)。
    - **轴 B 成员 `{enabled, disabled}`(+absent)**:agent MD 写 / m2m 减 / skill enable —— 与等级是不同枚举(`disabled` 不进 `ToolPermission`)。
    - **四层链(逐层只收窄/翻开,应用序 = 层号序)**:① 工具定义 = 能跑什么 + 各自等级,雷打不动;② **agent 宇宙 = agent MD builtin(直读)∪ `agent_unit` 绑定的 external 单元** = 天花板(每项 enabled/`disabled`,absent=不在宇宙);③ **dept 收窄宇宙**(决策 10)= 把部门未授权的 external 单元从宇宙移除 key、不在成员态上加闸,builtin 绝缘;④ **skill**(`active_skills`)= enable-only,翻开收窄后宇宙里的 `disabled`,不加删 key、不碰等级。
    - **`agent_unit` m2m 统一静态+动态**:agent MD external 声明经 reconciler 种 `seeded` 行、UI 挂载加 `dynamic` 行 —— 解了「冻结 config 天花板容不下 UI 新建的 DB 工具」(reviewer P1#1);builtin 不入 m2m —— 随 agent 物化在 `agent` 行(line 126「声明的 builtin」),引擎从 DB 快照的 agent 行直读(「直读」= 绕过 m2m join、非读 MD 文件;builtin 自身无表无独立 MD)。
    - **dept 在 skill 之前收窄 → private skill 翻不开 dept-denied 是 by-construction**(非末端 AND 闸;解析详见数据模型总览「工具有效集」)。
    - **P0 信任边界**:skill 只能翻 agent 宇宙内成员态 `disabled` 的 unit,做不到引入 `absent`(MD 未声明且 `agent_unit` 未挂)、碰等级、翻 dept-denied。故 operator「任何 skill 都别碰」= 让它 `absent`(不是 `disabled`);敏感工具靠 `confirm` 等级(skill 不可改、用户每次在环)兜底 → 私有 skill 无法静默提权,故 verify 门禁非权限边界、私有上传可跳过 verify。
    - **粒度统一 = unit**:agent MD 声明 / m2m 减 / skill enable / dept grant-deny 一律在 unit 上,一套 match 函数(按 unit 名)、**无第二套语义**(reviewer P2)。**builtin = singleton unit**(对 builtin 即逐工具,标准 `allowed-tools` 逐工具名原样工作);多工具 unit(set/MCP)要更细 = 拆 unit。**等级是唯一 per-tool 量**。over-grant(skill 开整 unit ⇒ 成员全开)兜底 = `confirm` 等级 + 拆 unit。
    - **命名 `<unit>__<tool>`**:**unit 名禁 `__`**(operator/loader 控)→ `<unit>__` 前缀唯一可识别;**tool 段可含 `__`**(MCP spec 合法名 `^[a-zA-Z0-9_-]{1,64}$`,见 F),故 **resolver 按已知 unit 名前缀剥离、不 split `__`、不假设恰好一次**(reviewer P2)。MCP = `<server>__<tool>`(不带 `mcp__` 前缀,否则 server unit 名含 `__` 撑破 `<unit>__*` 通配)。unit 名跨 tool-set/MCP 全局唯一、启动期撞名 loud-fail(见 B)。
    - **tool-set ↔ MCP 同型**:授权/披露/命名整链对齐,唯一别 = 静态(config 已知)vs 动态(连接时灌)→ B 把缝按 MCP 形状留对,F 纯加法。
    - **存储 + 解析**:agent MD `tools:` 存单元引用 + 成员态(不摊平);resolver 解析时展开成扁平 `{tool: level}`(builtin 直读 / external 从 `agent_unit` 取 / tool-set 静态展开 / MCP 运行时填),每项套「成员性 + 工具定义等级」。
    - **resolver = 唯一解析点**,所有读点读同一输出:渲染 `ctx:89`、条件提示词段 `ctx:204/215`、执行闸 `engine:759`、等级 `engine:844`、`search_tools` 可见集过滤 —— 由构造消灭多消费点漂移。
    - **迁移 + schema**:agent MD `tools:` **一次性转写**成新形态(我方 config、可改),成员态只 `{enabled(省略即是)/disabled}`、**删 auto/confirm 等级字段**(等级 sole-source 工具定义,MD 写了无效=误导)。旧 `auto/confirm` **不 lenient 当 enabled 读**,归「未知成员态字面量启动期 loud-fail」(指引:等级改去工具定义、成员态用 enabled/disabled)。**为何 loud 不 lenient**:静默当 enabled 会在工具定义为 auto 时把 operator 的 `confirm` 降级,撞 P0。

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
| `department_skill_rule` | department ⟷ skill | 这部门**能用**哪些 skill(例外成员,方向派生自 `visibility`) | admin UI |
| `department_unit_rule` | department ⟷ tool_unit | 这部门**能用**哪些 unit(例外成员,方向派生) | admin UI |
| `user_skill` | user ⟷ skill | 用户对 skill 的个人开关(覆盖默认) | 用户 |

**已有组织表**:`user`(`department_id` / `role`)、`department`(`parent_id` → 部门树,祖先链解析靠它)。

**引用关系**(箭头 = 外键/引用方向):

```
user ─department_id─> department ─parent_id─> department      (部门树)
agent_unit ─agent_name─> agent ;  ─unit_name─> tool_unit          (真 FK,可 DB cascade)
department_skill_rule ─department_id─> department ;  ─skill_slug─> skill     (真 FK,可 DB cascade)
department_unit_rule  ─department_id─> department ;  ─unit_name─> tool_unit  (真 FK,可 DB cascade)
user_skill ─user_id─> user ;  ─skill_slug─> skill              (真 FK,可 DB cascade)
```

**一个 turn 怎么算有效集**(resolver,决策 11;每 turn 从 DB 读快照):用户 U∈部门 D、用 agent A、激活 skills S →

- **工具有效集**(三步、**dept 收窄宇宙本身、不是末端 AND 闸** —— 部门 `deny` 的工具直接从宇宙移除 key,skill 物理上够不到):
  - ① **宇宙(ceiling)** = builtin(A,从 `agent`,for-everyone 恒在)**∪** external 单元(`agent_unit` 挂给 A 的),每项带成员态 enabled/`disabled`;agent MD 与 `agent_unit` 都没有的工具 = **不在宇宙**(absent)。
  - ② **dept 收窄宇宙** = 对 external 单元按 visibility 默认 + `department_unit_rule`(D 祖先链)例外解析(方向派生自 visibility),**部门未授权的直接移出宇宙**(key 删掉、连 enabled/disabled 状态都不再有;`public` 无规则行=授权**保留**、`department` 无 grant=**移除**,规则行只做 grant/deny 例外);**builtin 不过此步**(for-everyone、无 visibility、不进 dept 表,恒在宇宙)。
  - ③ **成员轴(在收窄后的宇宙内)** = 宇宙内 enabled 的项 **∪** S enable 的 `disabled`;skill **只翻幸存宇宙里**的 `disabled`。
  - **关键(reviewer P1)**:`deny` 的工具在 ② 已被移出宇宙、③ 里 skill **无 key 可翻** → private skill 绕不过部门授权是 **by-construction**(非靠末端 AND);与决策 10 / Phase G「dept 是 resolver 最后一个输入层」一致(dept 收窄在 skill enable 之前)。每工具**等级**从其定义查(`tool_unit` 行 / builtin 代码),绑定表不存等级。
- **`EffectiveSkillSet(U)` 两轴**(别揉成一个「可见」):**visible** = owner/public/`department_skill_rule(D)` 祖先链解析(**正确性**,决定能否 read/mount);**L1 enabled** = `visible ∩ (default_enabled + user_skill(U) 覆盖)`(**UX**,决定上不上索引)。用户关掉的 skill **仍 visible**(可 `/skill` 显式读)、只是不进 L1 → **不 404**。**全 agent 可见、不分 agent**(效果按各 agent 宇宙收窄,决策 11)。

**删除 cascade**(决策 10):所有 m2m 均真 FK → 删 dynamic 资源由 DB `ON DELETE CASCADE` 自动删指向它的行:**`skill`** → `user_skill` + `department_skill_rule`;**`tool_unit`** → `agent_unit` + `department_unit_rule` + tool-set 成员行;静态删(reconciler prune)同走 DB cascade,**不需 app-side cascade**。

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

**进展**:
- **2026-06-24 A 实现落地(合 main)**:`ToolResult.artifact: Optional[ArtifactSpec]`(纯数据、不持 service 句柄,守三层)→ `ArtifactService.ingest_tool_result` 成 `create_from_upload` 配额/blob/去重内核的**第三调用方**;`_maybe_persist_tool_result` 统一 —— 声明式 artifact 落盘失败 / 无名结果超 `max_result_size_chars` 一律 **loud-fail**(删 fail-open:退回超长原文正是落盘要防的)。`web_fetch` 文件旁路:按 URL 尾缀(`WEB_FETCH_BLOB_SUFFIXES`:pdf/office/zip/图)在 Jina 前分流、直连下载置 blob,**旁路直连前自调 `validate_public_url` + `allow_redirects=False`**(SSRF),`content_type` 取**受控尾缀 MIME、不信远端 Content-Type**(防 svg-XSS / XML 注入)。**XOR**:一 artifact 存且仅存一种实质表示(content XOR blob),「是否二进制」判别由可走私的 `metadata.blob_content_type` 升级为权威 **`Artifact.has_blob` 列**(就地写进 squash 的 `0001`,全新建库假设、不加 0002)。`artifact_envelope` 给 `<title>` 加 `_text` escape(model-facing XML 不追严格良构,见 CLAUDE.md)。验证:**1195 后端测试全过**。三条 reviewer 驱动决策的原委见 changelog 06-24。
- **2026-06-24 前端 live 暴露 + 授权弹窗收口**:tool-source artifact 回合内**实时可见** —— `artifactStore.applyArtifactCreated` 删 `source!=='tool'` 的 auto-open 排除(后端本就实时 emit `ARTIFACT_CREATED`,差的只是前端门控),tool 产物与 agent artifact 一视同仁弹出,仍保「不抢占用户手动选中」。**授权弹窗防误触**:`PermissionModal` 置 `closeOnBackdrop/closeOnEscape=false`,backdrop 点击 / ESC 不再静默发 deny → 出口只剩三按钮 + 后端 `PERMISSION_TIMEOUT` 兜底。commits `84d8d47`/`c5ae08d`/`a9a32a4`,前端 203 passed。**dev 注意**:SQLite dev 库需手动 `ALTER TABLE artifacts ADD COLUMN has_blob`(`create_all` 只建新表、不改已有表;全新建库 / 生产无此步)。

### B — 工具渐进式披露(tool-set DB 模型[迁出 config]+ `search_tools`;MCP 适配缝)

**做什么**:解决「30-endpoint 平台 = 30 份描述常驻 system prompt」。披露单位 = **场景/平台(tool-set)而非单工具**,机制 = deferred 索引 + 按需加载。**与 skill 正交**(原则 1)。

**包含**:
- **tool-set DB 模型 + 命名空间**:
  - external 工具/tool-set 本轮**从 config 迁入 DB + 前端 CRUD**(原则 5/决策 10:`config/tools/` 种子 = 不可变 seeded,UI 新建 = dynamic,无论来源都是 DB 行)。存储 = 「一平台多 endpoint + set 级描述/索引行 + `visibility` 列」。**凭证统一加密落库**(独立 `tool_credential` 表,unit 级,B-4 落地;细化见 `skill-system-phase-b-design.md` B-4「凭证模型」):resolve 一条路 = 读库解密、lazy 到 execute;dynamic = UI 直接配 key,seeded = reconciler seed 时按 `{{TOOL_SECRET_X}}` 从 env 取值加密落库(MD 仍只放引用、secret 间接层保留)。主密钥在 env、单把、不做轮转。**红线**:这是受信 backend 工具的 operator/工具级凭证,不碰沙盒(永不拿凭证)与 per-user 身份透传(B1/B2,仍 defer)两根轴。
  - **成员命名 `<setname>__<tool>`**(MCP 同型 `<server>__<tool>` 不带 `mcp__`),loader 据 set 名自动加前缀(作者写裸名)。**unit 名禁 `__`** → `<unit>__` 前缀唯一、`<unit>__*` 通配无歧义、扁平 builtin 可区分;tool 段可含 `__`(MCP spec 合法名,解析按已知 unit 前缀剥离,见决策 11)。静态作者成员名建议单 `_`(operator 简化、非强制)。
  - **为何命名空间**:① 关键 = 让 unit 粒度授权按前缀 `<setname>__*` 整组工作(与 MCP `<server>__*` 同构);② 跨 set 同名 endpoint 不撞;③ 模型可读。**用 `__` 不用 `:`**:参数是 XML 标签、`_repair_tool_name_as_tag` 会把名当标签,冒号撞 XML namespace。
  - **unit 名全局唯一、启动期撞名 loud-fail**:扩现有 `build_tool_map`(`base.py:279`)认 tool-set/MCP unit 名(非现成机制);顺带由 reconciler loud-fail 关掉 `load_all_agents` 同名静默覆盖。这也是未来 openapi→tool-set 生成脚本的落点。
- **`agent_unit` 绑定 API/UI 在此落地(决策 11,reviewer P2)**:UI 新建的 `tool_unit` 进 DB、甚至被部门 grant 后,**仍须挂到某 agent 的宇宙才可用**(否则对所有 agent 都 `absent`)—— 这个挂载入口就在 B。operator 在工具管理页勾选「此 unit 挂给哪些 agent」→ 写 `agent_unit` 的 `dynamic` 行(seeded 行由 reconciler 从 agent MD 种,见上)。**这只是给 agent 挂能力单元、不是编辑 agent 的 prompt/model**(运行时可编辑 agent 仍 Non-goal);故它是 operator 资源管理动作、与 G 的部门授权正交(宇宙=该 agent 暴露什么,dept=哪个部门能用)。**必须在 B(非 G):** 没有这个绑定,B 之后 UI 建的工具对任何 agent 都不可达 = 比 dept 更基础的一环;dept 是宇宙之上的额外闸。
- **通用 `config-seed→DB reconciler` 在此落地(横切底座,原则 5)**:tool 的「config 文件→DB」不是 tool 专属,是 **skill/tool/agent 共用的种子 ingest**(扫目录 + per-type 解析 + 内容哈希幂等 upsert + `seeded` 不可变 + 撞名 loud-fail)。**随第一个消费者 tool 在 B 建好**,skill@C、agent retrofit 复用同一件(agent retrofit 顺手关掉 `load_all_agents` 撞名缺口)。**澄清「G 是不是把 tool 表也做了」**:不是 —— tool 表在 B、mcp 在 F、agent 物化随 reconciler;**G 一张注册表都不建,只消费 reconciler 产出的 DB 行**(查 grant + 树解析)。引擎**每 turn 执行前从 DB 读一次注册表快照**(+ 启动期静态 builtin),turn 内读快照不反复查 DB(为何 per-turn 快照而非进程级长缓存:避跨 worker 缓存失效[否则要 pub/sub]、保 turn 内一致[目录不因 mid-turn CRUD 抖];成本同引擎今天每 turn 已重建 `MessageEvent` 历史)。
  - **落地位置 + 增量语义**(原原则 5 机制):跑在 `deploy/entrypoint.sh` 的 **leader-only 槽**(复用 migration 的 PG advisory lock,migration 后 / 起 uvicorn 前;SQLite 单副本直接跑),**绝不在 per-worker lifespan `init_globals`**(每副本跑会互写)。**增量覆盖**(name 作 PK)= per-unit 内容哈希(只写改了的);**改内容(同名)= UPDATE 定义列**,m2m 规则按 name 引用、原样保留(不碰 grant/统计列)—— **例外:若 `visibility` 列变更,同事务清该资源 dept 规则**(reconciler 是第二条改 visibility 路径,与决策 10 的 clear-on-visibility 同语义;否则 seeded 资源经 config 改 visibility 时旧例外反转方向);**改名 = prune 旧名 + cascade 其 agent_unit/dept/user_skill 规则,再 insert 新名(规则人工重授),reconciler loud-log 丢弃的规则**(改名罕见、授权不静默归零);config 删→prune + cascade;seed 撞 `dynamic`→loud-fail。**运维触发零新增步骤**:config 是 bind-mount,既有 `pause`→改→`resume`(recreate)重起容器即重跑 entrypoint。
  - **2026-06-29 乙2 落地(release/serve 拆分,合 main)**:reconcile 降为单点部署 job 的方向已实现 —— `entrypoint.sh` 加 `release` 模式(migrate+reconcile 一次后退出)+ `AF_SKIP_RELEASE` serve 路径(默认仍 inline,向后兼容);prod/intranet compose 加一次性 `release` service + backend `depends_on: service_completed_successfully` 门禁;nginx backend 改变量 `proxy_pass`($af_backend,经 resolver 重解析)使 `--scale` 真轮询。**真机 `--scale` 验证待 Mac 收尾**(见 `deploy/MULTI-REPLICA.md`)。多机编排(Ansible delegate 一台跑 release)仍将来。
  - **留待将来:多机一致性 / 多机编排**(现单机 compose + 乙2 门禁已够):advisory lock 只给**互斥**、非"全局只跑一次",各机 bind-mount config 若不一致 = 谁后跑赢 + 重启抖(migration 无此患 = 迁移文件烤死镜像里、确定收敛;reconcile 输入是 config 才暴露)。**config 一致性** = 成单一 artifact(烤镜像 / 同步同份)令 reconcile 幂等且与启动时序无关;**只跑一次** = reconcile 降为单点部署 job(单源、对共享 DB 跑一次)。**编排基线 = Ansible + docker**:沙盒是 DooD + runsc(`config.py` SANDBOX_RUNTIME),app 机本就是带 runsc 的纯 docker 宿主,Ansible 装 runtime + 推同份镜像正好契合、不碰沙盒架构。**k8s 非推荐**:gVisor 虽有 RuntimeClass,但要把沙盒 spawner 从 docker 模型重写到 k8s API + 每节点 runsc 配置,对私有化几台机不划算 —— 仅"本就是 k8s 厂"时考虑。
- **`EffectiveToolset` resolver 骨架在此立(基础设施,决策 11)**:把现散在 4 处各自直读 agent MD dict 的读点(渲染 `ctx:89`、条件提示词段 `ctx:204/215`、执行闸 `engine:759`、等级 `engine:844`)收成唯一解析点。**必须在 B 收口**:B 是第一个改变工具集形状的阶段,收口越晚同一组读点被反复 refactor 越多次(B/C/G 三轮碰同 4 点 = 退回架构信号)。
  - **B 立骨架时输入只有静态两样**:① agent 宇宙(agent MD builtin ∪ `agent_unit` external)② tool-set 单元展开(`<set>__*`,tool-set 要可调本就必须展开 = B-native 职责)。输出 = 扁平 `{tool: level}`。
  - **之后每阶段只加一个输入层、不再碰读点**:C 加 `active_skills`、F 加 MCP 运行时填充、G 加 dept 规则。`search_tools` 是第 5 读点(B 新建、非返工)。
- **deferred 渲染(走 CC 激进路线)**:tool-set(或单工具标 `defer: true`)只在 `<available_tools>` 出索引行,完整 XML 描述(含 `parameters`)不渲染。**索引行 = tool-set 一条 set 级描述 + 成员工具名列表(光名字、不给每工具 desc);单工具 deferred = 光工具名**(对齐 CC —— CC 连描述都不给、赌名字自解释;我方 tool-set 名字外包一层 **set desc 做语境**,比 CC 孤立名字更稳)。完整描述由 `search_tools` 经 `generate_tool_instruction` 按需补全。**defer 是显式开关、不按 token 自动**:config 配了 `defer: true` 才 defer(只出 name)、没配则完整描述照常注入;私有化无 tokenizer 算不了预算,故**不学 CC `tst-auto` 的 token 预算自动 defer**(合原则 7、一切 operator 显式配)。
- **tool list 挪进动态 reminder(prompt-caching)**:工具描述现在坐 system prompt 前缀(`context_manager.py:92`),工具列表会话内动态后每次变化打掉整段历史 cache。**改:拆 `generate_tool_instruction` 两段** —— 稳定的 tool-call 协议语法(`xml_formatter.py:17-42` 的 `<format>` 块)留前缀保可缓存;动态的工具 catalog(`:44` 循环)挪进 `_build_dynamic_context`、与 `artifacts_inventory` 同级(历史末尾 reminder,`ctx:77` 早立此原则,catalog 是唯一没跟上的)。catalog 变化只失效末尾、grammar 前缀恒稳。reminder 有序:`task_plan` 打头,tool list 挨 artifact inventory。当前生产无 APC = 收益暂 0 但无害,prompt 全可控故零成本可挪。
- **单元非 discovery 边界(两层够、无第三层)**:MCP `tools/list` 扁平一发返全部、无原生渐进发现;tool-set 同理。故 unit 只做授权 + 生命周期边界,披露就两层:① 检索注册表(`search_tools`)= 全成员扁平注册(MCP 连接灌 / tool-set 启动灌);② L1 索引 = enabled 工具 name 全注入(unit = set desc + 成员光名字,不做自动折叠)。怕大 unit(50 工具 MCP)撑爆 L1 → 不靠折叠,靠 agent MD 配 `disabled` + skill 按需 enable(operator 显式控制)。「server 里有啥」= `search_tools(unit=x)` 普通查询,不做 `list_members` 第三层。
- **`search_tools` 内建工具**:`select:Name,Name` 直选或关键词搜,返回完整 XML 描述作 tool_result(复用 `generate_tool_instruction`)。**结果必须过滤到当前 EffectiveToolset 可调集**(reviewer P1):含 enabled-but-deferred(defer 的意义),排除 disabled/absent/已减/未授,否则泄露不可调工具 + 模型反复试 → 故 `search_tools` 是 resolver 又一读点。描述随 tool_result 留历史,不维护已发现集(被压缩则模型见索引行自己再 search,详见决策 2)。注册进 `dependencies.py:_load_tools()`,纯 prompt 级、无 server `tool_reference` 依赖。
- **provider 抽象(F 的地基,B 建好)**:给 tool 模型加 `source`/`provider` flag(仿 CC `isMcp`);registry 归一化所有来源到一个 tool 形 + 合并函数。这样「MCP server = 又一个 deferred tool-set provider、`<server>__<tool>` 命名(`__` 唯一分隔、无 `mcp__` 前缀,决策 11)、走同一 `search_tools`」在 F 是纯加法。B 须按 MCP 的形状把缝留对(命名空间、按 server 名搜、动态 set),F 才接得干净。
- **部门作用域 wire-in 的边界(消费全留 G)**:tool-set 将来接入 `department_unit_rule`(FK→tool_unit),行 = 例外、方向派生自 tool-set `visibility`(无 `effect` 列,决策 10)。**B 只负责两件准备**:① tool-set DB 模型带 `visibility` 列(随本轮 DB 化,非 config frontmatter)、② 让 tool-set 有稳定 unit name 作 `department_unit_rule.unit_name` FK 目标。**部门规则的实际消费(查表 + 树解析 + 进 resolver)全在 G**(**两张规则表都在 C 建**——skill 表即消费、`department_unit_rule` 空跑到 G 才消费;dept 输入层 G 才加,见 line 101 分阶段输入);**B 的 resolver 骨架输入只有静态两样、不认部门规则**(修正:原写"resolver 减法认 grant"与 line 101 矛盾)。授权 UI 归 G。

**到时再敲定**:tool-set DB schema + `config/tools/` 种子加载细节;**两个语义已定**:① **压缩对工具发现** —— 无工具感知压缩,归 **compact_agent 提示词保留 `search_tools` 发现**(同现保留 artifact IDs/tool 交互);summary 留意识/续作、**全 schema 不入**,需重调靠常驻索引行重 search_tools(loud-fail 自纠)。② **`always_allow` 的 key = 规范全名 `<unit>__<tool>`**(builtin 裸名):同名跨 unit 独立、同工具跨 skill 共享。

**进展**:开工细化 + 逐片落地小结见同目录 `skill-system-phase-b-design.md`(B-1 write-only 物化 → B-2 接进引擎 + decision-11 重写,各片「进展」小节为准)。

- **2026-06-25 B-1 落地(合 main,纯加法、引擎零行为变化)**:DB 注册表 4 表 `tool_units`/`tool_members`/`agents`/`agent_units`(natural-key PK、m2m 真 FK + `ON DELETE CASCADE`、权限两正交轴 —— 等级唯一来源=工具定义,成员态归绑定),无存量数据**就地写进 squash `0001`**(沿用 A 的 `has_blob` 姿态、不加 `0002`)。通用 `config→DB reconciler`(`src/reconcile/`)= **目录即工具集**(扁平 `*.md`=singleton unit;`<set>/` 目录 + `_set.md`=toolset)、内容哈希幂等 upsert、prune、clear-on-visibility 钩子(dept 规则表未建→空跑);**命名不变量单点强制**:unit 名禁 `__` / member 段允许 `__`(剥前缀解析、不 split)/ unit·full_name ∈ `builtin∪reserved∪external` 全局唯一(撞名 loud-fail)。snapshot 读侧从 DB 行重建 `HttpTool`+agent 元数据(ORM 不外逃);入口 = 独立脚本 `scripts/reconcile_config.py`(dev 手动 / prod `entrypoint.sh` leader 槽调用,**follower 也跑一遍幂等 reconcile 自证 config**、堵 follower 带空注册表启动)。引擎仍走 in-memory `load_all_agents`/`_load_tools`,DB 物化暂不消费(B-2 flip)。测试 `tests/reconcile` 15 passed,db/tools/agents/repos/core/api 全过。详细收口决策见该 design doc + git history。

- **2026-06-25 B-2 落地(合 main,引擎切 DB 消费 + decision-11)**:新 `src/core/effective_toolset.py` 把原 4 读点(context_manager 渲染/条件段、engine 执行闸/等级)收成唯一解析点 —— `resolve_all(snapshot, tools)` 产出 `{agent: {full_name: 等级}}`,等级一律取自工具对象(绑定不存等级)。引擎/上下文构建改读 `EffectiveToolset`,不再直读 `AgentConfig.tools`。`controller_factory` 每 turn `load_registry_snapshot` → agents + external 工具(HttpTool)从 DB 重建、`resolve_all` 解析、缺 lead_agent loud-fail;`dependencies._load_tools` 瘦成 builtin-only,**external 工具唯一来源自此 = DB 快照**(不再进程级加载 `config/tools/*.md`)。decision-11:`config/agents/*.md` 的 `tools:` 值改 `enabled/disabled`(bash/web_fetch 的 CONFIRM 早在工具类、丢绑定覆盖**零行为变化**),`parse_agent_seeds` 收紧为成员态、旧 `auto/confirm` loud-fail。测试:resolver 单测 + reconcile 补 legacy-literal/disabled + 既有 engine/build/controller 用例经测试桥 `tests/core/_toolset.py` 注入、chat E2E 用合成快照;后端全过。详细见 design doc B-2「进展」+ git history。

### C — Skill 核心(存储 + L1 注入 + `read_skill` 工具 + 权限/上下文覆盖)

**做什么**:落地标准 Agent Skills 的**纯 prompt 修饰器**部分(不含 bundle 执行,那归 D)—— 可见性存储(决策 1)、L1 索引常驻(ContextManager)、L2 `read_skill` 返回 `skill_md`、`allowed-tools`→权限覆盖;**并落两张 dept rule 表(`department_skill_rule` + `department_unit_rule`)+ 祖先链解析地基**(决策 10:两表 near-identical、一起在 C 建;**skill 表即消费**[skill 可见性]、**`department_unit_rule` 建好但空跑、G 才消费**[宇宙收窄];tool/toolset/mcp 接入 + UI 归 G)。**C/D 阶段线 = 有没有 bundle**:单 SKILL.md(无 bundle,如 consolidate-memory)C 完即可用、不碰沙盒;**有 bundle(references 或 scripts)的等 D**(bundle 任何内容都走沙盒读,见决策 4 判别线)。

**包含**:
- **`Skill` 表 + 覆盖表 + grant 地基**(决策 1/3/10):**消费列** `slug`/`owner_user_id`(shared 为 null)/`visibility`(private/public/department)/`default_enabled`/`name`/`description`(`when_to_use` 折进)/`allowed_tools`/`compatibility` + **`metadata` JSON**(`license`/`version`/未知扩展等系统不消费字段,决策 3/9)+ **`skill_md` TEXT**(正文、去 frontmatter,L2 读)+ **`bundle` BLOB**(完整原始 zip 含 SKILL.md,L3/导出);`user_skill(user_id, skill_slug, enabled)` 稀疏覆盖;**`department_skill_rule(department_id, skill_slug)`** FK→`skill`(无 `effect` 列,行 = 例外、方向派生自 `visibility`,决策 10;此阶段先建表 + 祖先链解析)。Repo/Manager/Router 三层(skill 非 session-scoped,Manager 做 ownership/可见性/序列化)。
- **`EffectiveSkillSet(user)` = skill 侧单点可见性 resolver(对应工具的 `EffectiveToolset`、并列非合并)**:L1 注入 / L2 `read_skill` / L3 mount / **用户侧 read/list REST** 走它,杜绝「注入有闸、read 没闸」漂移;**admin 管理端点不走**(管 public/marketplace + 部门授权 UI,按 admin scope + ownership/seeded、**不被调用者自身部门 visibility 过滤** —— 否则 admin 看不到别部门可见的 shared skill,守 `feedback-admin-scope-user-mgmt`)。形状比 `EffectiveToolset` 简单 —— **无 agent 维度**(skill 全 agent 可见)、只两轴:**visibility**(owner/public/**department 走祖先链 ∩ `department_skill_rule`**)+ **enabled**(`default_enabled` + `user_skill` 覆盖)。守的是 **visibility = 正确性**(miss→404、四面一致),**enabled = UX** 只叠在 L1 + 前端 toggle(`/skill` 显式读自己关掉的可见 skill 是合法 opt-in、不被 enabled 挡)。与工具 resolver **只共用 dept 祖先链泛型 helper**(G 建)、不共用其余。
- **API + 前端**:CRUD + 可见性/启用 toggle 端点;前端 = 设置/管理页(非对话流内),admin 管 public(preinstalled/marketplace)、user 管 private + opt-in 链接。admin scope 守 `feedback-admin-scope-user-mgmt`(管共享资源,不碰用户数据)。部门 grant 的授权 UI 归 G。
- **L1 注入(ContextManager 职责)**:`<available_skills>`(name + description),每 turn 全量渲染(**无预算闸 —— 见原则 7**;索引大小靠 operator 策展约束,非运行时截断)。接入 `ContextManager.build()`,仿现 `_build_available_subagents`/artifact inventory 模板。**全 agent 可见 skill(去 agent 维度,改前述 lead-only)**:skill 可见度只走 user/dept visibility 轴、**不分 agent** —— 安全由「skill 的 `allowed-tools` 只能在该 agent 自己的、且按该 user 部门 **dept 收窄后**的工具宇宙(`agent_unit`)内 enable」兜住,故 skill 全局可见而**能力效果按 agent 宇宙 + 部门双重收窄**(判别:工具=能力需 per-agent 范围,skill=软引导无需,见决策 11)。`disable-model-invocation` 的从索引隐藏。
- **L2 = `read_skill(slug)` 工具**(决策 4):独立工具、镜像 read_artifact 的**工具契约**(`inf` 不二次 persist、`AUTO`)**但可见性不照抄**——read_artifact 只有 owner 一轴,skill 多 department 轴,故可见性走上述 `EffectiveSkillSet` resolver(**非抄 owner-only 404**,否则 dept skill 注入挡得住、read 挡不住),**死简单——返回 `skill_md`(正文、去 frontmatter)、无 path 参数、后端不解 zip**;输出附「其余细节(含 references)须 mount 进沙盒读」提示;**不合并进 read_artifact**(身份空间/store/Manager 不同)。用户 `/skill` = 等价入口(同走 read_skill 取正文注入,多一个 UI 触发)。激活无副作用、非状态切换(原则 8)。
- **`allowed-tools`→成员轴 B 的 enable-only(决策 11)**:
  - **激活持久化**:slug 加进 `state["active_skills"]`,照抄 `always_allowed_tools`(回合末写 `Message.metadata`、父消息捞回,`controller.py:176/529`,不扫历史)。resolver 据此**在 dept 收窄后宇宙内**翻开 skill 点名的 `disabled` unit(不引入宇宙外/dept-denied unit、不碰等级)。
  - **条目→unit 解析 = 纯 exact match、无模糊**(reviewer P2,import + runtime 共用一个函数):① exact-match 已注册 unit 名 → 该 unit;② 否则 exact-match 已注册全名 `<unit>__<tool>`(`<unit>` 须已知,按 unit 名前缀、不 split `__` 反推)→ 归属该 unit;③ 裸成员名(无 `<known-unit>__` 前缀、又非 unit 名)不接受 → import warn / runtime 忽略。**`search` ≠ `github__search`**(不同 key,裸 `search` 永不命中 set 成员;多 unit 重名 `search`/`create` 时防启错整 unit)。命中后 enable 整 unit。
  - **resolver 只加一个输入层 `active_skills`**(骨架 B 已立),不重碰 4 读点;dept/MCP/UI 同理各阶段加层。与 `always_allowed_tools`(等级轴)两条独立轴。
  - **import vs runtime 校验基准**:skill 全 agent 可见、不绑 agent,故 import 期无 user/dept/agent → 只把条目解析到 unit、对全局 ceiling 校验 unit 存在,可选 warn「当前无 agent 挂载此 unit」(`agent_unit` 后续可挂、非永久悬空);**别在 import 期做 dept 收窄**。runtime enable 才在「具体 agent × user 部门收窄后」生效。
  - 留待将来(memory 已标):① 历史 tool_result 引用已不可见工具的状态文案;② `allowed-tools` 点名宇宙外工具 → 忽略 + import warn。
- **config 种子**:`config/skills/<name>/` 启动时 zip→完整 blob + 解析索引列/skill_md(剥 frontmatter 后正文)→upsert 进 DB(`visibility=public`、`default_enabled=true`、owner=null),内容哈希幂等防重(决策 3)。

**到时再敲定**:frontmatter 精确字段子集(v0 = `name`/`description`/`allowed-tools`?);skill 与 B 的 tool-set 是否联动(**开放分叉**:CC/opencode 完全分离;我方**可选**让 skill 的 `allowed-tools` 调用时 auto-`search_tools` 预载其 set —— v0 倾向保持分离、再议)。

**进展**:未开工。

### D — Skill bundle 执行(L3 挂载进沙盒 + `compatibility` 依赖三层 + 离线 wheel)

**做什么**:让带 bundle 的 skill(docx/pptx/xlsx/pdf 类)真能用 —— 模型按需把完整 bundle mount 进沙盒(复用现有 `mount`)、bash `cat` 读 references / `python` 跑 scripts;依赖走原则 4 三层离线投递。这是「沙盒先于 skill」的兑现点。**bundle 里一切(references + scripts)都在沙盒读**(决策 4 判别线:SKILL.md 走读通路、bundle 走沙盒)。

**包含**:
- **bundle 经现有 `mount` 进沙盒(复用,非新机制)**:扩 `mount` 让它能引用一个 skill 的 bundle(类比引用 artifact id),解**完整 zip**(含 SKILL.md/references/scripts/assets)到**固定约定路径** `/workspace/.skills/<slug>/`。`${SKILL_DIR}` 因路径由 slug 确定**在 read_skill 时即可解**(替成约定路径),mount 返回真实路径作运行时确认 —— **占位符问题关掉**(决策 4)。**模型驱动、per-turn**(决策 5/原则 8):`read_skill` 提示"其余去沙盒读",模型那轮自己调 mount;**不 auto-mount、不跨轮维持**,受沙盒 ephemeral 纪律 + `<sandbox_status>` + loud-fail 管。
- **依赖三层兑现**(原则 4 / 决策 6):① 通用重栈进沙盒镜像(pandoc/libreoffice/poppler/qpdf + 科学栈);② 离线 wheel bundle 常驻 extras;③ skill asset `wheels/`(长尾兜底)= **模型在沙盒里按需 `pip install --no-index --find-links` 离线装、非系统自动**(守原则 8,同 `cat`/`grep` 词表自然消解)。**`compatibility` = 声明式提示、非触发器**:告诉模型「需要什么 + 装不到用 `wheels/`」,模型 probe 环境、不满足才装;`ImportError` loud-fail → 模型补装。前两层已覆盖绝大多数 → 第三层触发频率低,auto 无条件装 = 反向重机制。
- **典型闭环跑通**:用户传 docx → artifact(blob)→ mount → skill python 拆/改 OOXML 或 pandoc 转 → persist 回 artifact。这一条同时练 A(可选,数据工具变体)、C-mount、skill 执行。
- **`substitution` 整个不做**(纯 chat UI 无命令行式传参口,用户拍):激活走 model-driven `read_skill` / `/skill`,「参数」即会话本身、`$ARGUMENTS` 无着力点;命令替换(撞原则 8)/`${SESSION_ID}` 同不引入。正文里若有占位符 → 保留字面量、预装集人工改写顺手去掉(原则 3 不静默 rewrite)。零运行时模板展开,与决策 4 关掉 `${SKILL_DIR}` 一脉。

**到时再敲定**:skill bundle 挂载点(`/workspace/.skills/`)与 artifact mount(`/workspace/<id>`)的命名空间隔离;沙盒镜像扩容清单(node?LaTeX?权衡镜像大小);wheel bundle 的 arch 化(沿沙盒 plan per-arch 纪律);`pip install` 离线在沙盒激活期的耗时/失败处理;CPU 纪律(解压炸弹/大 zip,沿沙盒原则)。

**进展**:未开工。

### E — 导入门禁与预装(硬门槛 validator + 软门槛 verify agent + pandoc-first 预装)

**做什么**:skill 的不可信输入门禁(原则 2)+ 把用户存货改造成预装集。**last step**(用户:存货预装前先改)。

**包含**(导入单管线两道门,决策 7):
- **硬门槛 = 确定性 validator**(plain code,**阻塞**,跑每次导入含 user 私有上传;**只查 skill 本身良构**):借 `skills-ref`/`skill-validator` 规则 —— 6 字段 schema(在场字段合规、缺失宽容,决策 9)、name↔dir、孤儿文件、未闭合 fence、内部链接解析(含引用 asset/script)、SKILL.md 体量(legibility 警告)。bundle 字节上限归上传路由配额闸(原则 7③,按信任分层、admin 无闸),非 validator 项。
- **软门槛 = verify agent**(**可强制通过**;**系统兼容性归这**):**职责单一 = 判定「能不能在本系统跑」**,懂本系统约束(沙盒工具词表、无网、mount/persist、tool-set 披露)→ 两查:① 用了没有的工具/harness?② 依赖能否满足(有 `compatibility` 声明则据其校验、**缺则从 body/scripts 推断**)vs 镜像栈 ∪ wheel(气隙网:需网即拒/标)?**输出诊断报告(能跑/缺什么),不产 rewrite、不改 body**(改造/简化是人的事,见下条预装集简化 + 原则 3)。**用户私有上传时可选/可跳过**(LLM 成本)。其「系统知识」指向**活文档**(本 plan / skill-authoring 参考),不硬编工具名防漂移。
- **预装集简化(人工,非 verify agent)**(决策 8):预装集就 4 个文档 skill,**开发者在 E 阶段手工分析简化** —— 改 pandoc-first、删三份重复 `office/`、补 `compatibility` 声明、补 asset wheel(若长尾依赖);skill-creator 改造(去 CC widget/subagent-fork 假设)或暂不预装;schedule/setup-cowork **不移植**(驱动 CC 产品 widget)。改完进 `config/skills/`。verify agent 在此只回答「改完这版能不能跑」,**不替人做简化**。

**到时再敲定**:validator 借哪些具体规则、阈值;verify agent 自身是不是个预装 skill(自举);预装集最终名单;ZIP 导入导出端点(决策 6)。

**进展**:未开工。

### F — MCP client(把 MCP server 接成又一个 deferred tool-set provider)

**做什么**:落地 MCP 客户端,让系统能对接只暴露 MCP 接口的内网系统。**架构上 B 已把难的那半(registry/披露)铺好** —— F 是 B 的 provider 抽象的第一个外部消费者(自定义 HTTP 工具/tool-set 是内部消费者),MCP 工具 = 又一个 deferred tool-set,走同一 `search_tools`,不重做工具系统。reference 实现 = `../opencode/packages/opencode/src/mcp/index.ts`(本仓库同级,非项目内;provider-agnostic、无 server 依赖,比 CC 更贴我方)。

**包含**:
- **传输 + 协议客户端**(净新,体力活):用 Python 官方 MCP SDK 接 stdio(`StdioClientTransport` 等价)+ http/sse;JSON-RPC 握手、`tools/list`/`tools/call`、`list_changed` 动态刷新、连接生命周期/重连。照 opencode 那套搬。
- **JSON-Schema → 我方 XML 工具描述适配器**(我方特有的一道):MCP 工具的 `inputSchema`(JSON Schema)+ description 渲染成我方 prompt 级 XML 工具描述 —— CC/opencode 把 JSON schema 直接交原生 function-calling,我方是 XML,故须这层。`callTool` 包成我方 `BaseTool.execute` 形。**外部 tool 名 = check + loud-fail、不 sanitize(调研支撑:见 changelog 06-23)**:MCP 官方 spec 工具名本就限 `^[a-zA-Z0-9_-]{1,64}$`(snake_case >90%)= 我方 XML-tag-safe 字符集 → **合规 server 名字天然通过、零变换**。适配器只**校验**字符集,违规(如 Docker MCP gateway 加前缀产的 `server:tool` 冒号 —— spec 本身亦判违规)→ **loud-fail 跳过该工具 + ops log 指名 server/tool**、留其余可用,operator 上游修。`<server>__<tool>` 回解按**已知 server 前缀剥离**(非 split `__`)→ tool 名含 `__` 也能还原、不撞分隔;server 名 operator 控、撞名 registry 启动 loud-fail。**不做有损变换 → 无「撞名」类**(工具名不变 + server 前缀区分 + MCP 保证 server 内名唯一)。
- **接入 provider 抽象**(B 的回报,近乎免费):打 `source="mcp"` flag、`<server>__<tool>` 命名(`__` 唯一分隔、不带 `mcp__` 前缀,决策 11;server 名跨 provider 全局唯一、撞名 loud-fail)进统一 registry;整 server = 一个 deferred tool-set,索引行常驻、`search_tools` 按 server 名搜加载。**MCP 工具太多的披露问题在 B 落地即解决,F 不再碰**。
- **server 配置(DB + 前端)**:MCP server 随本轮 DB 化 = **DB 实体 + 前端 CRUD**(原则 5/决策 10:operator 出厂 server = `config/mcp/` 启动种子进 DB、不可变;UI 新建 = DB 原生),带 `visibility` DB 列;per-deployment;无联网发现(Non-goal)。**凭证走统一加密落库模型**(同 B-4 `tool_credential`:dynamic MCP server UI 配、seeded 从 env 取值加密落库),DB 存 server 定义 + 加密凭证。
- **部门作用域 wire-in(G 的消费者)**:MCP server 接入 `department_unit_rule`(FK→tool_unit;server 是一个 unit),行 = 例外、方向派生自 server `visibility`(无 `effect` 列,决策 10;**server 的 `visibility` 是 DB 列**,随本轮 DB 化)。整 server 单元粒度(声明/减/加/enable 都在 server 粒度)= 决策 11 解 MCP 动态工具的同一抓手。消费(查表 + 树解析)在 G。
- **动态工具的权限粒度**(决策 11 已定调):MCP 工具动态、没法逐个静态枚举 → 权限锚在 **server 单元**(agent MD 静态声明 server 单元、m2m 减整个 server、运行时发现的工具填进**已授权单元**),即 `<server>__*` 整组粒度、**不逐工具**。比「工具名+参数模式」粒度(仿 CC allow-rules)粗一层:server 粒度够 v0,更细留将来。**关键(reviewer P1#2)**:`department_unit_rule` 只到**整 server unit 粒度** —— **不把发现的 MCP 工具持久化成 DB 行**(否则复活已杀的「已发现集状态机」+ 引入「哪个容器的 `tools/list` 权威」HA 真相问题);逐 MCP 工具 dept 控制 defer。

**到时再敲定**:MCP server DB schema + `config/mcp/` 种子加载细节(落点已定 = DB + 前端,本轮 DB 化);stdio vs http 在内网的取舍(DooD/容器内进程 vs 网络服务);权限 set 粒度的具体语法(与 B 的 `always_allow` 跨 set 语意对齐);`list_changed` 刷新后历史里旧 tool 描述如何过期/自纠(**无「已发现集」状态机**,决策 2;靠常驻索引行变化 + 模型重 `search_tools` 自纠);凭证注入(走统一加密落库模型 = 同 B-4 `tool_credential`,operator/工具级密钥;**用户级身份透传是另一根轴、推迟**,见 Non-goals)。

**进展**:未开工。

### G — 部门作用域授权 + 管理 UI(横切:skill/tool-set/mcp/tool 四类一套机制)

**做什么**:把"哪个部门能用哪些资源"做成**一套统一的部门作用域授权层**(决策 10),服务 skill/tool-set/mcp/tool 四类;agent MD 不动,引擎运行时组合有效集。这是用户要的"admin 给指定部门配特定工具"——**靠作用域涌现,不克隆 agent**。

**包含**:
- **两张 dept rule 表 + 解析**(两表 + 解析地基都在 C 建,G 只加 unit 规则的消费):`department_skill_rule` / `department_unit_rule`(决策 10 已论证为何两张 FK 而非一张 polymorphic);判定 = 用户 `department_id` 走 `parent_id` 祖先链 ∩ 规则集(指父覆盖子树;命中 = 例外(方向 = 资源 `visibility` 默认反向),未命中走 `visibility` 默认),解析与已加载资源取交集(DB FK + cascade → 无陈旧规则)。两表共用同一棵祖先链解析(泛型 helper)。
- **`EffectiveToolset` resolver:G 只加 dept 规则输入(骨架在 B、决策 11)**:骨架 B 已立、C 加 `active_skills`、F 加 MCP 运行时,G 只追加最后一个输入层 `dept 规则`(dept-on-tools),不重碰读点。
  - **「G 是最后接入的实现阶段输入」≠「运行时应用次序」**(reviewer P1):运行时序固定 = universe 展开 → dept 收窄(移除 user 部门未授权 unit、删 key)→ skill enable,**dept 收窄在 skill enable 之前** → private skill 翻不开 dept-denied。输入集合 `(agent_config, active_skills, registry+MCP, dept 规则)` 是列举非次序。
  - **dept 是 resolver 唯一双向(grant/deny)输入**(C/F 都是 enable/填充),在单元展开后、skill enable 之前应用、方向**派生自资源 `visibility`**(行 = 例外成员、无 effect 列)。
  - **工具侧一律 unit 粒度**:`department_unit_rule.unit_name` = unit 名,`toolset`/`mcp` = `<unit>__*` 整组,`tool` = 裸名;skill 走 `department_skill_rule.skill_slug`、按整 skill 授权。一条「按 unit 名匹配整组」路径,无点名 set 成员 → 永不产生 set-vs-member 冲突/override/检测(删 resolver 的成员名 exact-match 分支)。
- **四类接入**:skill(C,`visibility=department` 消费规则)、tool-set(B wire-in)、mcp(F wire-in)、**tool(独立 external 工具 = 自成 unit,一等 DB 行、grant/deny 皆可,unit 存在即可消费;**非** tool-set 成员 —— member 粒度已与 MCP 对齐删除,决策 10)**;builtin 不接入(for-everyone,决策 10/11);参数级粒度(工具名+参数模式)留将来(决策 10)。
- **管理 UI**:admin 按部门分配资源(增删例外成员、方向随资源 `visibility` 派生显示)+ 看每部门有效能力;前端复用 skill 管理页骨架(决策 1 的设置/管理页),按资源类型分 tab(skill / unit)。admin scope 守 `feedback-admin-scope-user-mgmt`(管共享资源/授权,不碰用户数据)。
- **无规则体检 check 端点**(原冗余/孤儿审计整条删除):**同向默认冗余结构上不可表达**(无 `effect` 列、行=例外、跟默认同向=不建行);**祖先/子树冗余仍可表达**(父部门已有同资源例外、子部门再建一条 = 冗余,line 93 父覆盖子树)→ 但**非正确性问题**(解析结果一致),由 UI 建规则时拦截或直接接受、不上审计端点。孤儿由 DB FK `ON DELETE CASCADE` 杜绝、visibility 漂移由「改 visibility 清规则」(决策 10,Manager + reconciler 两路)按构造防住。正确性由「派生方向 + clear-on-visibility + DB cascade」三者按构造保住,不需运行时审计。

**到时再敲定**:**grant 删除** = DB `ON DELETE CASCADE`(两张 dept rule 表 + agent_unit/user_skill 均真 FK,删资源 DB 自动级联,无需 app-side cascade;原"config 类系统不碰 m2m + operator 运维契约"已作废);**改 visibility 清规则**是另一回事 = app-side 定向删 dept 规则行(UPDATE 非 DELETE、DB cascade 不触发,留 user_skill,决策 10);引擎"组合有效集"的缓存粒度(部门/用户级,资源变更少、可缓存);UI 是否支持"部门继承"可视化(指父覆盖子的展示);**参数级粒度**(工具名+参数模式、CC allow-rules 式,需调用时 arg 匹配)留将来(**unit 整体** grant/deny 已在 v0(`department_unit_rule` 整 unit 粒度),unit 之下的逐工具/参数粒度才是将来);**树规则 override 优先级**(深层更具体部门能否翻案祖先规则)—— v0 不做、祖先一刀覆盖整子树(决策 10"指父覆盖子树"),将来要"二级禁/某三级特批"再引入"最具体规则胜"(**届时需加回 per-row 方向/`effect` 列** —— 嵌套反向例外、派生单方向表达不了)。

**进展**:未开工。

## 关键风险

- **披露与 skill 职责漂移**(B/C)—— 一旦把披露塞进 skill,就回到「动态工具对小模型 legibility 负资产」的老坑。守原则 1:披露是工具层、skill 是覆盖层,review 时盯死边界。
- **依赖气隙地雷**(D)—— 社区 skill 默认运行时联网 `pip install`,搬进气隙网必断。`compatibility` 声明 + 软门槛交叉校验做 best-effort 过滤(真兜底 = 运行时 `--network=none` + `--no-index` 响失败);存货零 manifest = 预装集须人工补全依赖声明。
- **bundle = 新不可信输入类**(E)—— skill 脚本在沙盒跑(隔离 OK),但「哪些 skill 准入」是 trust 决策,生态无 signing 层 → 门禁(确定性 validator + verify agent 可运行性评估 + 人审/人手改)是我方唯一 trust 边界,不可省。
- **body 静默改写诱惑**(C/E)—— 工具词表对不齐时,regex rewrite body 看似省事实则脆(改坏比标记坏)。守原则 3:lint 标记 + 人审,绝不运行时静默改。
- **`search_tools` 无 server `tool_reference`**(B)—— 我方纯 prompt 级模拟。**不维护「已发现集」状态机**(决策 2 / changelog 06-17;修正旧表述"追踪须自建"误导实现者造状态机):描述随 tool_result 留历史、被压缩则模型见索引行自己再 search(自纠);避开 server beta 依赖、也不引入状态管理。
- **沙盒镜像膨胀**(D)—— pandoc/libreoffice/科学栈 + 可能 node 烤进镜像,镜像大小与构建时间。权衡「通用即烤、长尾走 wheel」,别把长尾也烤进去。
- **MCP server 粒度授权偏粗**(F/G)—— 决策 11 已定调把 MCP 当 **server 单元**(声明/减/enable 整组、resolver 展开),解了"动态工具无法静态枚举"。**残留风险 = 粒度只到整 server**:授/减一个 server = 它所有工具一起动,**没法逐工具选**(逐工具静态列举对动态发现不可能);更细的工具名+参数模式授权留将来。**与 skill/部门同根**:三方都要引擎从"静态 MD dict 查"改"resolver 解析"——统一解 = 决策 10/11 的 `EffectiveToolset` resolver(**骨架 B 立、C/F/G 各加一个输入层、读点只收口一次**,见 Phase B),别三处各修读点。
- **MCP server = backend 侧网络/凭证边界**(F)—— MCP client 跑在受信 backend(非沙盒),http MCP server 是真实出网 + 凭证持有点,与沙盒「全禁网」正交。须按现有 web 工具的出网纪律(凭证走统一加密落库 = B-4 `tool_credential`、operator 显式配、内网链路审计)管,别让 MCP 成为绕过沙盒网络边界的后门。

## 变更日志

> 本表只记**里程碑架构决策 + 为何不选替代**;逐轮 reviewer 收口、措辞/一致性修正、被取代的中间态留在 **git history**(本文件每个 commit ≈ 一条细节日志)。

- 2026-06-16 **起草 + 脊柱**:三件前置调研(CC 工具披露+MCP、agentskills.io 标准、用户存货)后锁原则/决策。核心 = **原则 1「披露归工具层、skill 正交」**(CC 实证 `SkillTool` 零引用 deferral)。分支走 main(纯加法、无破坏性中间态)。
- 2026-06-16 **激活 = 一次普通 read + 一次普通 mount、零新机制(原则 8)**:opencode 印证激活 = 返回正文的 tool_result、非状态切换,唯一副作用改权限 → L2 独立 `read_skill`、L1 归 ContextManager、bundle 走现有 `mount` 模型驱动 per-turn(删 auto-mount + 跨轮一致性)。substitution 全不做(纯 chat UI 无命令行传参口、参数即会话本身)。
- 2026-06-16 **F 阶段 = MCP client**:provider-agnostic 世界无 30-endpoint 披露现成解 → MCP = 又一个 deferred tool-set provider(架构层近乎免费、剩传输体力活 + JSON-Schema→我方 XML 适配器)。工具名走 **check + loud-fail**(spec `^[a-zA-Z0-9_-]{1,64}$` = 我方 XML-tag-safe 字符集,合规天然通过、不做有损 sanitize;违规跳过该工具)。
- 2026-06-17 **存储/读取/导入收敛**:存储 = blob 真相源 + 反规范化列(无损导出靠 construction;`skill_md` 存正文、剥 frontmatter);**判别线 = SKILL.md 走读通路 / bundle 一切走沙盒** → C/D 阶段线 = 有没有 bundle;mount 固定约定路径关掉占位符。导入 = 硬门槛(确定性 validator、只查 skill 良构)+ 软门槛(verify agent、判系统兼容、可跳过);**两门皆 best-effort,正确性兜底在运行时 `--network=none` + `--no-index` 响失败**。
- 2026-06-17 **部门授权入主线(决策 10 + 新 G)**:解「按部门定制能力」—— **不克隆 agent、靠作用域涌现**。决策 1 三 scope 拆 `visibility` + `default_enabled` 两正交字段 + 稀疏 `user_skill` 覆盖。推迟:用户凭证透传(红线 = 沙盒永不拿凭证)、运行时可编辑 agent。
- 2026-06-17 **skill 能力持有 = 复用 `always_allowed_tools`**(`state["active_skills"]`、不扫历史):纠「无跨 turn 状态」误判。避免坑 = 派生版让压缩静默撤权 → 独立持有让能力轴**不被压缩误伤**。原则 7 收缩(私有化无 tokenizer → 删 token 预算闸,唯一硬上限 = bundle 字节按信任分层)。
- 2026-06-17 **工具权限模型(决策 11)**:**两正交轴**(等级 = 工具定义不可改 / 成员 = enable-only)+ `EffectiveToolset` resolver(单点解析、消多读点漂移)。避免坑 = `disabled` 误塞等级枚举、confirm 洗 auto(全链无人改等级 → 按构造消失)。`search_tools` 跨 turn **靠历史、非状态机**(发现是上下文问题、家在历史)。粒度统一 = **unit-everywhere**(builtin = singleton unit、标准 allowed-tools 原样工作)。
- 2026-06-18 **resolver 骨架从 C 提前到 B**:resolver 是纯基础设施,拖到 C/G 则同 4 读点被 refactor 三轮(退回架构信号)→ B 立骨架、之后每阶段只加一个输入层(C `active_skills`、F MCP、G dept)。
- 2026-06-19 **step-back:external 工具/tool-set/mcp 全 DB 化**(溶解 reviewer 五轮在 config-vs-DB 双轨上的补丁):根因 = `tool` 退化(无 visibility)只因背后是 config 拿不到 DB 字段,reviewer 逼我为退化反复补特例(同形 bug 反复)→ 退回架构。定:external 工具也 DB 化(config 仅种子)、builtin for-everyone 不入表。**连根拔 config-vs-DB 双轨**(软引用同步/运维契约/HA 分层整段消解 → 统一 cascade)。
- 2026-06-19 **通用 `config-seed→DB reconciler` + agent 物化**:种子→DB 抽成通用横切底座(只 ingest 通用、消费各走各),agent 也物化(统一存储 + 撞名检查),config 仍唯一作者真相。落地 = `entrypoint.sh` leader-only 槽(复用 migration PG advisory lock)。**注册表 = 每 turn 一次 DB 快照**(避跨 worker 失效 + 保 turn 内一致)。`agent_unit` m2m 作 agent 宇宙(seeded + UI dynamic),绑定 API 归 B;**skill 去 agent 维度**(全 agent 可见、效果按宇宙收窄)。MCP 只 server 粒度、不持久化发现的工具(避复活已发现集状态机 + HA 真相)。
- 2026-06-19 **dept = 收窄宇宙本身(非末端 AND 闸)**:dept `deny` 直接把 key 从宇宙移除,skill 物理够不到 → **绕不过部门授权 by-construction**。应用序 = 宇宙 → dept 收窄 → skill enable。dept 授权对齐 **unit 粒度**(删 `tool` 点名 set 成员的跨粒度规则 = reviewer 整套冲突/override/check 机器的唯一来源;切 set 子集 = 拆 set)。
- 2026-06-23 **identity = natural key(删 surrogate id)**:skill/tool_unit/agent 不可重名 → natural key 作 PK、m2m 全按 name 引用,内外按名寻址统一。换来 **ABA 由构造消失**(删名即 cascade、无 id 可复用)。牺牲 = 改名不保规则(= 删旧 + 建新 + 人工重授);改内容(同名)仍 UPDATE 保规则。rename 罕见,值。
- 2026-06-23 **dept = 两张 FK 表(非一张 polymorphic)**:unit-everywhere 后物理目标只 `skill` / `tool_unit` 两类 → 拆 `department_skill_rule`(FK→skill)+ `department_unit_rule`(FK→tool_unit),均 DB `ON DELETE CASCADE` → ABA/孤儿/app-side cascade/`resource_type` 列全由构造消失。消费侧本就分型(skill 可见性 vs unit 宇宙收窄),两表反更顺。
- 2026-06-23 **dept 方向改派生 + clear-on-visibility(删 `effect` 列)**:经「visibility 派生 → 显式 `effect` 抗漂移 → 回派生」一个来回。回派生的钥匙 = **「改 visibility = 清该资源 dept 规则」**(Manager UI + reconciler **两路**、定向删、留 `user_skill`)→ 行不熬过 visibility 变更,旧「派生方向静默翻转、反授权」的 bug 失去载体。一行 = 例外、方向 = visibility 默认反向(public→deny、department→grant)。**连削四样**:effect 列、写入拒冗余、check 体检端点、抗漂移论证。未来 override(最具体规则胜、嵌套反向)需把 per-row 方向加回。
- 2026-06-23 **`EffectiveSkillSet` = skill 侧单点可见性 resolver**:戳破 `read_skill`「镜像 read_artifact」漏 department 轴的洞(owner-only 404 会放行 dept-denied skill)→ 立并列 resolver,L1/`read_skill`/mount/**用户侧** REST 共用一条可见性;visibility = 正确性(404)、enabled = UX(仅 L1)。**admin 管理端点不走**(否则被自身部门过滤、看不到别部门 shared skill)。与 `EffectiveToolset` 只共 dept 祖先链 helper。
- 2026-06-24 **A 阶段落地(首个实现)**:`ToolResult.artifact`(声明式)+ `ingest_tool_result` = `create_from_upload` 内核第三调用方;web_fetch 文件旁路(尾缀直连 blob、自带 SSRF + `allow_redirects=False`)。三条 reviewer 驱动的收口决策:① **artifact = 单一表示(content XOR blob)**——删「blob 原件 + 转换文本」双表示(对模型 confusing、backend 无转换路径产生它),「是否二进制」判别从可走私的 `metadata.blob_content_type` 升级为 **`Artifact.has_blob` 列**(顺带 by-construction 灭「text artifact 靠 metadata 伪装 binary」走私,hygiene 剥离遂不需要)。② **落盘统一 loud-fail**——必须落盘却失败(声明式 / 文本超阈值)一律响失败,删 fail-open(退回超长原文正是落盘要防的,合「overflow fails loudly」)。③ **model-facing XML ≠ parsed XML**(已记 CLAUDE.md)——`artifact_slice` 只给模型看、从不解析 → 不追求严格良构(只 escape 免费且不破匹配的 `<title>`;`body` 原文);严格留给被 `ET.fromstring` 解析的 tool call。web_fetch 弃远端 Content-Type(取受控尾缀 MIME,防 svg-XSS / XML 注入)。**部署姿态**:`has_blob` 列就地写进 squash 的 `0001`(全新建库假设,与 0001 注释一致,不加 0002)。
- 2026-06-25 **B 拆 4 片增量合 main + B-1 落地**:开工时把 B 切成 B-1(DB 模型+reconciler+snapshot 读侧)→ B-2(EffectiveToolset resolver+引擎切快照+decision-11 改写)→ B-3(deferred+search_tools+catalog 挪位)→ B-4(provider 缝+前端),细化进 `skill-system-phase-b-design.md`。**关键边界决策 = decision-11 的 agent MD 重写从 B-1 推到 B-2**:B-1 若改 agent MD 格式(成员态/等级分离),引擎(仍读旧 `{名:level}`)就被迫一起翻 → 塌掉增量边界。故 **B-1 = write-only 物化(引擎不动、纯加法),B-2 才把物化接进引擎并同步做 MD 重写**(引擎消费侧翻转是同一件事)。B-1 已合 main(落地见 Phase B「进展」)。逐轮 reviewer 收口(follower 自证 / member `__` / builtin 撞名)留 git history。
- 2026-06-25 **B-2 落地(引擎切 DB 消费 + decision-11)**:`EffectiveToolset` resolver(`src/core/effective_toolset.py`)把 4 读点收成单点;`controller_factory` 每 turn 从 DB 快照重建 agents + external 工具并 `resolve_all`,`dependencies._load_tools` 瘦成 builtin-only(**external 唯一来源自此 = DB**)。decision-11:agent MD `tools:` 值 `auto/confirm`→`enabled/disabled`(等级早在工具类、丢绑定覆盖零行为变化),`parse_agent_seeds` 旧字面量 loud-fail。落地细节见 Phase B「B-2 进展」。
- 2026-06-26 **B-3 落地(渐进式披露,纯 prompt 级)**:`generate_tool_instruction` 拆成 `generate_tool_grammar`(留 system 前缀保 APC)+ `render_tool_docs`(catalog 挪 `<available_tools>` reminder);`tool_unit.defer` 只渲索引行,`search_tools` 内建工具按需补 schema(`wants_context`+`ToolExecutionContext` 走正常路由)。**catalog 留 reminder 是有意**(reviewer 二轮驳「挪回前缀」):C 阶段 skill 能 enable agent-disabled 工具 → 可调集成员易变,放前缀则 toggle 打掉整条历史 APC,放尾部把易变性隔离在本就不缓存的末条 + admin 重放还原那轮真实工具集。**defer⟹可搜索 by-construction**(resolver 有 deferred unit 即 fail-loud 注入 search_tools)。`ToolExecutionContext` 只装非密事实,凭证走 B-4 独立 resolver。
- 2026-06-26 **B-4 后端落地(凭证加密 + CRUD,切 2 片之后端片)**:① **凭证统一加密落库** —— `tool_credential(unit,placeholder,密文,source)` + Fernet 单主密钥不轮转 + `CredentialResolver` lazy 到 execute、只解被调 unit、密文不进快照/catalog;**resolve 一条路 = 读库解密**(seeded 由 reconciler 从 env 取值加密、判变靠解密旧↔比 env 新支持 key 轮换;dynamic 由 UI 写);env `resolve_secrets` 退为 legacy 回落。红线:只发受信 backend、沙盒不拿、per-user 透传仍 defer。② **后端 CRUD**(三层:ToolRegistryRepository/Manager/admin_tools router)—— external 工具 unit/成员 dynamic 增删改(seeded 只读)、agent_unit 挂载(UI 建的工具靠此对 agent 可达)、凭证写-only(GET 永不回明文)。**撞名 by-construction 主闸**落在写入期(欠条已还),DB unique 兜 TOCTOU。③ **provider 缝不新建抽象**(第二消费者 MCP 未到,避过度防御):既有 `provider` 列 + snapshot 分派 + dict-splat 合并已归一化,CRUD 钉死 dynamic=http,F 加 mcp 分支纯加法。**前端是 B-4 第二片(待开工)**。
- 2026-06-29 **B-4 后端 reviewer 两轮收口 + 乙2 + 拆出 B-5/B-6(7 commit 合 main)**:外部 reviewer 两轮(15 findings + 复审 N1-N4)。落地:**①主密钥强制**(`cc5ca36`:`CREDENTIAL_KEY` 缺/格式错 fail-to-start,删运行期所有「缺 key」分支)。**②凭证解密移到 snapshot 读边界**(`a28015e`:`resolve_all_credentials` 一次性解密成纯 dict 灌 HttpTool,引擎循环回归无-DB,溶解 reviewer #4「resolver 骑 turn-long session、无 retry」+ #11)。**③env 缺失保留旧密文不删**(`ecc5cea`:env-absent 是模糊信号[副本 env skew / 注入先后],不在其上销毁机群共享态;撤销走删 config 引用)。**④CRUD 小修批 + ⑤复审收口**(`071db6f`/`ddb3e03`:#2/#5/#7-drift/#9/#10/#12/#14/#15 + N2/N4)。**关键 = 用户 step-back 暴露更深架构**:credential lazy 读骑 turn-long session 的问题 artifact 读**同型**(连接整轮被占 + idle-in-transaction + 无 retry)→ 本质 = turn-long session 不该存在(后台任务要的是「能拿 session 的能力」= db_manager 工厂,非全程攥一条)→ **拆出 B-5「退役 turn-long session」**(N1 明文驻留整轮一并由 lazy-decrypt-at-execute 解决;故 B-4 不做密文预载精修,留 B-5 一次到位)。**乙2 = reconcile 单点门禁**(release/serve 拆分 + nginx 变量 proxy_pass,落地见 Phase B line 197 内联;真机 --scale 验证留 B 收尾在 Mac docker 做)。**CVE 拆出 B-6**。**过度收口教训**:#7「repo.delete_unit 漏删凭证」我做了「自洽 repo」修(`ddb3e03`)→ 引入分层渗透 + 死代码 → 回退(`59d4205`),因为那个「直接调用者孤儿化凭证」无实际触发者(只 Manager 调 + FK cascade)→ 沉淀为**全局 CLAUDE.md step-back 第 1 信号「无可达触发的缺陷别 fix」**(按可达性 triage 先于机制,gate 其余三条)。
- 2026-06-29 **B-5 退役 turn-long session(1445 passed/31 skipped)**:代码勘察发现迁移已 ~70% 完成 —— 对话/事件读写 + artifact `flush_all` 早经 `db_manager.with_retry` 短 session;`controller_factory` 预开的 turn-long `async with db_manager.session()` 只剩两个消费者 = 引擎期 **artifact 读** + **snapshot 读**(含 `a28015e` 的 eager 凭证预解)。落地四块:**①snapshot → 短 session**(`load_registry_snapshot(session, *, db_manager)` 经 `with_retry` 读完即关;它本就全物化成 dataclass/detached HttpTool,无 ORM 逃逸)。**②ArtifactService 持 db_manager + `_run_with_repo` 单点会话边界**:所有 turn-path DB 读(`get_artifact`/`read_artifact`/`get_blob`/`list_artifacts`/`_stage_artifact` 去重+配额/`create_artifact` 存在性查/`ensure_session_exists`)各开短 retrying session,**WorkingSet 留实例做 turn-live 缓存**;序列化在回调内完成(ORM 不逃逸);REST-only `get_version`/`list_versions` 故意留 bound repo(返 ORM 给请求 session 序列化,改短 session 反会制造逃逸)。**③credential 退回 lazy decrypt-at-execute**(撤 `a28015e` eager):`CredentialResolver` 改持 `db_manager`(非 bound repo —— 那是旧 bug),execute 期开短 session 只解被调 unit、cipher 每 resolver 造一次(避 #11 重建)、**零明文缓存**(用完即弃,消 N1 明文驻留整轮)。**④controller setup 5 个对话调用 + 事件持久化 guard** 改走 `_with_db_retry`/能力判定,turn-long `async with` 整段删除。**用户决策(AskUserQuestion)**:本次 snapshot 改短 session 已免费解决凭证的 session-正确性,退回 lazy 唯一额外收益是 N1;N1 在加密落库威胁模型外(主密钥同进程驻留),但**合规/可审计姿态**(解密值不驻留超过使用它的那次调用)优先 → 选退回 lazy。**gate 审计抓到测试盲区**:`create_artifact` 的 DB 存在性查仍用 `_ensure_repository()`,引擎路径(`repository=None`)会 `RuntimeError`,happy-path E2E 测不到 → 修 + 补 `TestShortSessionPath` 回归(db_manager 路径 create/flush/list + 重复名 DB 查)。`a28015e` 的 eager 凭证测试随接口重写为 lazy 资源(明文不常驻 vs WorkingSet 缓存型常驻 —— 两种 lazy 语义在 resolver 注释钉死)。**未碰**:`flush_all` 既有 db_manager 短 session 路径 / `database.with_retry` 本体 / per-query `command_timeout`。
- 2026-06-30 **B-5 reviewer 一轮收口(`b78558e`,1447 passed/31 skipped;复审 clean ship)**:reviewer 抓到一类真问题 —— B-5 把 setup 写包进 `_with_db_retry`,却没满足 `with_retry` 的隐含契约「**fn 必须幂等**」(失败时从头重跑 fn)。两个写不幂等并修复:**①`add_message_async`** 撞 `DuplicateError` 当上次 retry 已成功(吞,照搬兄弟 `start_conversation_async`)—— 否则「首次 commit 后 / `update_title` 前瞬断」重跑撞重抛非瞬断异常逃出 retry → 整轮崩(消息已落库)。**②start_conversation** 的 `conv-{uuid}` 从被重试的 lambda 内提到边界外生成传入 → 重试复用同 id、撞重被吞 → 无孤儿会话。**③`_with_db_retry` 砍 `am`**(12 回调 0 个用,每次 DB 触碰白建 ArtifactService+WorkingSet+repo)→ `(cm, er)`。**④`_stage_artifact` 去重扫描收进一个 `_run_with_repo` 回调**(K 同名冲突 K+1 session → 1)。契约写进 `with_retry`/`_with_db_retry` docstring(把约定变成调用点可见);补 `TestRetryIdempotency`。**沉淀**:凡 `with_retry` 包裹的写必须幂等 = 稳定幂等键在边界外定 + 写遇「已存在」当成功;代码库本有三处范式(`start_conversation_async`/`batch_create`/`_flush_one`),B-5 漏套两个新调用点。**reviewer #5 推迟**(read_artifact dict vs `_serialize_memory`):订正我的 rationale —— 两个**非历史**分支与 helper 逐键相同、可干净收敛,只有**历史版本分支**(`get_version_content`,无 `original_filename`/`has_blob`、`updated_at=None`)真不同需独立;故收敛比「形状不全同」所述更可行,但仍既有/非缺陷/非本轮范围,留独立改。
- 2026-06-30 **B-6 依赖 CVE 升级(DEP-02 重生成 lock,容器内 1446 passed/32 skipped)**:`pip-audit` 当前 lock 抓到 3 包 6 漏洞 —— **pydantic-settings 2.14.1→2.14.2**(GHSA-4xgf-cpjx-pc3j)、**python-multipart 0.0.29→0.0.32**(CVE-2026-53538/53539 解析侧 DoS@0.0.30 + 53540@0.0.31,floor 抬到 `>=0.0.31`)、**starlette 1.1.0→1.3.1**(PYSEC-2026-248@1.3.0 + 249@1.3.1)。starlette 是 fastapi 传递依赖、不在 requirements.txt → **加直接安全 floor 约束 `starlette>=1.3.1`**(pip-compile 尊重该 floor;fastapi 0.136.3 兼容,resolver 无冲突)。按 DEP-02 在 `python:3.11-slim` 内 `pip-compile` 重生成 lock(pin 匹配部署解释器 + linux markers),re-audit `No known vulnerabilities found`;lock diff 极小(只动这 3 包 + starlette 的 `# via` 加 requirements.txt,无连带漂移)。**验证维度**:照新 lock 在干净 slim 容器装全量跑 full suite 通过(本地 1447、容器 1446 —— 差 1 = 容器缺某可选运行时的环境条件 skip,非 bump 回归)。B 阶段功能项收尾,剩真机 --scale 验收。
- 2026-06-30 **B 收尾验收(dev-Mac docker)+ 验收期顺手收口(本次 commit)**:dev 全链路实测,工具生态 + 部署门禁两条线都跑通。**①工具路径**:config-seeded(singleton `cat_fact` 外网 / toolset `diag` 内网 health)与 UI-dynamic 两条都验:建→(config 走 reconcile / dynamic 直接写库即时生效)→快照拾取→注入→调用→回流;defer vs 非 defer 注入形态对照(deferred 出 `<tool_unit disclosure="deferred">` 索引行 + 模型先调 `search_tools` 取 schema;非 defer 成员平铺成完整 `<tool name>` 块、无 unit 包裹、模型直调)；confirm 授权弹窗 + approve/deny 回流;`response_extract` 抽真嵌套字段(`$.fact`)。**②compose 自动 reconcile(乙2 release/serve 门禁)**:`docker-compose.prod.yml` 起 postgres/redis/release/backend,release 在 PG advisory lock 下 alembic+reconcile **一次**(created=6)、Exited 0、backend 靠 `service_completed_successfully` 闸住且带 `AF_SKIP_RELEASE=1` 不重复 reconcile;重跑幂等(created=0 skipped=6);镜像构建顺带验证 B-6 的 lock 可装。**③多副本** `--scale backend=2`:两副本 healthy、共享 Redis RuntimeStore(非 InMemory)、release 仍单容器一次;**Docker 网桥 DNS 对 scaled 服务轮询**实证(中立容器 10 发 5/5)——纠正"需 nginx 才能轮询"的误解:轮询是 Docker 原生,nginx 的作用是**宿主入口 + 防 DNS pinning**(静态 `upstream`/keep-alive 只解析一次会钉死副本,故用变量 `proxy_pass`+`resolver` 强制每请求重解析)。**撞名 fail-closed 实证**:dev 库已有 dynamic `cat_fact` 时再写 seeded 同名 → reconcile loud-fail(`seed 'cat_fact' collides with a UI-created (dynamic) tool unit`),写入前阻断、不覆盖。**未实跑(留真机)**:真 nginx LB 入口(caddy 要 ACME、dev Mac 起不来)+ 跨副本执行续接(A 发起 / B 经共享 Redis resolve interrupt)。**顺手收口(keeper)**:(a) `conversation_manager.__init__` 日志 INFO→DEBUG(无上下文、每请求 new、单轮刷 ~8 次,纯噪音);(b) lead_agent prompt 两处陈旧 —— 删"Each conversation turn starts fresh…看不到上轮工具调用"(与现 MessageEvent-replay 矛盾,CC 也无此话、正面框定为"unlimited context through automatic summarization")、Delegation 去掉硬编码 `web_search`/`web_fetch` 例子(内网禁用这俩,改工具无关措辞);(c) `_example.md` 修格式(注释移入 frontmatter、不可含三连字符否则 splitter 误判闭合、timeout 默认订正 60、"启动加载"改"reconcile 物化")+ 新增 `_example_toolset/`(补 toolset 多成员样板,singleton example 没覆盖的形态)。**遗留小问题(未修,待议)**:`response_extract` UI 标 JMESPath 实为简单点号提取器;`show_example` config MD 不可设(仅 UI、DB 列对两者都在);dev 首次 approve 偶发 "Load failed" 判为 dev 冷启动(疑 CORS 预检冷启)未复现、不可达、不修(step-back 第 1 条),真机生产构建复核。
