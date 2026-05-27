# 执行生命周期状态机

> 一个执行从提交到结束有哪些状态、谁有权推进、推进后哪些副作用必须一起发生 —— 把这件事固定下来，让 review 从「这里有没有 race」变成「这个 transition 合法吗、副作用完整吗」。

## 为什么需要这份文档

历轮改造补掉了很多具体竞态洞，但同一**形状**的 bug 反复出现。根因不在单点，而在「执行生命周期从未被建模成一等状态机」—— 状态散落在 Redis key（lease / interactive）、in-memory semaphore、SSE stream meta、DB `MessageEvent` 终态、`Message.response` 这些局部机制里，彼此弱绑定。把模型写下来，让跨层不变量显式、可机械评审。

后半场（终态）已经是一台正确的状态机 —— `core/post_processing.py` 的 `PostProcessState` ledger + `decide_terminal()`（唯一裁判）+ `choose_response_for_terminal()`（`(terminal_type, cancel_source) → display` 的单一映射），三条 cancel 路径都汇入它。

> 前半场 `QUEUED`/`RUNNING` 建模已落地：`mark_interactive` 移到取得 semaphore 之后、对 lease owner 做 fail-closed CAS；**inject 与 cancel 都 gate 在 RUNNING（interactive）**。**stream key 心跳续期是明确 deferred 的非目标** —— 保留 `STREAM_TTL_GRACE` 固定 TTL 作为 sanctioned best-effort 近似（理由见「三条正交的时间轴」与「维持 defer 的边界」）。

## 终态 taxonomy

```
COMPLETED | CANCELLED_BY_USER | CANCELLED_BY_SYSTEM | TIMED_OUT | FAILED | ORPHANED
```

| 概念终态 | 触发 | 线上/存储表示 |
|---|---|---|
| `COMPLETED` | lead agent 无 tool call，引擎正常退出 | `COMPLETE` 事件 |
| `CANCELLED_BY_USER` | 用户点取消（协作式：`hooks.check_cancelled` → `state["cancelled"]=True` 正常返回） | `CANCELLED` 事件，`cancel_source="cooperative"`，无 `reason` 字段 |
| `CANCELLED_BY_SYSTEM` | lease fencing / shutdown / late-cancel（外部 `task.cancel()`） | `CANCELLED` 事件，`cancel_source="external"`，`reason=external_cancel` / `external_cancel_post_processing` |
| `TIMED_OUT` | 引擎 run 超过 `EXECUTION_TIMEOUT` | `TIMED_OUT` 事件，`success=False`，`timed_out=True` |
| `FAILED` | LLM retry 耗尽 / compaction 失败 / flush 失败等 | `ERROR` 事件，`success=False` |
| `ORPHANED` | 实例崩溃，turn 在途但无 terminal（`response=NULL`） | **仅命名**，不建 reconciler |

> **存储表示不重命名**：`CANCELLED_BY_USER` / `CANCELLED_BY_SYSTEM` 是**概念层**区分，线上是单一 `CANCELLED` 事件 + `cancel_source`/`reason` 编码。线上真实事件类型见 `StreamEventType`（`COMPLETE` / `CANCELLED` / `TIMED_OUT` / `ERROR`）。`ORPHANED` 不落任何事件。

## Transition 表（authority + 必须一起发生的副作用）

`SUBMITTED → QUEUED → RUNNING → 终态` 全程已落地为一等状态机。下表每行给出该 transition 的**唯一 authority** 与**必须一起发生的副作用**。

| transition | 唯一 authority | 必须发生的副作用 |
|---|---|---|
| `SUBMITTED → QUEUED` | `runner.submit` | acquire lease（同步）；create stream meta（承载 `execution_queued` 事件）；注册 task。**不** mark_interactive |
| `QUEUED → RUNNING` | `_wrapped`（`async with semaphore` 之后） | `mark_interactive` 对 lease owner 做 **compare-and-set**：仍持 lease 才标记 + 启动 engine。**fail-closed**：lease 已过期/被接管、或 CAS 抛异常（归属不可知）一律 **abort**（不启动引擎 → 不覆盖新 owner 的 interactive key、不跑成静默第二写者；本轮成 ORPHANED，响亮可见）。stream/meta key 仍按固定 TTL（`EXECUTION_TIMEOUT + STREAM_TTL_GRACE`，best-effort，见「三条正交的时间轴」） |
| `QUEUED → ORPHANED`（abort / fence） | `_wrapped` / heartbeat / shutdown | 排队轮被 CAS abort（丢 lease）、心跳 fencing 或 shutdown 取消 → 引擎从未起跑 → 无 controller 后处理 → 无终态事件（`response=NULL`）= ORPHANED。**用户 cancel 不作用于 QUEUED**（gate 在 interactive == RUNNING，与 inject 对称；排队轮返回 409、不置任何 flag），理由见下「为什么 cancel 只作用于 RUNNING」 |
| `RUNNING → COMPLETE` | controller `decide_terminal` | append `COMPLETE`；events 落库；`Message.response = state.response`；SSE 转发终态 |
| `RUNNING → TIMED_OUT` | controller `decide_terminal` | append `TIMED_OUT`；events 落库；`Message.response = TIMED_OUT_RESPONSE`；SSE 转发终态。flush_all 照常跑（best-effort 保留部分 artifact） |
| `RUNNING → CANCELLED` | controller `decide_terminal` / `ensure_terminal` / engine_task | append `CANCELLED`(+reason)；events 落库；`Message.response = CANCELLED_RESPONSE_BY_{USER,SYSTEM}` |
| `RUNNING → ERROR` | engine（自 append ERROR）/ controller（flush_error） | events 落库；`Message.response = state.response or fallback` |
| `* → CLOSED` | `_wrapped` finally | release lease；clear interactive；close stream |

