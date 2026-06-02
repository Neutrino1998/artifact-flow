# Artifact 层重构 —— 实施计划(live 态上事件轨,单一生命周期)

> 状态:**已实现(单 PR,在 main 上;待多 worker 真机回归)**
> 起草:2026-06-02 · 最后更新:2026-06-02
> 前序产物:
> - `sandbox-implementation-plan.md`(本目录)—— 沙盒 plan;其原则 1「持久态归 TDSQL / 易失态归 Redis+worker」与本重构同源,本重构是沙盒回写/挂载的底座。

## 本文档定位

这是一份 **plan,不是详细设计**:讲清每个阶段做什么、为什么、什么算完成;具体改哪些代码 / 事件字段怎么定 / 序列化细节,留到开工那个阶段再敲定。同时是**跨 session 跟踪文档**——新 session 接手先读「进度」一节。

## 背景:为什么要改(已查实的缺陷)

`ArtifactManager`(`src/tools/builtin/artifact_ops.py`)是 Redis / 多 worker 架构**之前**就存在的早期产物,一个类焊了**五个职责**:① 内存工作缓存(`_cache`)② unit-of-work(`_dirty`/`_new`)③ repo 持有者(绑 db session)④ **进程全局注册表**(`_active_managers`)⑤ 业务逻辑 + 序列化。

因为状态(缓存)和注册表被焊进一个「每 scope new 一次」的对象,一个 turn 会出现**两个互相看不见的实例**(请求处理器一个、控制器一个),于是被迫架了**两座桥**:上传走 DB 即时 commit、REST live 读走进程全局注册表 overlay。

**已查实的核心缺陷(Explore 2026-06-02)**:

- streaming 事件**早已跨 worker**(Redis Streams `XADD`/`XREAD`,`{prefix:msg_id}` hash-tag),client 连任意 worker 都能收到别处 worker 跑的 turn 的事件。
- 但 artifact **改动不发任何专门事件**(只有泛化 `TOOL_COMPLETE`),前端 live view **靠 REST 轮询**;而 REST live overlay 走**进程本地** `_active_managers`,**无 session 亲和**。
- 结论:多 worker 下,前端那次 REST GET 被 LB 打到**非执行 worker** → `get_active` 返回 `None` → overlay 跳过 → **读到 DB 旧值 / 没 flush 的 404**。当年加 overlay 没真修好这个 bug,只是「恰好同 worker」时碰巧对。
- **现在生产是单 worker → 此 bug 潜伏未发**。故本重构是**地基 / 前瞻**工作,不是救火,可与沙盒同期排。

**根因一句话**:artifact 的 live 工作态,是整个系统里**唯一**还被钉在单进程堆上的 turn 状态;其它所有 ephemeral turn 态都搬到了「Redis 流(跨 worker)+ DB 投影(跨中心)」这条轨,artifact 是没上车的那个。`_active_managers` 这个进程全局可变单例,在多 worker 系统里本质是架构错配。

## 目标态:一条生命周期,不分来源

**所有 artifact —— 用户上传的、模型新建的 —— 走完全相同的四步,没有特例:**

1. **进 WorkingSet(不碰 DB)** —— 上传:turn 开始时 closure-carry 把转换后正文经 `ArtifactService.create_artifact(source=user_upload)` stage 进去;模型:`create_artifact` 写进去。**两者是同一个 Service 调用**,只差 `source` tag 与调用时机,因此上传**和模型自建一样 emit `ARTIFACT_CREATED`**(见下:这是冷启动重建能看到上传的唯一来源,不可省)。
2. **turn 期间 live = 纯事件** —— `create`/`rewrite` 发整文、`update` 发精确 span delta,走**现有 Redis 流**,前端 reduce。两类一视同仁。
3. **turn 末 flush → DB(唯一 commit 点)** —— `flush_all` 在 terminal 事件**之前**跑(现有顺序就是),uploads 与模型产物一起落。
4. **`COMPLETE` 后对齐一次** —— 前端拉 DB 权威态(已 flush、已折叠版本)。**只此一次,不中途。**

**Non-goals(本期不做)**:artifact 内容进 `MessageEvent` 持久化(事件 SSE-only,见决策 3);中途 DB re-pull(决策 4);把 WorkingSet 搬进 Redis(决策 5);`delete_artifact`(既有 scope 决定不引入);artifact 路径层级树。

## 贯穿原则

