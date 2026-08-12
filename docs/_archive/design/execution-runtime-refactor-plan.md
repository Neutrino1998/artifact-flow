# 执行运行时边界与 Conversation Admission 重构 —— 实施计划

> 状态：阶段 A 完成
> 起草：2026-08-10 · 最后更新：2026-08-12
> 关联文档：
> - `docs/how-it-works.md` —— 当前 Agent turn、SSE、持久化与运行时的产品级说明；本计划完成后同步更新。
> - `docs/configuration/runtime.md` —— 当前 Redis、执行并发和 timeout 的运维契约；本计划不改变现有配置语义。
> - `docs/_archive/design/skill-system-implementation-plan.md` —— 当前 Skill 导入、validator 与 bundle 生命周期；安全扫描产品闭环后续在其基础上另立计划。

## 本文档定位

这是一份 **plan，不是详细设计**。它负责锁定重构目标、责任边界、阶段顺序和验收条件；具体类名、函数签名和文件移动在对应阶段开工时确定。

本计划只重构已经存在且有真实调用路径的能力：Conversation send/delete 准入、执行任务监管和 Agent loop 调用边界。Skill 安全扫描与定时/跑批仅作为未来扩展方向，帮助判断抽象是否错误绑定 Conversation/Web 语义；它们不是本计划的消费者、交付项或验收项，也不会为其提前实现通用 Job 表、调度器或扫描产品状态机。

## 进度

**当前**：阶段 A 已完成，现有生命周期、持久化 retry、send/delete 结构空窗与 Router runtime 契约已有可回归测试锚点。下一步进入 B，只拆分 `create / require_owned / append_message` 持久化语义，不移动 lease、stream 或后台任务边界。

| 阶段 | 内容 | 依赖 | 状态 |
|---|---|---|---|
| A | 冻结现有生命周期与竞态契约 | 无 | 已完成 |
| B | 拆分显式 Conversation 持久化语义 | A | 未开始 |
| C | 收口 Conversation 生命周期并提取 TaskSupervisor | B | 未开始 |
| D | 收成可嵌入 AgentRuntime 与单一 finalization | C | 未开始 |
| E | 集成验收、文档与 embedded smoke | C、D | 未开始 |

依赖关系：

- **B 依赖 A**：先冻结现有行为，再只拆 `create / require_owned / append_message`；B 不移动 lease 或后台任务边界。
- **C 依赖 B**：C 在同一里程碑内先建立 ConversationLeaseHandle 与 TaskSupervisor，再完成 Admission 和全部现有 runtime Router 调用迁移。这样不会出现“协调器先拿 lease、旧 `ExecutionRunner.submit()` 又拿一次”的施工依赖环，也不引入很快删除的 `submit_admitted` 接口。
- **D 依赖 C**：D 落在稳定的 `TaskSupervisor + ConversationExecutionService` 边界上，再从现有 controller 执行路径提取 ConversationTurnHandler 与 AgentRuntime；不在 C 预建临时 Handler 壳。
- **关键路径**：A → B → C → D → E。

分支与发布策略：从 `main` 新建单一特性分支，全部阶段在该分支完成后一次合入；阶段间用可回归的独立 commit 保留定位能力。C 内部子步骤属于一个完整迁移里程碑，不要求发布中间态。最终合入态不保留 `ExecutionLauncher`、`create_if_missing`、旧 `ExecutionRunner` 兼容壳或临时 `submit_admitted`；半完成分支不发布。

## 目标与范围

在不增加 Chat DB 执行状态、不引入 Redis/PostgreSQL 状态同步的前提下，消除 send/delete 的检查—加锁空窗，并把进程内任务监管和 Agent loop 收成不依赖 Web/Conversation 语义、可被轻量嵌入式调用复用的边界。

**本计划包含**：

- 将 Conversation 创建与存在性确认拆成明确互斥的 API；服务端稳定 ID 的 DB 瞬断重试保持幂等，已有 Conversation 永远不能走创建入口。
- 增加统一 Conversation Admission/生命周期协调入口，使 send、single delete、bulk delete 使用同一个 conversation lease 线性化。
- 引入 Conversation 专属 LeaseHandle：acquire 成功即开始 heartbeat，负责 ownership lost、fencing、后台任务交接和 compare-and-release。
- 保留并收口现有 `lease + interactive + owner CAS` 机制，不改变用户可见的 QUEUED/RUNNING、inject、cancel 和 reconnect 语义。
- 从 `ExecutionRunner` 提取不含 Conversation、Redis、SSE 语义的 TaskSupervisor、单一 capacity gate 和 cleanup scope；不预建 named pools。
- 将现有 Pi-style loop 收成可独立调用的 AgentRuntime 边界，保留 nested-serial、事件顺序、timeout 和 terminal 不变量。
- 删除没有独立生命周期价值的 `ExecutionLauncher`，并迁移 Chat/Admin Router 当前所有 `runner.store` 调用，让 Router 只做 transport 映射。
- 增加无需 FastAPI、SQLAlchemy、Redis 初始化即可运行的 embedded smoke，验证未来 client 复用边界。

