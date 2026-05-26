# 高可用 Review 第四轮：发现与修复计划

> 架构层面 review（执行生命周期 / 状态机整体设计）。
> 来源：外部 reviewer 架构反馈 + 自研评估，合并去重后排列。
> 评估基线：沿用 r3（`ha-review-r3-findings.md`）——异地双活、单中心双实例、共享云托管 TDSQL + Redis、Redis 不跨中心同步。
>
> **基线更新（重要）**：r3 基线已写明 Redis 数据分析区是「华为大数据平台 redis Cluster，Redis 5.0+」。生产 Redis 形式较杂（部分 standalone / Sentinel，数据分析区是 Cluster），且我方无法自由选择部署形态 → **Cluster-safety 视为硬要求**，跨 slot 的多 key 命令是确定要修的真实 bug，不是隐患。

---

## 核心诊断：反复出现的 race，根因是执行生命周期未被建模成一等状态机

前几轮 HA 改造（PR1–PR9）补掉了很多具体竞态洞，但同一**形状**的 bug 反复出现。本轮判断：根因不在单点，而在**「一个执行从提交到结束有哪些状态、谁有权推进、推进后哪些副作用必须一起发生」从未被固定下来**，而是散落在 Redis key（lease / interactive）、in-memory semaphore、SSE stream meta、DB `MessageEvent` 终态、`Message.response` 这些局部机制里，彼此弱绑定。

值得肯定的是：**后半场（终态）其实已经是一台正确的状态机**——`core/post_processing.py` 的 `PostProcessState` ledger + `decide_terminal()`（唯一裁判）+ `choose_response_for_terminal()`（`(terminal_type, cancel_source) → display` 的单一映射），三条 cancel 路径都汇入它（`ensure_terminal` adopt-or-synthesize）。这正是 cancel/persist 漂移止血的原因。

本轮发现的新问题，本质都是「这台已有的状态机没有覆盖到的边」：
1. **排队态被折叠进运行态**：`submit()` 拿到 lease 就 `mark_engine_interactive` + `create_stream`，但真正执行要等 semaphore → `QUEUED` 和 `RUNNING` 混在一起。
2. **timeout 不是状态机内的终态**：超时裁判在传输层（`run_and_push`），和 controller 的终态裁判分裂成两个 authority。
3. **跨实体聚合读用了单 slot 原语**：`list_active_executions` 跨会话 `MGET` → Cluster 上 `CROSSSLOT`。

所以本轮的修复策略不是「再补单点 race」，而是**把已有的 dispatcher 模式向上（生命周期前半场）和向外（timeout）推广，并先把模型写下来**，让后续 review 从「这里有没有 race」变成「这个 transition 合法吗、副作用完整吗」。

> **分支策略**（项目惯例）：三个 PR 都是通用功能修复，一律先进 `main`，再合并到内网分支。无内网特定运维项。

---

## 优先级总览

| 优先级 | PR | 状态 | 说明 |
|---|---|---|---|
| P1 | **PR-A** Redis 层 Cluster-safety + hygiene | ✅ 已修（`6a8cc93`+`7a1c3c5`） | `list_active_executions` 跨实体 `MGET` → pipeline 逐 key `GET`；`push_event` 首推 `XADD`+`EXPIRE` 改单 key Lua（review 指出 pipeline 非原子,见 A-2）；过期 fixture 实已在 `89cb419` 修掉,本 PR 补 Cluster-safe 回归断言 |
| P1 | **PR-B** 执行生命周期状态机文档 + `TIMED_OUT` 终态 | ✅ 已修 | 模型落 `docs/architecture/execution-lifecycle.md` + CLAUDE.md 一条；`TIMED_OUT` 成一等终态，超时下沉到 engine_task 的 `asyncio.timeout`→`decide_terminal`，`run_and_push` 退回纯转发器；含 待决策项 2 的 DB `command_timeout` 兜底（PG 默认注入）。全量 998 passed |
| P2 | **PR-C** `QUEUED`/`RUNNING` 前半场建模 | ✅ 已修 | 纯控制面改动：`mark_interactive` 移到取得 semaphore 之后（QUEUED→RUNNING 边）；inject gate 在 interactive(RUNNING)、cancel gate 在 lease；queued cancel 走协作式 flag（**非** `task.cancel()`，跨 worker 正确）。stream key 维持 `STREAM_TTL_GRACE` 固定 TTL（心跳续期改判为 deferred 非目标，见验收项 2） |
| — | F-24 执行崩溃 reconciliation（`ORPHANED`） | **维持 defer** | 沿用 r3：只命名状态，不建 startup reconciler（best-effort 契约） |