1. **live 走事件轨,真相走 DB,两者不混。** 中途任何时刻都不查 DB 求 live 态;DB 只在 turn 末被对齐一次。这条根除了「中途 re-pull 只能对齐上传、对齐不了模型自建自改文档」的不对称。
2. **一个 turn 一个 WorkingSet,控制器独占,留在 lease-holder 进程内。** 它是该 turn 唯一的写者,别的 worker 只读、只从事件流读。worker 中途死 → 整个 turn 由 lease fencing 重启、WorkingSet 随之丢失,**这正是 ephemeral 该有的语义**。等同引擎 `state["events"]` 现在的做法——artifact 本就是 execution state 的一个切面。
3. **职责拆分:状态 / 编排 / 持久 / 算法 四层各司其职。** 见决策 2。算法体不进 Service。
4. **持久化时机对所有 artifact 一致:turn 末 flush。** 不给上传开即时 commit 的后门(收回先前提法)。用户输入的丢失兜底交给**前端 staged 保留到 `COMPLETE` 成功**,不是后端早 commit。
5. **中途漏事件靠流回放恢复,不靠 DB。** 与 `llm_chunk` 等所有事件的重连恢复同一机制(从 last-seen stream ID 重读),零新增对齐逻辑。

## 已锁定的决策

1. **删 `_active_managers` + REST live overlay。** live view 改由事件驱动;REST `GET /artifacts/*` 退化为**纯 DB 读**(冷启动 / turn 后)。turn 中冷启动的 client = DB 快照(往轮已 flush)+ 本轮事件流回放,二者合并重建,与系统重建历史同法。**推论(易漏)**:本轮上传在 turn 末 flush 前**不在 DB**,故它**必须在 turn 起点 emit `ARTIFACT_CREATED`** 才会进事件流——否则它两条腿都不在,对冷启动 client 隐形。这正是上传 stage 要走 `ArtifactService.create_artifact` 同一路径(而非裸插 WorkingSet)的硬理由:**WorkingSet 纯状态不发事件,Service 才发**。
2. **`ArtifactManager` 拆成四层:**
   - `ArtifactRepository`(已有,保留)—— 纯 DB 访问。
   - `ArtifactWorkingSet` —— turn 级**纯状态**(cache + dirty/new 标记),控制器独占,**无 DB、无注册表**。
   - `ArtifactService` —— **薄编排**:dedup、版本计算、调纯算法、上传 stage、tool-result 持久化、flush(WorkingSet→Repo)、**发事件**。无状态。
   - 纯算法函数(`compute_update` 模糊匹配、grep 扫描)—— 留各自模块,Service **调**它们,不内联。
3. **artifact 事件 SSE-only,不进 `MessageEvent`。** 与 `llm_chunk` 同(避免事件表被正文撑爆;DB artifact 表是唯一持久真相)。`COMPLETE` 后的 DB 对齐就是「以 DB 为准」的兑现,故事件无需持久。
4. **对齐恰好一次,在 turn 末。** 收回先前「中途 gap 就 re-pull」的设计——真相库在 turn 中途对模型自建文档根本不存在,中途 re-pull 不自洽。
5. **WorkingSet 留进程内,本期不进 Redis。** 单写者 + 事件轨已满足跨 worker live;把大正文按编辑频率塞 Redis 是写放大 + cluster-safe key 成本,无必要。(若将来出现多写者 / 跨 worker 协作写诉求再议。)
6. **turn 中禁用「读持久态再取走 / 加工」的前端操作(纯 UX 锁,非后端边界);只允许 live 查看,`COMPLETE` 对齐后启用。** 删 overlay 后 REST GET = 纯 DB,turn 中故意落后于 live(面板靠事件 reduce 补,**走 REST 取持久内容再加工的功能补不了**)。所以 **下载、导出(docx)、版本查看** 这类**读**操作 turn 中前端禁用,否则取到的是旧版、与面板 live 确定性不一致(导出还会对未提交的半改内容做转换)。
   - **判据(读 vs 写)**:这些是**读**——绕过前端只让发起者自己拿到旧版、**不污染共享态、不伤别人** → **前端 UX 锁就够**,后端读契约「返回 DB 持久真相」保持不变、不另设拦截(不一致由 client 自己 consider)。对比**写 / 执行类**(发消息、cancel、conversation tree 开分支、delete)——绕过会冲突 / 损坏 → 由**后端 lease(409)/ ownership(404)强制**,前端按钮只是其镜像,**不在本决策范围**。
   - 这是原则 1「live 与 durable 故意有缺口、turn 末对齐一次」的直接推论;现有「streaming 时隐藏版本选择器」是其特例,本决策推广成一类(读)。
   - **未来拐点**:导出现在是同步 REST 读;沙盒 plan 把导出搬进沙盒(pandoc)后,导出 = 起容器 = **执行**,届时从「前端 UX 锁的读」升级为「**lease 挡的写 / 执行**」——见沙盒 plan。