**Non-goals（本期明确不做）**：

- **不给 Conversation/Message 增加 `status`、`active_message_id` 或通用 `job_run` 字段** —— Chat 的 durable truth 已由 Conversation、Message、MessageEvent 承担，live ownership 继续归 Redis；新增字段只会制造双写。
- **不实现或验证 Skill 安全扫描产品闭环** —— scanner 选型、candidate/admission 状态、隔离执行和可选 vetter agent 均另立计划；本期只避免把通用核心绑定到 Conversation。
- **不实现 scheduler、cron、retry queue 或统一 Job 管理后台** —— durable 跑批需要真实需求下的 PostgreSQL claim/run 模型，不能从 Chat lease 反推一套空框架。
- **不做正式 SDK 与向后兼容承诺** —— 只建立清晰的内部 Python 包依赖和 embedded smoke。
- **不改 Pi-style nested-serial 引擎语义** —— 本期移动调用边界，不重写循环、工具协议、compaction 或 subagent 路由。
- **不改变事件/Artifact 持久化哲学** —— events-first、MessageEvent 历史、turn 末 flush、late-cancel 和 terminal 单一裁判保持不变。

**完成后的可观察结果**：

- 在 lease 有效且 ownership 可确认时，send 先取得 lease则 delete 稳定返回 409；delete 先取得 lease并提交后 send 稳定返回 404。lease 续租失败或归属不明时 fail closed：检测后停止新的前向执行并进入有界 finalization/cleanup；允许已经在途的 DB 操作提交或旧 turn 在检测前短暂重叠，但已删 Conversation 不会被隐式创建复活。
- Router 不再直接访问 `runner.store`，也不再理解 lease、interactive 或 cleanup 的实现细节。
- TaskSupervisor 可运行一个完全不含 conversation/stream 的 workload，并保持并发、cancel、shutdown 和 cleanup 行为。
- AgentRuntime 可被 embedded smoke 直接调用，不启动 Web、DB 或 Redis。
- 现有 Chat、Admin runtime observability、SSE、inject/resume/cancel、timeout、后处理与 Sandbox 清理回归通过。

## 原则与决策

1. **有效的 Conversation lease acquire 是用户 send/delete 的协调线性化点，权威 DB 校验必须发生在其后。**
   - 为什么：当前 send 在 Router 先检查 PostgreSQL，直到 `ExecutionRunner.submit()` 才获取 Redis lease；delete 可在空窗内提交。
   - 怎么做：附件转换、本次字节计算等无共享写的准备留在 lease 外；取得 lease 后立即 heartbeat，再完成 create/require、ownership、parent/active-branch 解析和最终准入，成功后才创建 stream、提交后台任务。后台任务接管同一个 LeaseHandle，不再次 acquire。
   - 失败契约：Admission/delete 期间续租失败或 ownership 无法确认时 fail closed；运行中检测到 loss 后不再开始新的 admission、engine round 或 tool work，只允许旧 turn 完成有界审计 finalization/cleanup。跨 Redis/PostgreSQL 的 lease 不是网络分区下的形式化事务，不承诺此时仍返回精确 409/404，也不承诺在检测前零重叠；已在途 DB 操作可能提交，显式 require/FK 保证不复活，owner CAS 保证旧 cleanup 不清新 owner，stream/lease 释放失败 loud-log 并由既有 TTL 有界回收。
   - 边界：管理员级联删除和库外写不遵守 conversation lease；由 DB FK 和既有 post-processing fail-soft 兜底，不为其增加跨库锁。