**为什么拆 3 个而不是 1～2 个**：
- **PR-A 与生命周期零耦合**、风险极低、可立刻上线。绝不能塞进生命周期 PR——否则这个 trivial 修复要陪跑那套最娇贵的 cancel 机器（3 条 cancel 路径 + events-first + slot-claim-before-await）的评审周期。
- **PR-B 与 PR-C 之间无顺序依赖**（`TIMED_OUT` 只活在后半场已有 dispatcher 里，不依赖前半场状态机），但揉一起等于在并发最敏感区一次性改两处，一处出 bug 另一处也别想 merge。
- **PR-C 紧迫度更低**（只有单实例 > `MAX_CONCURRENT` 排队时才咬人，危害是延迟 / 资源记账，不是数据损坏），且动路由 + runner 层、面更大 → 后置，等 PR-B 的模型文档落地后再长。符合「不为很少触发的边缘先搭机器，先写下模型」的 step-back 原则。

PR 之间逻辑独立可分别回滚。不强制一捆发：PR-A 是 quick win 可单独 ship。

---

## 能力边界（本轮更新）

| 能力 / 状态 | 状态 | 变化 |
|---|---|---|
| 共享控制面 / fencing / failover 恢复（PR1–PR9） | ✅ | 无变化 |
| **Redis 访问 Cluster-safety** | ✅ 已修 | `list_active_executions` 改 pipeline 逐 key GET；约束沉淀进 CLAUDE.md（PR-A,`6a8cc93`） |
| **超时终态唯一 authority** | ✅ 已修 | 超时下沉到 engine_task 的 `asyncio.timeout`→`decide_terminal`,`run_and_push` 退回纯转发器,SSE 与 DB 同源（PR-B） |
| **`TIMED_OUT` 作为一等终态** | ✅ 已修 | `StreamEventType.TIMED_OUT` + `decide_terminal`/`choose_response_for_terminal` 分支,优先级 `flush_error > {timed_out, cancelled} > error > complete`（PR-B） |
| **`QUEUED` 作为显式状态（inject/cancel 正确分流）** | ✅ 已修 | `interactive` 窗口 = RUNNING：inject 在 RUNNING 才放行（QUEUED→409）；cancel gate 在 lease（QUEUED/RUNNING 都可取消，协作式 flag 取得槽位即生效，跨 worker 正确）。同 worker `task.cancel()` 即时取消 = deferred 非目标（PR-C） |
| **in-flight turn 跨实例恢复（`ORPHANED`）** | ❌ 不支持（明确边界） | 系统支持**故障收敛**，不支持 in-flight turn **恢复**；崩溃后 `response=NULL` 仅命名为 `ORPHANED`，不建 reconciler（F-24 维持 defer） |

---

## PR-A：Redis 层 Cluster-safety + hygiene（P1）✅ 已修

> **状态（2026-05-26）**：已落地 `main`,commit `6a8cc93`（A-1/A-3 + 约束沉淀）+ `7a1c3c5`（A-2 review 修复）。29 个 Redis integration 测试全过、全量 1013 passed。尚未 push / 未 merge 进 intranet。

### A-1 `list_active_executions` 跨 slot `MGET`（核心）✅

**问题**：`redis_runtime_store.py:355-373` 先 `scan_iter` 扫所有会话的 lease key，再一把 `mget(keys)`。lease key 形如 `{prefix:conv_id}:lease`，hash tag 含 `conv_id` → 不同会话不同 slot → 多 key `MGET` 在 Cluster 上报 `CROSSSLOT`。该接口被会话列表端点直接调用（`chat.py:297`）。