## 阶段

**交付方式:ABC 作为一个 PR 整体交付,不拆阶段、不留兼容壳。** 下面 A/B/C 只是**逻辑分组 / 自检清单**,不是三次独立 merge——三者耦合本就紧(删 overlay 必须与前端改 reducer 同 merge,否则 turn 中 live view 退化读旧 DB;上传 closure-carry 依赖干净的 WorkingSet;发事件与上传 stage 都走 A 建的 Service),拆开只会制造半迁移假缝和待删的壳。直接在 `feat/artifact-layer`(从 main 切)把新四层**一次建成最终形态**,死端点(`/artifacts/{sid}/upload` + 前端 `uploadFile()`)随这个 PR 一并删除,整支绿了一次性 merge 回 main,再按既有策略 overlay intranet。单 worker 现状使大改爆炸半径小,`COMPLETE` 末尾 DB 对齐是兜底网。

### A — 拆解 ArtifactManager(纯重构,行为不变)

**做什么**:按决策 2 把 god-object 拆成 `ArtifactWorkingSet` / `ArtifactService` / `ArtifactRepository` + 纯算法,**保持现有行为**(registry / overlay / 上传即时 commit 暂不动),先把边界切干净。

**完成 =** `update`/`grep`/`create`/`rewrite`/`read`/`list`/`flush` 全部经 Service 编排、WorkingSet 持状态、算法在纯函数模块;**`ArtifactManager` 直接删除**(不留兼容壳),调用方改指新四层。

### B — live 上事件轨 + 删 overlay(后端 + 前端同期)

**做什么**:
- **后端**:`StreamEventType`(`core/events.py`)新增 `ARTIFACT_CREATED` / `ARTIFACT_UPDATED`;`ArtifactService` 在 create/rewrite/update 时 emit:
  - `ARTIFACT_CREATED` / rewrite → 带整文 + `current_version`;
  - `ARTIFACT_UPDATED` → 带**权威 span delta**(`offset` / `deleted_len` / `inserted_text`,取自 `compute_update` 结果)+ `current_version`。**前端无法从工具 params 反推**(模糊匹配),delta 必须服务端给。
  - 事件 **SSE-only**(决策 3)。
- **前端**:artifact panel 从「轮询 + 刷新」改为**对 artifact 事件 reduce**;按 `current_version` 序号 apply,跳号 → **从 last stream ID 重读流回放**追平(原则 5),不查 DB;`COMPLETE` 后**拉一次 DB 对齐**(决策 4)。**turn 中禁用读类 durable-acting 操作**(下载 / 导出 / 版本查看,见决策 6;纯前端 UX 锁),`COMPLETE` 后启用——别只处理版本选择器,**下载与导出两个 REST 入口最易漏**。(写 / 执行类如发消息 / cancel / 开分支由后端 lease 管,不在前端这层。)
- **删**:`_active_managers` 全局 + 两个 GET 路由里的 overlay 块;REST GET 变纯 DB 读。

**完成 =** 多 worker(或人为把 GET 路由到非执行进程)下 live view 仍正确;前端不再在 turn 中轮询 artifact;overlay / 注册表代码删除。

### C — 上传并入统一生命周期(减法)

**做什么**:
- 上传**不再即时 commit**;`POST /chat` 相二只收集转换后 payload,**closure-carry** 进控制器,turn 开始经 `ArtifactService.create_artifact(source=user_upload)` stage 进 WorkingSet(**走与模型自建同一路径,因此自动 emit `ARTIFACT_CREATED`** —— 冷启动 client 看到上传的唯一来源),随 turn 末 flush 落 DB。
- **顺手修 `_N` 重复**:把「能否 submit」检查挪到任何 commit 之前(409 时啥都没落 → 重发不产生副本)。
- **前端**:staged 上传文件**保留到 `COMPLETE` 成功**为止,作为「turn 中途死丢上传」的用户侧兜底(external cancel 丢未 flush 的东西对 uploads 与模型产物一致)。
- **删死代码**:独立 `POST /artifacts/{sid}/upload` 端点 + 前端 `uploadFile()`(Explore 确认 UI 无 live 调用)。
- **作废** `feat/sandbox` 讨论期那份「closure-carry 单独改造上传」的旧 plan ——它解的是本重构已溶掉的问题。