2. **新 Conversation 只有显式 create，已有 Conversation 只有显式 require；稳定 ID 的重试幂等不等于隐式创建。**
   - 为什么：`create_if_missing` 让“创建”和“确认存在”共享入口，调用方漏传一个布尔值即可复活已删实体。
   - 怎么做：新会话/消息 ID 在 DB retry 边界外由服务端生成并保持稳定；同一次操作若已 commit、仅确认响应失败，重试撞相同 ID 视为幂等成功。客户端提供的已有 Conversation ID 只能 `require_owned`，不能调用 create。删除 `ensure_conversation_exists(create_if_missing=...)` 和 `add_message_async(create_conversation_if_missing=...)`；`append_message` 只接受已有 Conversation，但保留 parent 校验、Message insert、`active_branch` 更新和根消息（`parent_id is None`）title 更新，父实体消失由 require/FK loud-fail。
   - 可达性：真实 UUID 碰撞在当前受控 ID 生成路径不可达，不为理论碰撞新增 nonce/version/operation 状态；如果未来允许客户端创建指定 ID，再单独设计幂等键契约。
   - 不选方案：继续补 `False` 和二次 exists 检查 —— 同形竞态已重复出现，继续补调用点只会扩大隐式契约。

3. **Lease、interactive、durable DB 与 TaskSupervisor 是不同状态轴，不做互相投影。**
   - Lease：当前哪个 owner 有权写共享资源，覆盖 QUEUED、RUNNING、post-processing 和 cleanup。
   - Interactive：agent loop 是否真的可交互，只覆盖允许 inject/cancel/resume 的 RUNNING 窗口。
   - PostgreSQL：Conversation/Message/MessageEvent 的长期事实，不记录 Redis live 状态。
   - TaskSupervisor：单进程 task 引用、容量、cancel/shutdown 和 cleanup，不承担崩溃恢复。

4. **TaskSupervisor 复用进程内机制，不携带 Conversation 语义。**
   - 只拥有 task registry、一个现有 Agent 执行所需的 capacity gate、TaskScope、cleanup、shutdown 和观测。
   - 不 import Conversation、RuntimeStore、StreamTransport、SQLAlchemy 或 FastAPI。
   - Conversation lease heartbeat/fencing、interactive 和 stream 由 Conversation 执行协调层组合；TaskScope 只提供通用的取消、事件和 LIFO cleanup seam。LeaseHandle 与 TaskScope 同阶段落地但保持为不同对象。
   - 当前只有一个 semaphore 消费者；第二类真实 workload 出现前不引入 pool name、pool registry 或多池配置。

5. **AgentRuntime 返回 engine outcome，不裁决最终 terminal；ConversationTurnHandler 是唯一 finalization owner。**
   - AgentRuntime 接收 invocation、agents/tools、hooks、event sink 和 cancellation，返回包含 engine state 与 stop reason（complete/timeout/cancel/error）的 outcome；stop reason 是事实输入，不是 terminal event。
   - ConversationTurnHandler 负责历史加载、Message 建立、用户 scope 装配，以及 runtime 子任务的 cancel-and-drain；随后由受保护的单一路径完成 Artifact flush、terminal 决策、Event persistence 和 Message.response 更新。
   - 外层 task cancel 不能绕过落盘：先取消并 drain runtime 子任务，再进入可承受 late-cancel 的 post-processing。最终 terminal 仍由 flush 后 dispatcher 决定，保持 events-first 和 response slot-claim-before-await。
   - `entry_agent` 参数化，但默认 Chat 仍从 `lead_agent` 开始；不得破坏单 task、单 active agent 和事件审计顺序。

6. **不提前统一未知 workload 的持久化模型。**
   - Chat 继续使用 Redis conversation lease；当前没有真实调用路径的 durable run/claim、重试和调度语义不进入本计划。
   - 未来扩展只提供一条负向设计约束：TaskSupervisor 与 AgentRuntime 不得要求调用方伪装成 Conversation。

7. **最终形态直接替换旧边界，不保留薄兼容壳。**
   - `ExecutionLauncher` 没有独立 policy 或 lifecycle，职责并入 ConversationExecutionService。
   - `ExecutionRunner` 的通用部分进入 TaskSupervisor，Conversation 部分进入协调层；Chat 的 active-stream/inject/cancel/list/resume/delete 和 Admin observability 均迁移到应用服务后删除原类，避免留下 `runner.store` 服务定位器。

**尚未决定**：

- TaskScope、ConversationLeaseHandle 与 stream/sandbox cleanup 的具体交接 API —— 最晚 C 开工前用“acquire 即 heartbeat、提交只交接不重拿、易失资源先清、lease 最后放”的测试反推最小接口；不能用一组通用生命周期 callbacks 重建新的 god-object。
- AgentRuntime 的最终模块路径和 outcome DTO —— 最晚 D 开工前根据现有 `execute_loop`、controller post-processing 和 embedded smoke 的最小共同输入确定；DTO 必须表达 stop reason，但不能携带已裁决 terminal 或 Repository。