**为什么加了 prefix 仍有问题**：hash tag 的本意是把**同一实体**的 key 绑到同一 slot（已验证安全：`lease`+`interactive` 同 `conv_id` tag；`interrupt`+`cancel`+`queue` 同 `msg_id` tag，所以 `cleanup_execution:385` 的三 key `DELETE` 同 slot；所有 Lua 用单 `KEYS[1]`）。但把 `conv_id` 放进 tag 的代价就是**不同会话刻意分散到不同 slot**（这是 Cluster 负载均衡所需）。`MGET` 是单 slot 原语，「列出所有活跃执行」本质是跨 slot 的 fan-out 查询——任何 hash tag 方案都无法把它表达成一条多 key 命令。

**修法（已定：单 `GET` 的 pipeline）**：扫到 lease key 后用 pipeline 逐个 `GET`，非原子，redis-py 按节点路由。lease 集合受 `MAX_CONCURRENT` 上界约束，batch 很小，开销可忽略。

**为什么不用 `mget_nonatomic`（关键）**：prod Redis 形式很杂、我方无法控制实例化出来的是哪种 client，所以修法必须**同一份代码在 standalone / Sentinel / Cluster 上都正确**。`mget_nonatomic` 是**只挂在 cluster client 上的方法**，standalone `Redis` 根本没有它（redis-py 5.3.1 实测：`Redis.mget_nonatomic` → `AttributeError`；`RedisCluster.mget_nonatomic` → ✓）。用它会**修好 Cluster、却同时弄坏 standalone**，与「跨部署形态稳」的目标正相反。GET pipeline 是唯一三种形态同码正确、无需按 client 类型分支的写法。

| 写法 | standalone `Redis` | `RedisCluster` |
|---|---|---|
| `mget(keys)` 跨实体 | ✓ | ✗ `CROSSSLOT` |
| `mget_nonatomic(keys)` | ✗ `AttributeError` | ✓ |
| **GET pipeline** | ✓ | ✓ |

> 推论：Cluster-safe 是最严格约束，满足它就自动满足 standalone/Sentinel（单一 keyspace，多 key 天然同 slot）——但**前提是所选构造在 standalone client 上也存在**。这条轴只管 key 路由正确性；Pub/Sub 在 Cluster 上的全节点广播（5.0+ 经典 pub/sub）、failover 时序差异是独立议题，本 PR 不碰、也不因此回归。

不引入「active set」单索引 key：set 成员不随 lease TTL 过期，崩溃 producer 会残留 → 需 reconciliation，本规模不值得。

### A-2 `push_event` 首次 `XADD`→`EXPIRE` 孤儿窗口（顺带）✅

**问题**：`redis_stream_transport.py:114-127` 首次 push 是 `exists` 判断 → `XADD`（`:118`）→ 若 first_push 再 `EXPIRE`（`:127`），两步非原子。producer 在两步之间崩溃 / 断连 → stream key 无 TTL（meta key 在 create 时已有 TTL，会自己过期，但 stream key 会孤儿）。

**修法（最终：单 key Lua）**：初版用 `pipeline(transaction=False)` 把 XADD+EXPIRE 一并发送,**review 指出 pipeline 只是批量发送、非原子**——半包只送到 XADD 而没送到 EXPIRE（连接中途断 / failover）仍会留下无 TTL 孤儿键（永不自愈）。改为单 key Lua `_LUA_XADD_WITH_TTL`：`XADD` 后按 `TTL==-1`（键在但无过期）判据条件 `EXPIRE`,整段原子执行 → 要么 XADD+EXPIRE 都发生、要么键根本没创建。`TTL==-1` 判据既识别首推、又自愈历史遗留无 TTL 键,且保留「后续推送不刷新 TTL」。单 `KEYS[1]=stream_key`,Cluster 安全;顺带去掉单独的 `exists` 预查（少一次 round-trip）。