**完成 =** 上传与模型自建 artifact 在 WorkingSet / 事件 / flush / 对齐四步上逐字相同;409 不再产生 `_N`;死端点移除。

## 关键风险

- **B / 前端必须同期**:先删 overlay 后上前端 = live view 全程读旧。发版顺序要锁。
- **事件正文体积**:大 artifact 的 create/rewrite 整文进事件偏重。可设 live-content 上限,超限事件只带「已变更」信号、靠 `COMPLETE` 的 DB 对齐补全(对齐本就兜底)。开工时定阈值。
- **span delta 权威性**:`update` 的 delta 必须来自 `compute_update`(含模糊匹配命中的真实 span),不可前端反推。这是 B 的硬约束。
- **flush 部分失败**:既有语义——flush 错 → terminal ERROR;对齐拉到的是已 flush 的部分(best-effort,不变)。
- **多 client 一致**:第二个观察者靠 DB 快照 + 流 history 回放重建,依赖 stream history TTL 覆盖 turn 时长(现有 streaming 已具备)。

## 与沙盒 plan 的衔接

见 `sandbox-implementation-plan.md`(本目录)。本重构把「artifact live 态」摆正到事件轨 + DB 投影后,该 plan 的回写(决策 3 回写二分)/ 挂载(原则 4 显式 stage 工作区)就有了干净底座:沙盒回写产物 = 一次 `create`/`rewrite` 进同一个 WorkingSet、走同一条事件轨、随同一个 turn flush,无需为沙盒另造持久化路径。两条约束传给沙盒 plan:

- **排序**:本重构先于沙盒 **C 阶段**(引擎集成)落地;沙盒 **A 阶段二进制存储建在新四层上**(`ArtifactService`/`WorkingSet`/`Repository`),不是已删除的 `ArtifactManager`。
- **二进制走元数据事件**:docx 上传、zip 回写这类 binary **不可塞进 SSE 整文事件**,复用本 plan 的溢出口——只发**元数据事件**(id/文件名/大小/类型),前端 download chip + 按需 REST 取字节 + `COMPLETE` 后 DB 对齐。沙盒 A 的二进制存储**复用此机制,不另造**。

## 变更日志