## 为什么 cancel 只作用于 RUNNING

`cancel` 与 `inject` 都 gate 在 `interactive`（== RUNNING）；一个还在 `QUEUED` 的轮返回 409、**不置任何 cancel flag**。这是刻意的边界，不是缺失：

- **`QUEUED` 是唯一纯 worker 本地的状态** —— 一个 task park 在某个 worker 进程内的 `asyncio.Semaphore` 上。Redis 看不到「谁在排队、在哪个 worker、排第几」。
- **cancel 是 Redis 中介的信号**。若允许它作用于 `QUEUED`，就得把 cancel flag 跨越「Redis 观察不到的 in-memory 等待」续命（排队等待在 `EXECUTION_TIMEOUT` 之外、可无界），并和 worker 本地的 dequeue 时序对齐 —— 这正是 r4 review round-1/2 反复冒出同形状 bug 的根因（flag 过期、CAS 覆盖、续期与心跳耦合）。**跨层中介一个本地状态，cancel 语义必然撕裂。**
- **代价极小**：排队轮消耗零算力、只会排在*其他会话*后面（同一会话 lease 单写、排不起队）、且很快起槽起跑；起跑（RUNNING）后 cancel 即刻可用 —— 此时 flag 在持有该轮的 worker 上、几秒内即被 `check_cancelled` 读到，跨 worker 正确、无需续期。

所以一个排队轮要么 advance 到 RUNNING（之后可 cancel），要么被 abort/fence → ORPHANED；用户在排队期间的「取消」诉求由「起跑后立即可取消」承接（best-effort 契约）。

## `TIMED_OUT` 的产出

超时裁判**在引擎内**，不停在传输层：

1. **超时在 `run_engine` 内、只裹 `execute_loop`**（`controller.py`）：`async with asyncio.timeout(config.EXECUTION_TIMEOUT)`。超时后 `except TimeoutError` 像协作式 cancel 一样「带 flag 正常返回」—— 置 `state["timed_out"]=True` + `completed=True`，`finalize_metrics()`，`final_state = initial_state`，走完整 post-processing。
2. **`decide_terminal` 产出唯一 TIMED_OUT 终态**（`post_processing.py`）：`timed_out` 分支在 `flush_error` 之后、`is_cancelled` 之前 —— 保持「持久化失败即便在超时轮也以 ERROR 暴露」的优先级（`flush_error > {timed_out, cancelled} > error > complete`）。`timed_out` 与 `cancelled` 互斥（超时路径只置前者）。
3. **`run_and_push` 是纯转发器**（`controller_factory.py`）：不再裹 `asyncio.timeout`。SSE 终态即 `pp.terminal_event`，与 DB 终态同源。

**为什么干净（Python 3.11+）**：`asyncio.timeout` 只把**自己** deadline 触发的取消转成 `TimeoutError`；外部 `task.cancel()`（lease fencing）原样以 `CancelledError` 再抛 → 两个 `except` 分支不混淆（超时在内层 engine_task，外部 cancel 来自外层 `_wrapped`）。

**GIL 警告**：`asyncio.timeout` 底层是 `task.cancel()`，无法打断钉住 GIL 的同步 CPU 工具；工具作者仍自己兜 wall-clock（见 CLAUDE.md「Tool authors own CPU-cost discipline」）。

## 三条正交的时间轴（liveness / deadline / cleanup）

把它们搅在一起（用 `EXECUTION_TIMEOUT` 同时当引擎 deadline **和** stream key TTL）会反复制造「排队 / post-processing 超过某个静态 TTL → 终态丢失」的洞。三者其实正交：