**review Finding 2（TTL 对齐）→ 推 PR-C**：首推设的是完整 `execution_timeout`（从首推时刻起算）,`create_stream` 早于首推时 stream 会比 meta_key 晚过期。原注释「不超出 meta_key」是过度声明,已纠正为 best-effort 契约（stream key 必带 TTL 且 ≤ `execution_timeout`）。精确对齐留给 PR-C——届时 `create_stream` / TTL bump 移到 RUNNING 后 `t1−t0` 趋零,该错配自然消失;且此残留属 glance-only 传输键的 bounded 自愈内存（`consume` 进门先查 meta,meta 没了残键读不到）,不值得现在上 `PTTL` 对齐机器。

### A-3 测试缺口（顺带）✅

r3 记录的过期 fixture（`stream_timeout` → `execution_timeout`）**实已在 `89cb419`（2026-04-08）修掉**,现仓库 `grep stream_timeout` 零命中,文件路径也已迁到 `tests/integration/`——这条 r3 发现已过时,无需处理。本 PR 改为补 Cluster-safe 回归断言：A-1 加 tripwire（monkeypatch `mget` 抛异常,证明不再走 MGET,单节点也能钉住）;A-2 强化孤儿测试（断言首推 TTL 有界、后续推送不刷新）。

**范围**：`redis_runtime_store.py`、`redis_stream_transport.py`、`tests/test_redis_runtime_store.py`。约束写入项目惯例（见下方「约束沉淀」）。

---

## PR-B：执行生命周期状态机文档 + `TIMED_OUT` 终态（P1）✅ 已修

> **状态**：已落地 `main`（待提交）。模型文档 `docs/architecture/execution-lifecycle.md` + CLAUDE.md「Architecture Decisions」一条；`TIMED_OUT` 一等终态经既有 dispatcher 产出；DB `command_timeout` 兜底（待决策项 2）随附。全量 **998 passed, 29 skipped**；前端 typecheck + **188 passed**。
>
> **待决策项落定**：① 用一等 `TIMED_OUT` 终态（利于可观测）。② 超时只裹 `execute_loop`,post-processing 不设 app 级 deadline,per-query 上界归 DB 层（PG `command_timeout` 默认注入 / MySQL server GUC / SQLite N/A,见 execution-lifecycle.md「不变量 4」）。

### B-1 先写下模型（doc，零运行时风险）

产出一份生命周期状态机文档（建议落 `docs/architecture/` 或随本 PR 进 CLAUDE.md「Architecture Decisions」一条）：enum + transition 表 + 每个 transition 的「唯一 authority + 必须一起发生的副作用」+ 测试矩阵骨架。这是「共同语言」产物，让 PR-B/PR-C 的代码可被机械评审。

终态 taxonomy（在既有 `COMPLETE / CANCELLED / ERROR` 上补齐）：

```
COMPLETED | CANCELLED_BY_USER | CANCELLED_BY_SYSTEM | TIMED_OUT | FAILED | ORPHANED
```

（`CANCELLED_BY_USER` = cooperative；`CANCELLED_BY_SYSTEM` = external_cancel / lease fencing / shutdown / late-cancel；`ORPHANED` 仅命名，不建 reconciler。）

### B-2 `TIMED_OUT` 经既有 dispatcher 产出唯一终态

**问题**：超时裁判在传输层 `run_and_push`（`controller_factory.py:125` 的 `asyncio.timeout`），超时后直接推 SSE `error`（`:147-153`）。但被取消的 controller 会按 external cancel 路径持久化 `CANCELLED`（`controller.py:206`），late-cancel 时甚至 adopt 已存在的 `COMPLETE`（`controller.py:527` → `_recover_from_late_cancel`）。结果：**前端实时看到 timeout error，DB 历史却是 cancelled / complete**——两个终态 authority。

**修法**（最干净的形态：模仿协作式 cancel「带 flag 正常返回」）：