- 2026-06-02 **实现落地(ABC 一个 PR,在 main 上)**。要点 / 与 plan 的偏差与补充:
  - **四层**:`artifact_working_set.py`(纯状态)+ `artifact_service.py`(编排+发事件,自带独占
    WorkingSet)+ 既有 `ArtifactRepository` + 纯算法模块;`ArtifactManager` 删除,`_active_managers`
    注册表与两个 GET 的 overlay 删除,REST = 纯 DB。`artifact_manager` 变量统一改名 `artifact_service`
    (含 `execute_loop` 形参 + 各测试 fixture —— 2026-06-02 收尾补齐,先前为免改测试注入暂留的"唯一例外"
    已消除;`tests/manual/engine.py` 因仍 import 已删除的 `ArtifactManager` 类、属独立 stale 项,不在此列)。
  - **emit seam(锁 B 方案)**:引擎 `execute_loop` 起点 `service.bind_emit(_emit)`、loop 末 finally 解绑;
    Service 在 create/rewrite/update/上传 stage 直接发 `ARTIFACT_*`(`sse_only=True`)。**决策 3 理由修正**
    (采纳 review 意见):不是「避免撑爆事件表」(模型 create/rewrite 正文本就随 `llm_complete` 持久化一次),
    真正理由是 **artifact 有专属持久家(artifact 表+版本),MessageEvent 里的副本无任何读者** + 与非工具来源
    (上传/persist_tool_result)统一单 emit 站点 + 两受众解耦。
  - **补缺口①(plan 未点破)**:`compute_update` 原**不回传命中位置**,`ARTIFACT_UPDATED` 的权威 span
    无来源 → 扩 `MatchInfo` 加 `offset`/`deleted_len`(三层均填),含 reconstruct 不变量测试。
  - **补缺口②(plan 未点破):delta 重建的同步 base 由后端负责**。delta 的 offset 是相对**本轮前几次
    编辑之后**的内容(非 pre-turn DB),故 DB 只能当**第一条** delta 的 base;要 live 正确须从一个已同步
    base 按序重放每条 delta。这个"同步 base"放后端:`_emitted_base` 让任一 artifact 本 turn 的**首个** live
    事件强制发整文(create/rewrite/首-update 皆整文),其后才发 delta → 前端是个**纯同步 reducer**,
    不必在 live 路径上做 async DB-base 取数 + 乱序 delta 排队,事件流**自包含**(断线重连重放即可重建)。
    替代方案(前端在首条 delta 触发 DB read 当 base)同样可行但把 async 竞态推给前端,故选后端。
    打开中 / 跨轮的文档其 base 本就是 `current.content`(上轮 COMPLETE 已对齐 DB),不靠此机制;它只为
    "从未打开却被后台改"的旧文档兜底。大正文超 `ARTIFACT_LIVE_CONTENT_MAX_CHARS` 则省略正文发信号、靠
    COMPLETE 对齐。
  - **前端**:`artifactStore.liveContent` 事件 reduce(span delta apply / 整文替换 / 自动开面板,
    source='tool' 不抢面板);`selectFromLive` 让 turn 中点列表项也看 live;下载/导出/版本/刷新 turn 中
    隐藏(推广版本选择器既有 guard);COMPLETE 后 `clearLiveContent` + 一次 DB 对齐。
  - **上传并入(C)**:chat 路由只 convert(相一)、不再即时 commit;转换后内容 closure-carry 进引擎,
    `execute_loop` 起点经 `create_from_upload` stage(发 `ARTIFACT_CREATED`、随 flush 落库)。`_N` 重复
    bug 随「提交退到 submit 之后」消失。死代码删除:`POST /artifacts/{sid}/upload` + `create_from_upload`
    的即时-commit 版 + `create_artifact_from_converted`/`convert_and_create_artifact` + 前端 `uploadFile()` +
    `UploadResponse` schema + 孤立的 `artifactAutoOpen` lib;OpenAPI types 已重生成。前端 staged 文件
    `markSent`→COMPLETE `clearSent`/非成功终态 `unmarkSent`,保留到 COMPLETE 成功作上传丢失兜底。
  - **测试**:后端 1053 passed / 31 skipped;前端 187 passed + tsc 干净。新增 `test_artifact_events.py`
    (emit/delta/base-tracking/上传 stage)、`compute_update` span 测、`artifactStore`/`stagedFilesStore` reduce 测。
  - **遗留**:`tools→core` 顶层 import 环 → `artifact_service` 用本地字符串常量 `_EVT_ARTIFACT_*`(drift 测交叉校验);
    多 worker 真机回归(GET 打到非执行 worker 仍正确)尚需手动跑。
- 2026-06-02 加决策 6 并校正读/写边界:turn 中禁用**读类** durable-acting 操作(下载/导出/版本查看,**纯前端 UX 锁、非后端边界**)、`COMPLETE` 后启用——删 overlay 后 REST=纯 DB、turn 中落后于 live,这类功能会拿到旧版。判据:读类绕过只伤自己 → 前端锁够;写/执行类(发消息/cancel/conversation tree 开分支/delete)绕过会冲突 → 后端 lease(409)/ownership(404)强制,不在本决策。**去掉先前误列的 revert**(当前无此功能,只有 conversation tree)。记未来拐点:导出入沙盒后从读升级为 lease-gated 执行。
- 2026-06-02 定交付方式:ABC **一个 PR 整体交付**(`feat/artifact-layer`),不拆阶段、不留 `ArtifactManager` 兼容壳、死端点随 PR 一并删——避免半迁移假缝与待删脚手架。A/B/C 降为逻辑分组/自检清单。
- 2026-06-02 补缺口:明确上传必须 emit `ARTIFACT_CREATED`(收回即时 commit 后,上传在 flush 前不在 DB,不发事件则对冷启动「DB+流」重建隐形)。落法=上传 stage 走 `ArtifactService.create_artifact(source=user_upload)` 同一路径,事件随之自动发出(WorkingSet 纯状态不发、Service 才发)。同步更新目标态步骤 1 / 决策 1 / 阶段 C。
- 2026-06-02 起草。源自一次架构 review:从「上传是否即时 commit」起,逐层追到 `ArtifactManager` 五职责焊死 + 进程全局 `_active_managers` 与多 worker 架构错配(live overlay 跨 worker 静默失效,单 worker 下潜伏)。锁定目标态为「单一生命周期 + live 上事件轨 + DB turn 末对齐一次」,四层职责拆分,删 registry/overlay,上传并入统一生命周期(收回即时 commit 与中途 re-pull 两个先前提法)。粒度定为 plan 级,实现细节留各阶段开工敲定。