## 目标责任图

```text
FastAPI Router
    ↓ transport only
ConversationExecutionService
    ├─ acquire/finalize admission
    ├─ ConversationLeaseHandle (immediate heartbeat / fencing)
    ├─ stream + interactive + runtime commands
    └─ submit admitted workload
            ↓
TaskSupervisor / single capacity gate / TaskScope
            ↓
ConversationTurnHandler
    ├─ DB history + Message
    ├─ per-user Agent/Tool/Skill assembly
    ├─ cancel-and-drain runtime child
    ├─ sole terminal decision + persistence
    └─ AgentRuntime.run(...)
            ↓
Pi-style AgentRuntime

RuntimeStatusReader ────────────→ lease/interactive read protocols
Chat/Admin Router ──────────────→ RuntimeStatusReader

Embedded caller ────────────────→ AgentRuntime.run(...)
```

## 实施阶段

### A — 冻结生命周期与竞态契约

**做什么**：在移动代码前，用可达场景锁定现有正确行为和本次要消除的空窗。

**包含**：

- send/delete 两种获胜顺序的确定性并发测试。
- 新 Conversation 创建、稳定 ID DB retry、已有 Conversation require、parent 显式/自动/根分支三态测试。
- QUEUED 持 lease 但不 interactive、RUNNING CAS、旧 owner clear/release 不影响新 owner测试。
- cancel、timeout、外层 task cancel 特殊持久化、post-processing、Sandbox cleanup-before-lease-release 的现有回归锚点整理。
- 盘点 Chat/Admin Router 对 `runner.store` 的全部真实调用，锁定 active-stream、inject、cancel、list、resume 和 observability 的现有返回契约。
- 初始化失败的用户响应与 ops 日志契约；4xx/5xx 按现有日志规则覆盖。

**不包含**：

- 生产代码重构或新增状态。

**验收项**：

- 测试能够稳定复现当前 send 检查与 lease acquire 之间的结构空窗，而不是依赖 sleep。
- 所有既有 ExecutionRunner、RuntimeStore、Chat delete/cancel/controller tests 继续通过。
- 测试清楚区分用户 delete、管理员级联删除与库外删除的可达边界。

**进展**：

- 2026-08-12 完成。阶段内只修改测试和本文进度，没有修改生产代码、API schema 或运行时状态。
- send/delete 双顺序由 `RuntimeStore.try_acquire_lease` 测试包装器 + `asyncio.Event` 精确控制，不依赖 `sleep`：send 先取得 lease 时 delete 稳定 409；delete 在 send 已完成 DB 检查但尚未 acquire lease 时提交，稳定复现当前 send 仍返回 200 的结构空窗，同时证明后台 no-create 防线不会复活 Conversation 或留下 Message。C 完成后同一用例把 send 预期改为同步 404。
- 固定 Conversation/Message ID 的测试通过真实 `DatabaseManager.with_retry` 模拟“首次 commit 成功、后续确认/步骤瞬断、fresh session 整体重试”；Message Duplicate 分支继续完成根消息 title，锁定 B 不得提前 return。
- parent omit / explicit ID / explicit null 三态已在 controller 调用边界冻结，并由 Repository 测试继续覆盖 parent 归属、`active_branch` 与根消息 title。
- QUEUED/RUNNING、lease owner CAS、旧 owner cleanup、外层 task cancel 持久化、timeout、post-processing、Sandbox cleanup-before-lease-release 均有回归锚点；Redis 集成测试另锁定 stale owner 的 clear/release 不影响 replacement owner。
- Chat runtime 迁移清单：active-stream、inject、cancel、conversation list、single/bulk delete、resume；Admin 迁移清单：conversation list、event detail、active stream，以及 `/admin/runtime` 的 active conversation/task snapshot。非 Router 消费者另有 `controller_factory` 的 hooks/interactive clear、ExecutionRunner 自身 lease/heartbeat、lifespan SandboxReaper 和 observability sampler，C 迁移时不能只搜索 Router。
- 错误双契约已冻结：初始化失败和未捕获 5xx 同时具备脱敏用户响应、request ID 与 exception ops log；active delete 409、上传配额 413、非法 parent 422、stale resume 409 保留可定位 warning；自明 404/401/schema 422 不增加噪音日志。
- 删除边界明确分层：用户 single/bulk delete 走 conversation lease；管理员删用户级联与库外删除不走该 lease，继续由 FK 和 controller post-processing fail-soft 测试兜底，不把后两者误写成 Admission 保证。
- 验收：阶段 A 目标测试全部通过；Redis RuntimeStore 集成测试 24 项通过；完整后端套件 2018 项通过、67 项按环境跳过，测试容器缺少文档/Sandbox 依赖导致的 3 项在补齐已钉住依赖后单独复跑全部通过。