```python
# controller.py run_engine —— engine_task 是独立 task
try:
    async with asyncio.timeout(config.EXECUTION_TIMEOUT):
        final_state = await execute_loop(...)
except TimeoutError:                       # 普通 Exception，非 BaseException
    initial_state["timed_out"] = True
    initial_state["completed"] = True
    finalize_metrics(initial_state["execution_metrics"])
    final_state = initial_state            # 正常返回 → 走完整 post-processing
except asyncio.CancelledError:
    ...  # 既有 external-cancel 路径不动
```

```python
# post_processing.decide_terminal()
if s.get("timed_out"):
    pp.terminal_type = StreamEventType.TIMED_OUT.value
    pp.terminal_event = ExecutionEvent(TIMED_OUT, ..., data={..., "execution_metrics": metrics})
    return

# post_processing.choose_response_for_terminal()
if pp.terminal_type == StreamEventType.TIMED_OUT.value:
    return config.TIMED_OUT_RESPONSE
```

然后 `run_and_push` **删掉** `asyncio.timeout` + `except TimeoutError`，退回纯转发器。SSE 终态就是 `pp.terminal_event`（`controller.py:508` 已有 yield），与 DB 终态同源、同一个 transition。

**为什么干净**（三点）：
- `TimeoutError` 与 external cancel 天然可区分（Python 3.11+）：`asyncio.timeout` 只把**自己** deadline 触发的取消转成 `TimeoutError`；外部 `task.cancel()`（lease fencing）原样以 `CancelledError` 再抛 → 两个 `except` 分支不会混淆。
- 正常返回（非抛异常）会走完整 post-processing → `flush_all` 照常跑，**timeout 时部分 artifact 被保留**（best-effort）。若决定不 flush，改成抛异常按 external-cancel 处理；本计划倾向 flush。
- **GIL 警告同今天**：`asyncio.timeout` 底层是 `task.cancel()`，仍无法打断钉住 GIL 的同步 CPU 工具——但这是无回退（旧 `run_and_push` timeout 限制一模一样）。工具作者仍自己兜 wall-clock（见 CLAUDE.md「Tool authors own CPU-cost discipline」）。

**决策点**：超时是否只裹 `execute_loop`（引擎循环 = 无界工作所在），还是也裹 post-processing？倾向只裹引擎循环，post-processing 的 IO 已有函数级重试 + late-cancel 机器兜底；若要兜 post-processing，给它自己的内部 deadline 并仍走 `decide_terminal`（产 ERROR / TIMED_OUT 终态），**不重新引入第二 authority**。

**新增常量 / 枚举**：`config.TIMED_OUT_RESPONSE`（占位串，对齐 `CANCELLED_RESPONSE_BY_*`，`config.py:46-47`）；`StreamEventType.TIMED_OUT`（`core/events.py`）。

**范围**：`core/post_processing.py`、`core/controller.py`、`api/services/controller_factory.py`、`config.py`、`core/events.py`、前端（识别 `TIMED_OUT` 终态事件，复用 cancelled 的渲染外观即可）、测试（success / cooperative / external / late-cancel / **timeout** 全终态矩阵）。

---

## PR-C：`QUEUED`/`RUNNING` 前半场建模（P2，后续）

### C-0 先把跨层生命周期模型写下来（PR-C 开篇，先于前半场代码）

> **缘由**：PR-B review round-1 的 P1#1（stream TTL 绑死引擎 deadline）与 P1#2（`timed_out` 漏在 transport/router 终态集合）是**同一形状的 bug** —— 「一条跨层不变量是隐式的，于是漂移」。按 step-back 规则（同形状到第二轮 → 退到架构），PR-C 不直接写前半场代码，**先把模型落下来，前半场代码对着它实现**。模型与代码同 PR 落地，模型才不失真。

把 `docs/architecture/execution-lifecycle.md`（今天是引擎/终态视角）扩成完整跨层模型：

1. **一条主轴，投影到多个载体**（不是 N 个独立状态机）：
   `SUBMITTED → QUEUED → RUNNING → POST_PROCESSING → CLOSED`，结果 ∈ `{COMPLETE, CANCELLED, TIMED_OUT, ERROR}`（在 POST_PROCESSING 裁定），崩溃逃逸 = `ORPHANED`。