| 轴 | 问的问题 | 机制 | 跨度 | owner |
|---|---|---|---|---|
| **① Liveness** | 还有活着的 producer 在做这个任务吗？ | 心跳续期 | queue→run→post 全程 | lease（`_renew_loop`） |
| **② Deadline（TIMED_OUT）** | *引擎 run* 跑太久了吗？ | `asyncio.timeout(EXECUTION_TIMEOUT)` 裹 `execute_loop` | **只 run** | engine |
| **③ Cleanup 窗口** | 结束后残余事件给重连 consumer 读多久？ | `close_stream` 设 `STREAM_CLEANUP_TTL` | post 之后 | `close_stream` |

关键：**lease 管「任务做完没」（① 横跨 queue→run→post），timeout 管「run 跑多久」（② 只 run）** —— 两条不同的轴。lease 不因 timeout 而 fence；TIMED_OUT 时引擎正常返回、lease 持有到 post 结束才释放。② 的 `asyncio.timeout` 在 `_wrapped` 取得 semaphore **之后**才起算，所以排队时间天然不计入 `EXECUTION_TIMEOUT`。

**stream key 概念上属于 ①，不是 ②**：它的寿命语义上只跟「producer 是否还活着」走，与 `EXECUTION_TIMEOUT` 解耦。但**实现上刻意保留固定 TTL 近似**（见下），不上心跳续期机器。

> **现状（accepted best-effort，非过渡态）**：stream/meta key TTL 是创建时设定的固定值 `EXECUTION_TIMEOUT + STREAM_TTL_GRACE`（`STREAM_TTL_GRACE` 兜引擎 deadline 之后的 post-processing）。这是 liveness 轴的**近似**：正常场景有效；唯一残余缺口是「单实例饱和（> `MAX_CONCURRENT`）时，排队等待 + 本轮 run + post 之和超过该固定 TTL」，后果**仅是丢实时 SSE 终态** —— DB 终态始终正确，刷新 / 重连即恢复。
>
> **为什么不上心跳续期（deferred 非目标）**：stream key 是 glance-only 的传输键（用户在它上面 glance，不 act），失败模式自愈、可恢复。把 lease↔stream 统一成心跳续期要给 `StreamTransport`（Protocol + InMemory + Redis）加 `refresh_ttl`、接进 `_renew_loop`、删 `STREAM_TTL_GRACE` —— 为很少触发、glance-only、可自愈的边缘搭强一致机器。按 step-back-on-design-creep 接受固定 TTL 近似为长期契约；若该缺口在实测中真咬人，再单独处理。

## 不变量

1. **events-first**：`Message.response` 只在 `_persist_events` 返回 True 后才写（无「终态已显示但 events 缺失」状态）。
2. **slot-claim before await**：`pp.response_update_attempted` 在 `await update_response_async` **之前** set，防 cancel-mid-await 竞态。
3. **单一 dispatcher**：任何路径想写 `Message.response`，都过 `choose_response_for_terminal(pp)`；`ensure_terminal` adopt-or-synthesize 保证已有 semantic terminal 不被 late-cancel 改写。
4. **超时是产品级终态，后处理卡死是基础设施故障 —— 分层兜底（best-effort）**：

   超时**只裹引擎循环**（无界工作所在），**不裹 post-processing**。后处理是有界 DB 写 + 函数级重试（`with_retry`，3 次瞬断重试）+ late-cancel 兜底；它**没有 app 级 wall-clock 上界**。per-query 上界是 **DB 层职责**：

   | driver | per-query 上界 |
   |---|---|
   | **PostgreSQL**（默认提供的部署形态） | 代码注入 `command_timeout` 默认（`config.DB_COMMAND_TIMEOUT`，setdefault），开箱即有界。这是协议安全的 per-语句超时（asyncpg 内置 server 端取消 + 连接回收）。禁用：`ARTIFACTFLOW_DB_COMMAND_TIMEOUT=0`（跳过注入）；**不能**用 DSN `?command_timeout=0` —— asyncpg 拒绝 ≤0、会启动失败；DSN 若显式给值须 >0 且覆盖默认。**不要在应用层裸 `asyncio.wait_for` 包 DB 写** —— 会污染池化连接 + 留 commit 歧义。 |
   | **MySQL / TDSQL**（兼容目标） | driver 无等价钩子（aiomysql 不吃 `read/write_timeout`）→ per-查询上界由**部署方基础设施**负责：`innodb_lock_wait_timeout`（写锁等待，默认 ~50s）+ 中间件/server 超时。原因同 timezone：我们够不到托管实例的 server GUC。 |
   | **SQLite** | 无此缺口（进程内，`PRAGMA busy_timeout` 兜锁）。 |

   能同时击穿「DB per-query 超时 + app retry + lease fencing」三者的病态网络黑洞，需 socket 层 TCP keepalive —— 接受为 out-of-scope best-effort。理由：不为很少触发的基础设施卡死在应用层搭第二个 deadline authority（会重新引入「两个终态 authority」混乱）。