### B — 拆分显式 Conversation 持久化语义

**做什么**：只修正 Manager/Repository 调用语义，使“创建”和“确认已有”在 API 形状上互斥；本阶段不移动 lease、stream 或后台任务时序。

**依赖**：A 的持久化与 retry characterization tests 稳定。

**包含**：

- Manager 层提供明确的 `create`、`require_owned` 和 `append_message` 语义；`append_message` 只接受已存在的 Conversation，绝不创建父 Conversation。
- 新 Conversation/message ID 必须在 `with_retry` 边界外稳定生成；同一次写已 commit、确认响应瞬断后的 Duplicate 按现有契约视为幂等成功。
- 已有 Conversation 的客户端输入 ID 只能进入 `require_owned`；任何 controller/message 路径都不能用它调用 create。
- 删除 `ensure_conversation_exists(create_if_missing=...)`、`add_message_async(create_conversation_if_missing=...)` 及对应布尔参数。
- Message append 保留 parent 归属校验、Message insert、`Conversation.active_branch` 更新和根消息 title 更新；父行消失时由 require/FK fail closed。

**不包含**：

- 统一 Admission、lease 获取位置、heartbeat、TaskSupervisor、Router runtime 调用迁移或 AgentRuntime 提取。

**验收项**：

- 新 Conversation 只有一个服务端 ID 分配 + 显式 create 调用路径；已有 ID 只有 require 路径。
- 固定 ID 的 Conversation/message 写重复执行保持幂等，锁定“首次 commit、确认响应失败、整体重试”的真实场景。
- append 合法 parent 后 `active_branch` 指向新消息；非本 Conversation parent 继续失败；`parent_id is None` 保持现有 title 更新行为。
- Message/active_branch 已 commit、title 更新前瞬断时，整体重试允许吞同 ID Duplicate 后继续完成 title；不得在 Duplicate 分支提前 return。
- 已删除已有 Conversation 不能被 append/controller 路径复活；Message FK 失败不得降级为自动创建。
- 不为理论 UUID 碰撞新增 nonce、version 或 operation 状态。
- 本阶段完成后现有 send/delete 行为不变，A 的 characterization tests 继续通过。

**进展**：

- 尚未开始。

### C — 收口 Conversation 生命周期并提取 TaskSupervisor

**做什么**：一次完成 lease 所有权、进程内任务监管、统一 Admission 和现有 Router runtime 调用迁移，消除旧 Runner 自行 acquire 带来的施工依赖环。

**依赖**：B 已建立明确、幂等且不复活的持久化 API。

**内部施工顺序（同一里程碑，不发布中间态）**：

1. 建立 ConversationLeaseHandle/协调器：acquire 成功即 heartbeat，暴露 ownership-lost/fencing，并以 owner CAS 释放。
2. 从 Runner 提取 TaskSupervisor/TaskScope：task registry、重复 run ID 拒绝、单一 capacity gate、cancel/shutdown、long-running 观测和有界 LIFO cleanup。
3. 建立 ConversationExecutionService：发送准入、single/bulk delete、stream/interactive 生命周期以及 pre-acquired LeaseHandle 向后台 task 的一次性交接。
4. 迁移 Chat/Admin 当前全部 `runner.store` 调用，删除 `ExecutionLauncher`、`ExecutionSpec`、`ExecutionRunner` 与薄兼容入口。

**包含**：

- 发送流程：lease 外完成上传纯转换和软配额准备；服务取得 LeaseHandle 并立即 heartbeat；handle 内完成 create/require、ownership 和 parent/active-branch 最终校验；成功后创建 stream 并提交给 TaskSupervisor。提交交接同一个 handle，绝不再次 acquire。
- single/bulk delete 使用同一 LeaseHandle，acquire 后立即 heartbeat，在 handle 内重新完成权威 existence/ownership 判断和 DB DELETE。
- Admission/delete 的 lease loss 或 Redis 归属不明均 fail closed；执行任务接管后检测到 loss 触发 fencing/cancel，停止新的 engine/tool 前向工作，但保留旧 turn 的有界审计 finalization 与 cleanup。
- RuntimeStore 按消费者拆成 ConversationLeaseStore、InteractionStore、InterruptStore、InjectQueue 等小协议；Redis/InMemory 可继续由同一对象实现。
- Chat active-stream、inject、cancel、list、resume、single/bulk delete 迁移到 Conversation 应用服务；Admin active execution/detail/stream observability 迁移到只读 RuntimeStatusReader 或等价窄服务。
- Router 只做 auth、parse、domain-to-HTTP 映射；自明 404 不加噪音日志，409/上传业务拒绝按现有规则保留可定位 warning。
- QUEUED 持 lease 但不 interactive；取得唯一 capacity gate 后用 owner CAS 标记 RUNNING；engine 退出立即 clear interactive，lease 持续覆盖 post-processing 和 cleanup。
- Sandbox 等易失资源通过 TaskScope 有界 LIFO cleanup，仍先于 stream/lease 释放；cleanup 异常不阻断后续释放。
- 至少一个无 Conversation/SSE 的测试 workload 通过 TaskSupervisor，证明通用层无语义泄漏；本期不加入 named pools 或占位 pool 配置。