2. **四个投影**（修正最初的「三层」草图）：
   - **DB = 持久权威**（不是状态机）：开始读 path events，结束 dump events/response/metadata/artifacts；它对一个执行的「状态」只是 `{有无终态事件 + response}`（response NULL = `ORPHANED`）。其他层向它收敛。
   - **lease**（`conv_id` 维度，管互斥/所有权）与 **stream**（`msg_id` 维度，管传输）**拆成两台机器** —— 把它们混作「Redis 层」正是 P1#1 被藏住的原因。
   - **engine task = 驱动器**（不是同级层）：推进其他投影（acquire lease / create stream / write DB / decide terminal）。
   - **agent 运行细节是 RUNNING 的子状态**，在生命周期抽象之下（见 `engine.md`），不画进主轴。
3. **三条正交的时间轴**（review round-2 厘清，dissolve 掉 grace 的纠结）：
   - **① Liveness**（lease + stream key 存在）：「还有活着的 producer 在做吗」→ 心跳续期，横跨 queue→run→post。
   - **② Deadline（TIMED_OUT）**：「*引擎 run* 跑太久了吗」→ `asyncio.timeout(EXECUTION_TIMEOUT)` 只裹 `execute_loop`。
   - **③ Cleanup 窗口**：结束后残余事件读取期 → `STREAM_CLEANUP_TTL`。
   **stream key 属于 ①，不是 ②** —— 寿命该跟 lease 一样靠心跳续期，与 `EXECUTION_TIMEOUT` 彻底解耦。把它锚在 timeout 上（PR-A best-effort → P1#1 grace → review round-2 的 P2 排队/post-processing 超 grace）是反复踩同一坑：静态定值盖不住 ① 的无界跨度。这正是 lease↔stream 统一（共用一个心跳）。
4. **跨层不变量表**（核心产物，每条注明「结构保证」还是「测试守护」）：
   | 不变量 | 跨层绑定 | 隐式则触发 |
   |---|---|---|
   | stream key 存活 = liveness（心跳续期，横跨 queue→run→post），与 `EXECUTION_TIMEOUT` 解耦 | stream ↔ lease | **P1#1 + round-2 P2**（PR-B `STREAM_TTL_GRACE` 是 stopgap；PR-C 挂心跳续期并删 grace） |
   | 每个执行终态也是 stream 停止事件 | DB-终态 ↔ stream/router | **P1#2**（已修：`TERMINAL_EVENT_TYPES` + `test_terminal_event_sync.py` 守护） |
   | `Message.response` 写入 ⟺ 终态事件已落库（events-first） | engine ↔ DB | cancel 路径漂移（已修） |
   | lease 贯穿 queue→run→post，仅 @CLOSED 释放；不因 timeout 而 fence | lease ↔ engine | fencing 正确性 |
   | 当前（非 historical）段恰有一个终态 | engine ↔ DB | `ensure_terminal` 误 adopt historical |

   目标：把「这里有没有 race」变成「这个 transition 碰了哪条不变量、是否被保证」。

**问题**：`submit()` 一拿到 lease 就 `mark_engine_interactive`（`execution_runner.py:114`）+ `create_stream`（`:115`），但真正执行要等 `async with self._semaphore`（`:162`）。后果：
- 还没跑的任务也能被 `/inject`（`chat.py:231`）/ `/cancel`（`chat.py:265`）当运行态处理。
- `/cancel` 只写 cancel flag（`request_cancel`），而 flag 只在 `execute_loop` 内被 `hooks.check_cancelled` 读到——排队中的本地 task 挂在 semaphore 上，flag 没人读，**无法即时取消**。
- stream TTL 从排队时开始算（`create_stream` 在 submit 内），且 `EXECUTION_TIMEOUT` 同时是 stream lifetime（`config.py:28`）→ 排队等待超 TTL 时后续可能无流可推。

> 注：危害是延迟 / 资源记账，不是数据损坏——slot 腾出后 `check_cancelled` 会早退、DB 仍得到干净 `CANCELLED`。只在单实例 > `MAX_CONCURRENT` 排队时触发。故定 P2。

