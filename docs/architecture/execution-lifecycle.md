# 执行生命周期状态机

> 一个执行从提交到结束有哪些状态、谁有权推进、推进后哪些副作用必须一起发生 —— 把这件事固定下来，让 review 从「这里有没有 race」变成「这个 transition 合法吗、副作用完整吗」。

## 为什么需要这份文档

历轮改造补掉了很多具体竞态洞，但同一**形状**的 bug 反复出现。根因不在单点，而在「执行生命周期从未被建模成一等状态机」—— 状态散落在 Redis key（lease / interactive）、in-memory semaphore、SSE stream meta、DB `MessageEvent` 终态、`Message.response` 这些局部机制里，彼此弱绑定。把模型写下来，让跨层不变量显式、可机械评审。

后半场（终态）已经是一台正确的状态机 —— `core/post_processing.py` 的 `PostProcessState` ledger + `decide_terminal()`（唯一裁判）+ `choose_response_for_terminal()`（`(terminal_type, cancel_source) → display` 的单一映射），三条 cancel 路径都汇入它。

> 标注**【计划中】**的部分（前半场 `QUEUED`/`RUNNING` 建模、stream key 心跳续期）尚未落地；本文档描述目标模型，已落地部分按现状描述。

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

后半场（`RUNNING → 终态`）已落地；前半场（`SUBMITTED → QUEUED → RUNNING`）的行为**目标模型**，前半场拆分尚未实现（见 transition 内 **【计划中】** 标注）。

| transition | 唯一 authority | 必须发生的副作用 |
|---|---|---|
| `SUBMITTED → QUEUED` | `runner.submit` | acquire lease；create stream meta；注册 task |
| `QUEUED → RUNNING` | `_wrapped`（semaphore 获取之后） | 启动 engine。**【计划中】** `mark_interactive` 移到此处（当前在 `submit`）。stream key 不锚 `EXECUTION_TIMEOUT`，靠心跳续期（见「三条正交的时间轴」） |
| `QUEUED → CANCELLED` | cancel 路由 | **【计划中】** 直接 `task.cancel()`（task 正挂 semaphore）；release lease |
| `RUNNING → COMPLETE` | controller `decide_terminal` | append `COMPLETE`；events 落库；`Message.response = state.response`；SSE 转发终态 |
| `RUNNING → TIMED_OUT` | controller `decide_terminal` | append `TIMED_OUT`；events 落库；`Message.response = TIMED_OUT_RESPONSE`；SSE 转发终态。flush_all 照常跑（best-effort 保留部分 artifact） |
| `RUNNING → CANCELLED` | controller `decide_terminal` / `ensure_terminal` / engine_task | append `CANCELLED`(+reason)；events 落库；`Message.response = CANCELLED_RESPONSE_BY_{USER,SYSTEM}` |
| `RUNNING → ERROR` | engine（自 append ERROR）/ controller（flush_error） | events 落库；`Message.response = state.response or fallback` |
| `* → CLOSED` | `_wrapped` finally | release lease；clear interactive；close stream |

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

**stream key 属于 ①，不是 ②**：它的寿命该跟 lease 一样靠**心跳续期**（只要 producer 活着就续），与 `EXECUTION_TIMEOUT` 彻底解耦 —— 排队 / post-processing 再长，只要任务没 done、心跳还在续，key 就不过期；崩溃后心跳停，key 在 ~`lease_ttl` 内过期（孤儿清理有界）。静态定值盖不住 ① 的无界跨度。

> **现状 vs 目标**：当前 stream/meta key TTL 是创建时设定的固定值 `EXECUTION_TIMEOUT + STREAM_TTL_GRACE`（`STREAM_TTL_GRACE` 兜引擎 deadline 之后的 post-processing）—— 这是 liveness 的**近似**，对正常场景有效；极端排队 / 重度 DB 退化下仍可能丢**实时** SSE 终态（DB 终态始终正确，刷新 / 重连即恢复）。**【计划中】** 改为与 lease 共用心跳续期、删除 `STREAM_TTL_GRACE`，随前半场 `QUEUED`/`RUNNING` 建模一起落地。

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

5. **Redis key 存活属 liveness 轴，靠心跳续期，与 `EXECUTION_TIMEOUT` 解耦**（见「三条正交的时间轴」）。stream key 与 lease 共用心跳、横跨 queue→run→post；`EXECUTION_TIMEOUT` 只决定 TIMED_OUT，不决定任何 Redis key TTL。把 stream key TTL 锚在 `EXECUTION_TIMEOUT` 上是错误抽象（静态定值盖不住 liveness 的无界跨度）。**【计划中】**（当前为固定 TTL 近似，见上「现状 vs 目标」）。

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
- **前半场 `QUEUED`/`RUNNING` 建模**：`mark_interactive` 移到 semaphore 之后、queued cancel 即时生效、stream key 心跳续期等，均为 **【计划中】**；本文档已写下目标 transition 表与不变量。