**不包含**：

- AgentRuntime/engine outcome 提取、durable Job queue、PostgreSQL run claim、scanner process、scheduler 或第二类 capacity pool。

**验收项**：

- 短 TTL + 人为阻塞 Admission/delete 的测试证明 heartbeat 从 acquire 起生效，而不是等后台 task 启动；提交只交接 handle，不发生第二次 acquire。
- lease 有效时：send 先获 lease → delete 409；delete 先提交 → send 在 handle 内 require 得 404。
- lease loss/续租异常时：检测后不再启动新的 admission、engine round 或 tool work；旧 turn 仍可按 message ID 完成有界审计 finalization。测试允许已在途 DB 操作提交和检测前短暂重叠，但必须证明显式 require/FK 不复活已删 Conversation、旧 owner CAS cleanup 不清除新 owner、stream/lease cleanup 失败 loud-log 且由既有 TTL 有界回收。
- 不把协作式 cancel 描述成同步 CPU 工具的强制抢占；继续依赖现有工具 CPU-cost discipline，lease/fencing 测试只验证系统可实际提供的检测后行为。
- TaskSupervisor 不 import Conversation、Redis、StreamTransport、SQLAlchemy 或 FastAPI，且只包含一个 capacity gate。
- Chat/Admin 当前全部 runtime 操作保持既有 404-not-403、409、429、QUEUED/RUNNING、resume 和 observability 语义；代码库不再存在 Router 直访 `runner.store`。
- bulk delete 保持逐项 best-effort，不跨 entity 发 Redis multi-key 命令；跨 conversation 列表继续 pipeline fan-out，Cluster-safe。
- 成功、初始化失败、排队取消、fencing、shutdown 和 cleanup timeout 均收口到一致的资源释放顺序。
- `ExecutionLauncher`、`ExecutionSpec`、`ExecutionRunner` 和临时 `submit_admitted` 均不存在。

**进展**：

- 尚未开始。

### D — 收成 AgentRuntime 与单一 finalization

**做什么**：把 Pi-style loop 暴露为独立 runtime 调用边界，并将正常、timeout、协作取消、外部 task cancel 和 error 都汇入 ConversationTurnHandler 的唯一 post-processing 路径。

**依赖**：C 的 TaskSupervisor、TaskScope、LeaseHandle 和 Conversation 应用服务稳定。

**包含**：

- AgentInvocation/RuntimeHooks/EventSink/EngineOutcome 的最小内部契约；outcome 只包含 engine state 与 stop reason，不生成或持久化最终 terminal。
- `entry_agent` 参数化；Chat 默认仍为 `lead_agent`，internal/用户 scope 权限装配保持当前规则。
- ConversationTurnHandler 持有历史、Message、Artifact、事件持久化和 post-processing；AgentRuntime 只运行智能循环并归一化 complete/timeout/cancel/error 原因。
- 外层 task cancel：取消并 drain AgentRuntime 子任务，取得可 finalization 的 outcome/state，再进入受保护的 post-processing；不得让 `CancelledError` 直接绕过 Artifact/Event/response 持久化。
- 保留 timeout 只包 engine loop、post-processing 在 timeout 外；复用 late-cancel ledger 处理 cancel 落在 engine-exit、flush、events、response 或 metadata await 的情况。
- 由 flush 后唯一 dispatcher 决定 terminal；保持 events-first、Message.response slot-claim-before-await、error sanitization 和 terminal precedence。

**不包含**：

- 重写 engine 内部状态结构、并行 subagent、middleware、checkpoint 或 durable resume。

**验收项**：