**transition 表（PR-B 文档里写下、PR-C 落地）**：

| transition | 唯一 authority | 必须发生的副作用 |
|---|---|---|
| `SUBMITTED → QUEUED` | `runner.submit` | acquire lease；create stream meta（`pending`，短 TTL，靠心跳续期）；注册 task |
| `QUEUED → RUNNING` | `_wrapped`（`async with semaphore` 之后）| **此处** `mark_interactive`；启动 engine。stream key **不锚 `EXECUTION_TIMEOUT`**，与 lease 共用心跳续期（review round-2：liveness 轴，非 deadline 轴） |
| `QUEUED → CANCELLED` | cancel 路由 | 直接 `task.cancel()`（task 正挂 semaphore）；release lease；可选写 `CANCELLED` 终态 `reason="cancelled_while_queued"` |
| `RUNNING → {COMPLETE, CANCELLED, TIMED_OUT, ERROR}` | controller `decide_terminal` | PR-B 的 dispatcher |
| `* → CLOSED` | `_wrapped` finally | release lease；clear interactive；close stream |

**落地动作（as-built）**：
1. `mark_engine_interactive` 从 `submit()` 移到 `_wrapped` 内 `async with semaphore` 之后（best-effort：标记失败只记 warning、不丢 turn，因为 interactive 只 gate inject/cancel 这类 UX 便利，不是正确性不变量）；clear 仍走既有 `_on_engine_exit` / `cleanup`。`create_stream` **留在** `submit`（必须 —— `execution_queued` 在取 semaphore 前就推到它上面）。
2. `inject` gate 保持在 interactive（移动后 == RUNNING）→ QUEUED 自动 409；`cancel` gate 改到 **lease**（横跨 QUEUED→RUNNING→post）→ 排队中也能取消。
3. cancel 一个 QUEUED turn：**协作式**，只置 Redis cancel flag；待取得槽位、引擎首个 `check_cancelled` 即看到 → 既有协作式路径产出干净 CANCELLED 终态 + `CANCELLED_RESPONSE_BY_USER`。**不**走 `task.cancel()`：排队 task 只在某一 worker，cancel 请求经 LB 可能落到别的 worker，`task.cancel()` 跨 worker 不成立；协作式 flag 跨 worker 正确，且省掉「为排队 turn 在 runner 合成终态」（否则前端因 `response` 为空整条 turn 消失）。
4. **stream key TTL 维持 `STREAM_TTL_GRACE` 固定值**（accepted best-effort，非过渡态）：`create_stream` 必须在 `submit`（QUEUED）建好，其寿命因而横跨 queue→run→post；唯一残余缺口（单实例饱和排队 > grace）只丢实时 SSE 终态，DB 始终正确、刷新即恢复。心跳续期（lease↔stream 统一）改判为 **deferred 非目标**（见验收项 2 与 `execution-lifecycle.md`）。

**范围（as-built，纯控制面）**：`api/services/execution_runner.py`（mark_interactive 移位）、`api/routers/chat.py`（cancel gate 改 lease + inject 注释）、`docs/architecture/execution-lifecycle.md`（前半场标 landed、stream 心跳续期标 deferred）、测试（runner QUEUED/RUNNING 分离 + 路由 QUEUED 分流）。**传输层、`config.py`、`runtime_store` 接口、前端均未改动** —— 协作式 cancel 复用既有 flag 路径，前端复用既有 cancelled 渲染。

`execute_loop` 的扁平 `while not completed` 循环**完全不动**——本轮全部改动都在它外围的 controller / runner / 传输层。

### PR-C 验收项（收尾必做）

review round-2 结论：上述 finding **不挡 `d0b9a07`**（PR-B 的 grace 是有效 stopgap），但作为 PR-C 的**显式验收项**：