5. **lease 存活属 liveness 轴，靠心跳续期（`_renew_loop`）、横跨 queue→run→post，与 `EXECUTION_TIMEOUT` 解耦**（见「三条正交的时间轴」）。`EXECUTION_TIMEOUT` 只决定 TIMED_OUT，不决定 lease TTL。stream key 概念上同属 liveness 轴，但**实现上保留固定 TTL 近似**（`EXECUTION_TIMEOUT + STREAM_TTL_GRACE`）作为 accepted best-effort —— 心跳续期是 deferred 非目标（理由见上「为什么不上心跳续期」）。cancel flag **不**跨越排队等待续命：cancel 只作用于 RUNNING（见「为什么 cancel 只作用于 RUNNING」），flag 在几秒内被读取，`EX=EXECUTION_TIMEOUT` 恒够、无需 heartbeat 耦合。

6. **单写者：`QUEUED → RUNNING` 只在确认仍持有 lease 时发生（compare-and-set，fail-closed）**。`mark_interactive` 原子校验 lease owner == 本 msg_id 才标记 interactive + 启动引擎；排队期间 lease 过期/被接管则 abort，绝不在他人持有的会话上跑成第二写者、也不覆盖新 owner 的 interactive key。这把 lease 的「单对话互斥」从「靠 heartbeat fence 的延迟撤销」收紧为「起跑前的前置闸」。**CAS 抛异常（归属不可知）= fail-closed abort** —— 宁可丢一个 turn（ORPHANED，响亮可见、可刷新恢复）也不 fail-open 跑成静默第二写者（codebase 偏好 loud failure over silent corruption）；redis-py client 已对瞬断重试，仍抛即视为持续故障。

## 测试矩阵

| 场景 | 单元（`tests/core/test_post_processing.py`） | 集成（`tests/core/test_controller_cancel_persist.py`） |
|---|---|---|
| success / COMPLETE | `decide_terminal` complete + `choose_response` complete | — |
| cooperative cancel | cooperative 分支 + adopt cooperative | `test_cooperative_cancel_writes_response_by_user` |
| external cancel（执行中） | adopt external（带 reason） | `test_external_cancel_persists_accumulated_events` |
| late-cancel（后处理中） | adopt / synthesize | `test_external_cancel_during_exists_async_persists_events` 等 |
| timeout | `test_timed_out_path` / `test_flush_error_overrides_timed_out` / `test_adopts_existing_timed_out_terminal` / `test_timed_out_always_returns_timeout_placeholder` | `test_engine_timeout_produces_timed_out_terminal` |
| ERROR | `test_error_path_does_not_double_append` | `test_late_cancel_on_engine_error_path_still_persists` |

终态集合跨层一致性（`core.events.TERMINAL_EVENT_TYPES` ↔ 传输/路由层本地副本）由 `tests/core/test_terminal_event_sync.py` 守护。

## 维持 defer 的边界

- **`ORPHANED` reconciliation**：系统支持**故障收敛**，不支持 in-flight turn **恢复**。崩溃后 `response=NULL` 仅命名为 `ORPHANED`，不建 startup reconciler（best-effort 契约）。
- **stream key 心跳续期**：stream key 概念上属 liveness 轴，但实现保留固定 TTL 近似（`EXECUTION_TIMEOUT + STREAM_TTL_GRACE`，accepted best-effort）。把 lease↔stream 统一成心跳续期是 deferred 非目标 —— glance-only 传输键的自愈缺口不值得跨 Protocol/InMemory/Redis 搭强一致机器（理由见「三条正交的时间轴 → 为什么不上心跳续期」）。
- **QUEUED 期间取消**：用户 cancel 不作用于 QUEUED（gate 在 interactive == RUNNING，排队轮 409、不置 flag）。这是刻意边界，不是缺失 —— 排队是 worker 本地的 in-memory semaphore 等待，Redis 中介的 cancel 够不到，强行支持需把 cancel flag 跨「Redis 观察不到的等待」续命，必然撕裂 cancel 语义（详见「为什么 cancel 只作用于 RUNNING」）。排队轮起跑（RUNNING）后即可 cancel；这也顺带排除了同 worker `task.cancel()` 快路径（跨 worker 本就不成立）。