- AgentRuntime 不 import FastAPI、Redis、StreamTransport、ConversationManager 或 Repository，也不 emit/persist 最终 terminal。
- complete/timeout/cooperative cancel/external cancel/error 分别产出 engine outcome，最终 terminal 只由 ConversationTurnHandler 的 flush 后 dispatcher 产生。
- 外部 cancel 在 runtime 执行中、runtime drain、engine-exit、artifact flush、event persistence 和 response update 各落点的确定性测试通过；没有未落盘事件、双 terminal 或 response-before-events。
- nested serial、同轮多 tool/subagent 顺序、native-call closure、compaction 和 permission interrupt 回归通过。
- Conversation SSE/replay、active stream、inject/resume/cancel、timeout、error sanitization、terminal persistence 与 Sandbox cleanup 行为不变。

**进展**：

- 尚未开始。

### E — 集成验收、文档与 embedded smoke

**做什么**：证明目标边界既保持 Web 产品行为，又能在无服务端基础设施时独立运行。

**依赖**：C、D 全部完成（C 已传递依赖 B）。

**包含**：

- Embedded smoke：加载 Agent/Tool 配置，使用 InMemory/no-op hooks 与 event sink 直接调用 AgentRuntime；不初始化 FastAPI、DatabaseManager、Redis 或 SSE。
- Web 端到端 smoke：新/已有会话发送、排队、inject/cancel、断线重连、single/bulk delete、terminal 和 Artifact 持久化。
- Admin runtime observability smoke：active execution 列表、会话详情和 active stream 查询保持只读行为。
- 多 worker Redis 目标测试：lease owner、interactive owner 和旧 terminal/active-message compare-and-clear 不回退。
- 更新 `docs/how-it-works.md` 与 `docs/configuration/runtime.md`，说明 runtime、Conversation Admission、live state 和 durable state 的新边界。
- 文档明确标注通用层不依赖 Conversation/Web；不记录尚未实现功能的接入契约，也不建立占位表或死代码。

**验收项**：

- Embedded smoke 证明 runtime 包导入和执行不触发服务端全局初始化。
- 后端完整测试、目标 Redis integration tests 和必要 Docker/Sandbox manual smoke 通过。
- OpenAPI/前端类型仅在 API schema 实际变化时同步生成；前端发送/删除/stream 行为回归通过。
- `rg` 确认无 `create_if_missing`、`ExecutionLauncher`、Router 直访 `runner.store` 等退役入口。

**进展**：

- 尚未开始。

### 整体完成条件

- A–E 全部完成并通过各自验收，特性分支一次合入 `main`。
- 有效 lease 下的 Conversation send/delete 串行化和“已删不可复活”由结构与并发测试共同保证，不依赖调用点记忆布尔参数；lease 归属不明时统一 fail closed。
- Lease、interactive、TaskSupervisor、AgentRuntime、DB 持久化五类职责在代码依赖上可辨认，没有新增跨 Redis/PostgreSQL 同步机制。
- Web Chat 全功能回归与 embedded runtime smoke 同时通过。
- Chat/Admin Router 不再直访 runtime store；旧 facade/兼容壳/重复状态和迁移脚手架已删除；代码与验收中没有扫描/跑批的占位实现。

## 未来扩展方向对本计划的约束

本节不是接入设计或后续承诺，只用于审查本次抽象是否过度绑定当前 Web Chat：

- Skill Scan 提醒我们：AgentRuntime 不应要求 Conversation、SSE 或 Chat Message 才能运行。
- 定时/跑批提醒我们：TaskSupervisor 只管理进程内执行，不应把 Redis conversation lease 冒充成通用 durable job 状态；是否需要多 capacity pool 等第二个真实消费者出现后再决定。
- 轻量嵌入式调用提醒我们：核心 runtime 的导入和执行不应触发 FastAPI、数据库或 Redis 初始化。

除上述负向约束外，本计划不定义这些功能的数据模型、入口、调度、隔离、重试、状态机或验收用例；待出现真实需求与调用路径时分别设计。

## 关键风险