1. **`EXECUTION_TIMEOUT` 只管 engine run deadline** —— 不管 queue / post-processing / stream lifetime。代码与文档都按此口径。✅ 保持（PR-B 已落地，PR-C 未触碰）。
2. ~~**stream/meta key 随 lease heartbeat 续期；删除 `STREAM_TTL_GRACE`**~~ → **决策改判：保留 `STREAM_TTL_GRACE` 固定 TTL 作为 accepted best-effort，心跳续期改为 deferred 非目标**。理由：stream key 是 glance-only 传输键，唯一缺口（单实例饱和排队 > grace）只丢实时 SSE 终态、DB 始终正确刷新即恢复；跨 Protocol/InMemory/Redis 加 `refresh_ttl` 为可自愈边缘搭强一致机器，不划算（step-back-on-design-creep）。`STREAM_TTL_GRACE`、传输层、`config.py` 全部**未改动**。
3. **收尾文档同步** —— `execution-lifecycle.md` 已把前半场建模标 landed、stream 心跳续期标 deferred 非目标。sibling docs（`concurrency.md` / `streaming.md` 等）按此口径核对：stream TTL 仍是固定 `EXECUTION_TIMEOUT + STREAM_TTL_GRACE`，不再承诺改心跳续期。

> **关于 current-state 文档残留（刻意 deferred，非漏）**：`d0b9a07` 只更新了 `execution-lifecycle.md`（标注「目标模型 / 计划中」）+ 本 fix-plan，**未**改其他 canonical docs。若 `d0b9a07` 单独 merge，PR-C 落地前其余 current-state 文档（`streaming.md` / `concurrency.md` 等）仍是旧 TTL 模型 —— 这是**有意推迟到 PR-C 收尾统一刷新**，不是遗漏。reviewer 看到旧描述时以本条为准。

---

## 约束沉淀（随 PR-A 写入项目惯例）

> **Redis Cluster-safety**：standalone/Sentinel 是 baseline，但所有 Redis 访问必须 Cluster-safe——任何多 key 命令不得跨实体（不同 `conv_id` / `msg_id`）。跨实体聚合要么 fan-out（pipeline / `*_nonatomic`），要么读单个索引 key。新增多 key 操作在 review 时必须交代其 slot。

---

## 待决策项

1. ~~`TIMED_OUT` 用一等终态还是 `cancel_source="timeout"` 变体~~ **已定：一等终态**（`StreamEventType.TIMED_OUT`，利于可观测；超时非取消，语义独立）。
2. ~~PR-B 超时只裹 `execute_loop` 还是也兜 post-processing~~ **已定：只裹 `execute_loop`**。post-processing 不设 app 级 deadline（避免第二 authority）；per-query wall-clock 归 DB 层：PG `command_timeout`（`config.DB_COMMAND_TIMEOUT` setdefault 注入，DSN 可覆盖/禁用）、MySQL/TDSQL server `innodb_lock_wait_timeout`、SQLite N/A。详见 `docs/architecture/execution-lifecycle.md`「不变量 4」。
3. ~~PR-C 取消 QUEUED turn 是否写一条 `CANCELLED`/`reason="cancelled_while_queued"` 审计事件~~ **已定：无需额外事件 / 不合成终态**。采协作式取消（非 `task.cancel()`）：turn 取得槽位后引擎首个 `check_cancelled` 即看到 flag → 走既有协作式 CANCELLED 路径产出正常终态 + `CANCELLED_RESPONSE_BY_USER`，终态合成问题自然消解。
5. ~~PR-C queued cancel 用 `task.cancel()` 还是协作式 flag~~ **已定：协作式 flag**（`task.cancel()` 跨 worker 不成立 —— 排队 task 只在某一 worker，cancel 经 LB 落别处则 no-op；协作式 flag 跨 worker 正确，见 PR-C 落地动作 3）。
6. ~~PR-C stream key 用心跳续期还是保留 `STREAM_TTL_GRACE`~~ **已定：保留 `STREAM_TTL_GRACE`**（glance-only 传输键、缺口自愈，心跳续期 deferred 非目标，见验收项 2）。
4. ~~PR-A 选 `mget_nonatomic` 还是 GET pipeline~~ **已定：GET pipeline**（`mget_nonatomic` 是 cluster-only API，会弄坏 standalone；prod 形式杂需同码可移植，见 A-1）。