- **Lease acquire、heartbeat 与任务交接之间出现新空窗** —— 触发信号：Admission/delete 阻塞超过 TTL 后出现第二 owner，或提交后台任务时再次 acquire 自冲突；应对：LeaseHandle acquire 成功即 heartbeat，提交只转交同一 handle，短 TTL 阻塞测试覆盖 acquire→admission→queue→run→post-process 全程。
- **跨 Redis/PostgreSQL 的保证被表述过强** —— 触发信号：续租状态未知时仍开始新的前向工作，或声称故障时必然返回 409/404、零 DB 在途提交、零 engine 重叠、零残留；应对：ownership 不可确认即 fail closed，精确返回码只承诺在有效 lease 下；检测后只允许有界审计 finalization/cleanup，显式 require/FK 防复活，owner CAS 防误清，清理失败 loud-log + TTL 回收。
- **DB retry 幂等契约被 create/append 拆分破坏** —— 触发信号：首次 commit 后确认响应瞬断，重试撞 Duplicate 使整轮失败；应对：ID 在 retry 外稳定生成，仅受控新建调用路径将同 ID Duplicate 视为幂等成功，不为不可达 UUID 碰撞加状态。
- **Lease 所有权移交或 cleanup 顺序退化** —— 触发信号：submit 失败残留 lease/stream，或 Sandbox 在 lease 释放后仍存活；应对：A 先锁定所有退出矩阵，TaskScope 采用有界 LIFO cleanup，lease 最后释放。
- **把 interactive 错当通用 running 状态** —— 触发信号：QUEUED 可 inject、post-processing 仍接收 cancel，或非对话 workload 被迫实现 interactive；应对：协议视图拆分，Conversation 层独占 interactive。
- **AgentRuntime 提取引入事件/终态第二裁判或 cancel 绕过落盘** —— 触发信号：runtime 直接 emit terminal、SSE terminal 与 DB terminal 不同、双 ERROR、response 先于 events，或外层 CancelledError 令 finalization 未执行；应对：runtime 只返回 outcome/stop reason，ConversationTurnHandler cancel-and-drain 后进入唯一受保护 post-processing。
- **Router 迁移汇成新的 god service** —— 触发信号：send/delete、runtime command、Admin read 和底层 store 方法被机械塞进一个无边界类；应对：Conversation command/admission 与只读 RuntimeStatusReader 按用例分离，底层依赖小协议，Router 只做 transport 映射。
- **为未来 workload 过度设计** —— 触发信号：出现没有生产消费者的 Job 表、registry、retry 状态机、named pool、占位 pool 或通用 lifecycle callback soup；应对：本期只有一个 capacity gate；No-Conversation supervisor test 和 embedded runtime smoke 只证明依赖边界，不驱动新增未来机制。
- **大分支难定位回归** —— 触发信号：多个阶段同时红且无法区分行为变化；应对：单分支但按 A–E 独立 commit 和验收推进，每阶段绿后再进入下一阶段。
- **错误出口在搬层后失去 ops 日志或泄露内部错误** —— 触发信号：5xx 只有用户消息、预期 4xx 打 exception stack、SSE 返回原始异常；应对：每个新 Service failure exit 同时审 user mapping 与 ops log，沿用 request_id/instance_id 规则。

风险判断继续遵循：先确认可达性；同形补丁第二次出现即回看边界；若机制只用于同步两份状态则停下并统一 substrate；修复复杂度接近目标能力时重新收 scope。

## 变更日志

- 2026-08-12 **阶段 A 完成**：用无 sleep 的 barrier tests 稳定复现 send/delete 双顺序与当前 check→lease 空窗；补齐固定 ID post-commit retry、parent 三态、stale owner、Router runtime 返回值及 user/ops 错误双契约；盘点全部 `runner.store` 与非 Router runtime 消费者。阶段内无生产代码或 schema 变化。
- 2026-08-11 **契约精化**：将 `append_message` 从误导性的“只 INSERT”改为“只接受已有 Conversation”，显式保留 parent 校验、active_branch 和根消息 title；把 lease loss 验收改为检测后的 fail-closed、有界 finalization、CAS cleanup 与 TTL 回收，不再承诺网络分区下零重叠/零残留；修正 ConversationTurnHandler 到 D 才提取的出现时点。
- 2026-08-11 **reviewer 修订**：消除原 B/C 施工依赖环，B 收窄为持久化语义、C 合并 LeaseHandle/TaskSupervisor/Admission/Router 迁移；恢复稳定 ID 的 DB retry 幂等契约；将 AgentRuntime 明确为 engine outcome producer、ConversationTurnHandler 明确为唯一 finalization owner；纳入 Chat/Admin 全部 `runner.store` 真实调用并删除 named pools 预留。
- 2026-08-10 **起草**：由 Skill 上传安全扫描的复用需求反查现有执行边界，确认 `ExecutionLauncher` 无独立生命周期价值、`ExecutionRunner` 混合进程监管与 Conversation 语义，并将 send/delete TOCTOU 收口为统一 Admission。锁定本期不加 Chat DB status、不提前实现 Job 系统，以 embedded runtime 作为可复用边界验收。
