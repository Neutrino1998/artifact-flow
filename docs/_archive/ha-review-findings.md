# 高可用 Review 发现与修复建议

> 持久化改造（persistence-refactor-plan.md）三轮全部完成后的 HA 专项 review。
> 来源：自研 review + 外部 reviewer 反馈，合并去重后统一排列。
> 评估基线：双服务器 active-active + 云托管 RDBMS + 云托管 Redis。

---

## 当前能力边界

| 能力 | 状态 | 说明 |
|------|------|------|
| 共享控制面（lease/interrupt/stream 跨 Worker） | ✅ | Lua CAS、check-subscribe-check-wait、心跳续租 |
| Fencing / split-brain 防护 | ❌ | lease 丢失后无阻断机制 |
| 故障检测（readiness） | ❌ | `/health` 不检查 DB/Redis |
| Redis failover 恢复 | ⚠️ 部分 | `is_cancelled`/`drain_messages` 有 graceful degrade，Pub/Sub 和 XREAD 无恢复 |
| 事件持久化保证 | ⚠️ 降级 | fallback 到本地文件，容器环境不可靠 |

---

## P0 — 必须修复

### F-01 Lease fencing 缺失（split-brain 风险）+ 执行生命周期职责错位

**来源**：Reviewer P1 + 职责审计

**问题**：两个层面。

**层面 1 — 无 fencing**：入口只在 `chat.py:208` 拿一次 lease，续租循环 `execution_runner.py:99` 失败只记日志继续跑。`redis_runtime_store.py:336` 的 `renew_lease` 不返回 owner 校验结果。一旦 lease 因 Redis failover / 网络抖动 / GC pause 过期，另一个 Worker 可以 `SET NX` 成功接管，原 Worker 仍继续写库。`conversation_repo.py:324` 是覆盖写，`artifact_repo.py:245` 明确无乐观锁 → last-writer-wins / split-brain。

**层面 2 — 职责错位**：lease 获取在 `chat.py`（路由层），续租在 `ExecutionRunner`（服务层），但续租失败的反应谁都没做。根因是执行生命周期管理被拆散在路由和服务两层：

| 步骤 | 当前在哪里 | 应该在哪里 |
|------|-----------|-----------|
| `try_acquire_lease` | `chat.py:209` | `ExecutionRunner.submit()` 预提交阶段 |
| `mark_engine_interactive` | `chat.py:220` | `ExecutionRunner.submit()` 预提交阶段 |
| `create_stream` | `chat.py:226` | `ExecutionRunner.submit()` 预提交阶段 |
| 预提交失败回滚（release_lease + clear_interactive） | `chat.py:257-260` | `ExecutionRunner.submit()` 预提交 try/except |
| 心跳续租 | `ExecutionRunner._renew_loop` | ✓ 位置正确 |
| 续租失败 → fencing | ❌ 无人负责 | `ExecutionRunner._renew_loop` |
| cleanup_execution | `ExecutionRunner._wrapped` finally | ✓ 位置正确 |

路由层（chat.py）应该只做参数校验 + 鉴权 + 调用 `runner.submit()` + 返回 HTTP 响应。执行生命周期的全部管理（拿锁 → 创建 stream → 心跳 → fencing → 清理）都应该收敛到 `ExecutionRunner`。

> **拆分 guardrail**：当前 `ExecutionRunner` 约 146 行，加入生命周期管理后预计 ~250 行，仍在合理范围。如果后续持续膨胀超过 ~300 行（例如需要管理 controller factory、多种执行模式），应拆出独立的 `ExecutionOrchestrator`，Runner 回归纯调度器职责。

**涉及文件**：
- `src/api/routers/chat.py` — lease/interactive/stream 操作移出
- `src/api/services/execution_runner.py` — 接管完整生命周期 + fencing
- `src/api/services/runtime_store.py` — `renew_lease` 返回类型改 `bool`
- `src/api/services/redis_runtime_store.py` — `renew_lease` 返回 compare-and-expire 结果

**修复建议**：

**1. `renew_lease` → 返回 `bool`**

```python
# RuntimeStore Protocol
async def renew_lease(self, conversation_id: str, message_id: str, ttl: float) -> bool: ...

# RedisRuntimeStore — 检查 pipeline 结果
async def renew_lease(self, ...) -> bool:
    pipe = self._redis.pipeline(transaction=False)
    pipe.evalsha(self._sha_compare_and_expire, 1, self._lease_key(conversation_id), message_id, str(ttl_int))
    pipe.evalsha(self._sha_compare_and_expire, 1, self._interactive_key(conversation_id), message_id, str(ttl_int))
    results = await pipe.execute()
    return results[0] == 1  # lease key 续租成功

# InMemoryRuntimeStore — 永远返回 True
async def renew_lease(self, ...) -> bool:
    return True
```

**2. `_renew_loop` 检测到 lease 丢失 → `task.cancel()`**

```python
async def _renew_loop(self, conversation_id, task_id):
    interval = self._lease_ttl // 3
    while True:
        await asyncio.sleep(interval)
        try:
            still_owner = await self.store.renew_lease(conversation_id, task_id, ttl=self._lease_ttl)
        except Exception:
            logger.warning(f"Heartbeat renewal failed for {task_id} (network issue)")
            continue  # 网络抖动：下次重试，还有 2 次机会（TTL/3 间隔）

        if not still_owner:
            logger.error(f"Lease lost for {task_id} — fencing execution")
            task = self._tasks.get(task_id)
            if task:
                task.cancel()  # 注入 CancelledError，整条调用链展开
            return
```

**3. `CancelledError` 天然跳过 post-processing**

`task.cancel()` 会向 `execute_and_push → _run_and_push → controller.stream_execute` 注入 `CancelledError`。controller 的 post-processing（flush artifacts、update response）在 engine loop 之后的顺序代码中，`CancelledError` 会直接跳过这些代码 → **不需要额外 flag，写 DB 操作天然被阻止**。

```
_renew_loop: task.cancel()
    ↓
CancelledError 注入到 coro 当前 await 的点
    ├─ engine 正在 call_llm → 中断
    ├─ engine 正在 wait_for_interrupt → 中断
    ├─ controller 正在 event_queue.get() → 中断
    ↓
stream_execute 的 post-processing 被跳过（flush_all、update_response 不执行）
    ↓
ExecutionRunner._wrapped() finally → cleanup_execution（正常清理）
```

**4. 执行生命周期收敛到 `ExecutionRunner.submit()`**

```python
# ExecutionRunner — submit 接管全部生命周期
async def submit(self, conversation_id, task_id, coro, *,
                 user_id=None, stream_transport=None) -> asyncio.Task:
    # 1. 拿 lease（原来在 chat.py:209）
    active = await self.store.try_acquire_lease(conversation_id, task_id)
    if active:
        raise ConflictError(f"Execution already active: {active}")

    try:
        # 2. 标记 interactive（原来在 chat.py:220）
        await self.store.mark_engine_interactive(conversation_id, task_id)
        # 3. 创建 stream（原来在 chat.py:226）
        if stream_transport:
            await stream_transport.create_stream(task_id, owner_user_id=user_id)
    except Exception:
        await self.store.release_lease(conversation_id, task_id)
        await self.store.clear_engine_interactive(conversation_id, task_id)
        raise

    # 4. 提交后台任务（心跳 + fencing 在 _wrapped 中）
    ...

# chat.py — 简化为参数校验 + 鉴权 + 调度
@router.post("")
async def send_message(request, current_user, runner, ...):
    conversation_id = request.conversation_id or f"conv-{uuid4().hex}"
    message_id = f"msg-{uuid4().hex}"

    if request.conversation_id:
        await _verify_ownership(conversation_id, current_user, conversation_manager)

    try:
        await runner.submit(conversation_id, message_id, execute_and_push(),
                           user_id=current_user.user_id, stream_transport=stream_transport)
    except ConflictError:
        raise HTTPException(status_code=409, detail="...")

    return ChatResponse(conversation_id=conversation_id, message_id=message_id, ...)
```

**关键点**：engine 和 controller 都不需要改动。engine 完全不知道 lease 的存在，`CancelledError` 是 asyncio 的标准取消机制。

---

### F-02 Health 端点缺少深度检查

**来源**：Reviewer P2 + 自研 Review

**问题**：`main.py:98` 永远返回 `{"status": "healthy"}`，不验证 DB 和 Redis 连通性。负载均衡器会继续给故障节点分流量。

**涉及文件**：
- `src/api/main.py`

**修复建议**：

拆分为 liveness + readiness 两个端点：

```python
@app.get("/health/live")    # 进程活着 — K8s liveness probe
async def liveness():
    return {"status": "ok"}

@app.get("/health/ready")   # 可接受流量 — K8s readiness probe / LB health check
async def readiness():
    checks = {}
    try:
        async with db_manager.session() as s:
            await s.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = str(e)

    if redis_client:
        try:
            await redis_client.ping()
            checks["redis"] = "ok"
        except Exception as e:
            checks["redis"] = str(e)

    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(checks, status_code=200 if healthy else 503)
```

保留原 `/health` 指向 readiness（向后兼容）或直接替换。

---

### F-03 `get_stream_status` 同步/异步接口不一致

**来源**：自研 Review

**等级调整**：P0 → P2（当前无生产调用点，属于接口清理，非功能性缺陷）

**问题**：`StreamTransport` Protocol 中 `get_stream_status` 是同步方法。`RedisStreamTransport` 无法同步读 Redis，当前直接返回 `None`（`redis_stream_transport.py:210-213`），另有 `get_stream_status_async`。如果未来调用方依赖同步版本做状态判断，Redis 模式下会返回错误结果。

**涉及文件**：
- `src/api/services/stream_transport.py` — Protocol 定义
- `src/api/services/redis_stream_transport.py`

**修复建议**：Protocol 中 `get_stream_status` 改为 `async`，`InMemoryStreamTransport` 对应加 `async`（dict 操作不阻塞），调用点加 `await`。删除 `get_stream_status_async`。

---

### F-04 `DATABASE_URL` 硬编码默认值（生产隐患）

**来源**：自研 Review + Reviewer 修正

**问题**：`config.py` 中 `DATABASE_URL` 的默认值硬编码为 SQLite。应用启动时 `dependencies.py:83` 总会把 `config.DATABASE_URL` 传给 `DatabaseManager`，所以 `DatabaseManager.__init__` 的 `None` fallback 不是真正的风险点——真正的风险是 `.env` 没配 `DATABASE_URL` 时，`config.py` 的默认值直接生效为 SQLite，双机部署各自用独立 SQLite，数据不同步且难以排查。

**涉及文件**：
- `src/config.py` — `DATABASE_URL` 默认值（主要修复点）
- `src/db/database.py` — `__init__` 的 `None` fallback（顺带清理）

**修复建议**：

1. `config.py`：`DATABASE_URL` 默认空字符串，不提供 SQLite fallback
2. `config.py` 或 `dependencies.py` 的 `init_globals()`：启动时校验 `DATABASE_URL` 非空，空值 fail fast
3. `database.py`：移除 `database_url is None` 的 SQLite fallback（`create_test_database_manager()` 显式传 SQLite URL，不受影响）

所有数据库配置统一通过 `.env` 提供，代码中不硬编码任何默认 URL。

---

## P1 — 应该修复

### F-05 断线重连与 permission auto-deny 冲突

**来源**：Reviewer P3

**问题**：`stream.py:99-107` 在 `CancelledError`（客户端断连）时立刻 auto-deny pending interrupt。但 Redis Streams 已支持 `last_event_id` 断线重连（`redis_stream_transport.py:146`）。两个逻辑矛盾：如果认为断线是临时的→不应立刻 deny；如果认为用户走了→不需要重连基础设施。

网络抖动、LB 切连接、滚动发布期间的短暂断线都会被当成"用户拒绝工具调用"。

**涉及文件**：
- `src/api/routers/stream.py` — `CancelledError` 处理

**修复建议**：

三个方案按复杂度递增：

1. **最简方案**：删掉 auto-deny，依赖 `PERMISSION_TIMEOUT`（通常 300s）自然超时。缺点：用户真的走了也要等 5 分钟
2. **Grace period**：断连后不立刻 deny，启动延迟任务（比如 30s），期间客户端重连则取消 deny。需要额外状态管理
3. **Consumer 计数**：Redis 记录 stream 是否有活跃 consumer，只有确认无 consumer 且超过 grace period 才 deny

建议先用方案 1（最安全），后续按 UX 需求升级到方案 2。

---

### F-06 `consume_events` XREAD 断连无恢复

**来源**：Reviewer P2（故障链路经 Reviewer 二轮修正）

**问题**：`redis_stream_transport.py:159` 的 `XREAD BLOCK` 循环中，Redis 断连（云 Redis 主从切换 1-3s）会抛 `ConnectionError`，当前无捕获 → 异常从 `consume_events` 生成器抛出 → 被 `stream.py:109` 的 `except Exception` 捕获（**不是** `CancelledError` 分支）→ 给前端推 error event → SSE 连接中断。

注意：此路径**不会**触发 `stream.py:99` 的 auto-deny interrupt（那是 `CancelledError` 分支，仅在客户端主动断连时触发）。直接后果是流中断/报错，不是自动拒绝 permission。

**涉及文件**：
- `src/api/services/redis_stream_transport.py` — `consume_events`

**修复建议**：

在 XREAD 循环中加 `ConnectionError` 重试：

```python
while True:
    try:
        result = await self._redis.xread(...)
    except aioredis.ConnectionError:
        logger.warning(f"Redis connection lost during consume {stream_id}, retrying...")
        await asyncio.sleep(1)  # 短暂等待 Redis 恢复
        continue
    # ... 正常处理
```

注意：cursor 已在本地维护，重连后从上次位置继续读即可。

---

### F-07 Lua 脚本 NOSCRIPT 容错

**来源**：自研 Review

**问题**：`init_scripts()` 在启动时 `SCRIPT LOAD`，但 Redis `SCRIPT FLUSH`（运维操作）或主从切换后 SHA 失效，`evalsha` 会抛 `NOSCRIPT`。

**涉及文件**：
- `src/api/services/redis_runtime_store.py` — 所有 `evalsha` 调用
- `src/api/services/redis_stream_transport.py` — `evalsha` 调用

**修复建议**：

用 redis-py 内置的 `Script` 对象替代手动 `script_load` + `evalsha`：

```python
# 初始化时
self._acquire_lease = self._redis.register_script(_LUA_ACQUIRE_LEASE)

# 调用时（自动处理 NOSCRIPT → re-load → retry）
result = await self._acquire_lease(keys=[key], args=[message_id, str(ttl)])
```

`Script` 对象内部已实现 NOSCRIPT 自动 re-load，无需手动管理 SHA。

---

### F-08 Redis 连接缺少 retry 策略

**来源**：自研 Review

**问题**：`dependencies.py:100` 用 `aioredis.from_url()` 创建客户端，未配置重试策略。云 Redis 主从切换（1-3s）期间所有 Redis 操作都会失败。

**涉及文件**：
- `src/api/dependencies.py` — Redis 客户端创建

**修复建议**：

```python
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

_redis_client = aioredis.from_url(
    config.REDIS_URL,
    decode_responses=True,
    retry=Retry(ExponentialBackoff(cap=2, base=0.1), retries=3),
    retry_on_timeout=True,
)
```

这覆盖了短暂网络抖动场景。对于 Pub/Sub 连接（长连接），redis-py 的 retry 不适用，需要在业务层处理（见 F-06、F-09）。

---

## P2 — 建议修复 / 加固

### F-09 `wait_for_interrupt` Pub/Sub 断连无捕获

**来源**：Reviewer P2

**问题**：`redis_runtime_store.py:204` 的 Pub/Sub 循环中，Redis 断连会抛 `ConnectionError`，当前无捕获 → 异常上抛 → engine 崩溃。

自然兜底：interrupt 有 `PERMISSION_TIMEOUT`（通常 300s），即使不做恢复，最坏情况是超时 deny。但 engine 异常退出比正常超时更难处理（error state vs timeout deny）。

**涉及文件**：
- `src/api/services/redis_runtime_store.py` — `wait_for_interrupt`

**修复建议**：

在 Pub/Sub 循环中捕获 `ConnectionError`，重走 check-subscribe-check-wait：

```python
except aioredis.ConnectionError:
    logger.warning(f"Pub/Sub connection lost for {message_id}, re-checking status...")
    await pubsub.aclose()
    # 重新检查状态（可能在断连期间已 resolve）
    if await self._is_resolved(message_id):
        return await self._get_resume_data(message_id)
    # 重建 Pub/Sub 订阅
    pubsub = self._redis.pubsub()
    await pubsub.subscribe(channel_name)
    # 双重检查
    if await self._is_resolved(message_id):
        ...
```

或简化为：捕获 `ConnectionError` → `return None`（视为超时 deny），行为与现有 timeout 一致。

---

### F-10 删除 event fallback 本地文件（假安全感）

**来源**：Reviewer P2 + 重新评估

**问题**：`controller.py:384` 事件持久化 3 次重试失败后 fallback 到 `logs/events_fallback.jsonl`。

这个 fallback 机制有害无益：
1. 容器/双机环境下本地文件不共享，实例被杀或磁盘是临时卷时仍然丢失
2. 没有 replay 机制——写了也没人读
3. fallback 本身也可能失败（`controller.py:406`），最终只是 `logger.critical` 然后结束
4. 给人"数据被兜住了"的假安全感，实际上就是丢了

**影响范围**：events 是审计/回放数据（哪个 agent 调了什么 tool），不影响核心业务路径。在 `_persist_events` 被调用前，`flush_all`（artifact 持久化）和 `update_response_async`（对话响应持久化）已经完成。用户的对话结果和 artifact 不受影响。

**涉及文件**：
- `src/core/controller.py` — `_persist_events` / `_write_fallback_events`

**修复建议**：

直接删除 `_write_fallback_events` 方法和调用。3 次重试失败后 `logger.error` 记录丢失的事件摘要（message_id + event count）→ 结束。不做 fallback。

```python
# 改动后的 _persist_events 末尾：
else:
    logger.error(
        f"Event persistence failed after {max_retries} attempts for {message_id} "
        f"({len(db_events)} events lost): {e}"
    )
    # 不做 fallback — events 是审计数据，conversation + artifact 已持久化
```

如果未来有审计合规需求，再考虑 outbox pattern（events 与业务写在同一事务）。

---

### F-11 `llm_chunk` 每 token 一条全量快照 → coalesced snapshots

**来源**：自研 Review + Reviewer 深化分析

**问题**：`engine.py:265-268` 每收到一个 LLM token 就 emit 一条 `llm_chunk`，内容是 `response_content` 全量累积文本（不是 delta）。这导致：

1. **O(n²) 字节量**：假设最终 response 2000 字符、500 次 chunk，总传输 ≈ 1+2+3+...+2000 ≈ 200 万字符
2. **Redis Stream 被灌满**：500 条 chunk + 其他事件，MAXLEN 1000 几乎用尽，断线重连时旧事件可能已被修剪
3. **前端只用最新快照**：`useSSE.ts:114` 直接用 `content` 覆盖显示，不依赖逐 token append，中间态全浪费

**涉及文件**：
- `src/api/routers/chat.py` — `_run_and_push`（coalescer 放置位置）
- `src/api/services/redis_stream_transport.py` — MAXLEN 参数

**修复建议**：

在 `_run_and_push` 中加 coalescing 逻辑（engine、transport、前端都不改）：

- **只合并 `llm_chunk`**，其他事件类型（`tool_call`、`agent_start`、`complete` 等）立即推送
- flush 条件：
  - 距上次 flush ≥ 50-100ms
  - 或累计新增 ≥ 32-128 字符
  - 遇到非 `llm_chunk` 事件前强制 flush 缓冲的最后一条

```python
async def _run_and_push(stream_transport, stream_id, event_stream):
    pending_chunk = None        # 缓冲的最新 llm_chunk
    last_flush_time = 0.0

    async def flush_pending():
        nonlocal pending_chunk, last_flush_time
        if pending_chunk:
            await stream_transport.push_event(stream_id, pending_chunk)
            pending_chunk = None
            last_flush_time = asyncio.get_event_loop().time()

    async for event in event_stream:
        if event.get("type") == "llm_chunk":
            pending_chunk = event  # 只保留最新（累积快照语义，旧的可丢）
            now = asyncio.get_event_loop().time()
            if now - last_flush_time >= 0.08:  # 80ms 节流
                await flush_pending()
        else:
            await flush_pending()  # 非 chunk 事件前先 flush
            await stream_transport.push_event(stream_id, event)

    await flush_pending()  # 确保最后一条 chunk 被发出
```

**为什么放在 `_run_and_push`**：
- engine 是纯逻辑层，不应知道传输优化
- transport 是通用传输层，不应有事件类型语义
- `_run_and_push` 是 controller → transport 的桥梁，天然是 coalescing 的位置

**效果**：
- 2000 字符回复：~500 条 chunk → ~25 条（80ms 节流），Redis Stream 压力降 95%
- MAXLEN 1000 绰绰有余，可保持不改
- 断线重连仍安全：每条仍是累计快照，丢中间态靠下一条追平
- 前端零改动

---

### F-12 `push_event` check-then-act 竞态

**来源**：自研 Review

**问题**：`redis_stream_transport.py:96-124` 先 `HGET status`，再 `exists(stream_key)`，再 `XADD`，三步非原子。`close_stream` 可能在 HGET 之后、XADD 之前执行，事件写入已关闭的 stream。

**影响**：实际危害有限（TTL 会清理），consumer 已正常退出。

**涉及文件**：
- `src/api/services/redis_stream_transport.py` — `push_event`

**修复建议**：可以用 Lua 脚本原子化 `HGET status + XADD`，或接受这个 edge case（在注释中标注已知竞态窗口）。

---

### F-13 Redis key 无命名空间隔离

**来源**：自研 Review

**问题**：所有 key 用 `lease:`, `stream:`, `interrupt:` 等扁平前缀。同一 Redis 实例被多环境（staging/production）或多 ArtifactFlow 实例共用时 key 冲突。

**涉及文件**：
- `src/api/services/redis_runtime_store.py` — key helper 方法
- `src/api/services/redis_stream_transport.py` — key helper 方法

**修复建议**：加可配置前缀：

```python
# config.py
REDIS_KEY_PREFIX: str = "af"

# redis_runtime_store.py
def _lease_key(self, conversation_id: str) -> str:
    return f"{self._prefix}:lease:{conversation_id}"
```

---

### F-14 Stream TTL 默认 30s 偏短

**来源**：自研 Review

**问题**：`redis_stream_transport.py:51` 默认 `stream_ttl=30`。前端首次加载 JS bundle + 网络延迟可能超过 30s，stream metadata 过期后 consumer 连接失败。

**涉及文件**：
- `src/api/services/redis_stream_transport.py`
- `src/config.py`

**修复建议**：生产环境建议 60-120s，确保在 config 中暴露为可配置项。

---

### F-15 InMemory `cleanup_execution` 全量扫描

**来源**：自研 Review

**问题**：`runtime_store.py:210-215` 每次 cleanup 对 `_conversation_leases` 和 `_engine_interactive` 做 dict comprehension 全量扫描（O(n)）。

**影响**：单 Worker 并发量不大时无影响，大量活跃对话时成为热点。

**涉及文件**：
- `src/api/services/runtime_store.py` — `InMemoryRuntimeStore.cleanup_execution`

**修复建议**：维护 `message_id → conversation_id` 反向映射，cleanup 时 O(1) 删除。

---

## 架构问题 — Router / Service 职责划分

> 在分析 F-01 时发现的系统性问题：路由层（routers）承担了大量应属服务层（services/core）的职责。
> 以下审计覆盖了 `src/api/routers/` 下的所有文件。

### 审计结论

**chat.py** 是重灾区，包含 7 处职责越界。**stream.py** 有 1 处。**auth.py** 和 **artifacts.py** 分层清晰，无问题。

### R-01 chat.py: 执行编排逻辑散落在路由层

**位置**：`chat.py:208-260`（`send_message` 函数）

`send_message` 中的多步编排序列本应是服务层的原子操作：

```
chat.py 当前做的事（不该做）：
  ① try_acquire_lease        ← RuntimeStore 操作
  ② mark_engine_interactive  ← RuntimeStore 操作
  ③ ensure_conversation      ← 业务逻辑
  ④ create_stream            ← StreamTransport 操作
  ⑤ 组装 execute_and_push    ← 协程编排
  ⑥ runner.submit            ← 调度
  ⑦ 失败回滚 release/clear   ← 生命周期管理
```

**问题**：
- 路由层知道了 lease、interactive、stream 三者的创建顺序和回滚逻辑
- 如果未来新增入口（如 WebSocket、gRPC），这些逻辑必须重复
- 错误处理分散：HTTP 层回滚在 `try/except`，后台任务错误在 `execute_and_push` 的内层 `except`

**归属**：全部收敛到 `ExecutionRunner.submit()` 或新建 `ExecutionOrchestrator`。路由层只需 `await runner.submit(...)` + HTTP 状态码映射。

**与 F-01 的关系**：F-01 的修复方案已包含此重构（lease 获取 + stream 创建 + interactive 标记全部移入 runner）。

---

### R-02 chat.py: Controller 实例化在路由层

**位置**：`chat.py:74-133`（`_create_controller` context manager）

路由层负责组装 `ExecutionController` 的全部依赖（session、repo、hooks、tools），包含 ~60 行基础设施代码。

**问题**：
- 路由知道 controller 的内部依赖结构（ArtifactRepository、ConversationRepository、MessageEventRepository...）
- DB session 在路由层创建但被后台任务使用（生命周期跨越了 HTTP 请求）
- 修改 controller 依赖需要同时改路由代码

**归属**：移到 `ExecutionRunner` 或独立 factory，路由层不应知道 controller 的构造细节。

---

### R-03 chat.py: 路由直接访问 `runner.store`

**位置**：`chat.py` 多处（`send_message`、`inject_message`、`cancel_execution`、`resume_execution`）

路由层通过 `runner.store` 直接调用 RuntimeStore 的方法（`try_acquire_lease`、`get_interactive_message_id`、`inject_message`、`request_cancel`、`resolve_interrupt`）。`RuntimeStore` 是 `ExecutionRunner` 的实现细节，不应暴露给路由层。

**问题**：
- 路由层与 RuntimeStore 的 API 紧耦合
- 语义泄漏：路由知道 "lease"、"interactive" 这些运行时概念
- 如果 RuntimeStore 接口变更，所有路由都要改

**归属**：`ExecutionRunner` 应暴露高层方法，隐藏 RuntimeStore 细节：

| 当前（路由直接调 store） | 应改为（runner 方法） |
|---|---|
| `store.get_interactive_message_id(conv_id)` | `runner.get_active_execution(conv_id)` |
| `store.inject_message(msg_id, content)` | `runner.inject(conv_id, content)` |
| `store.request_cancel(msg_id)` | `runner.cancel(conv_id)` |
| `store.resolve_interrupt(msg_id, data)` | `runner.resolve_interrupt(msg_id, data)` |

`inject` 和 `cancel` 可以用 `conv_id`（语义上就是"对当前活跃执行操作"，runner 内部做 conv→msg 映射）。

**但 `resume` 必须保留 `message_id` 粒度**：permission-resume 是幂等操作，需要精确匹配到具体的 interrupt。如果只用 `conv_id` 找"当前活跃执行"，旧页面或重试请求可能把审批结果打到新一轮执行的 interrupt 上，语义错位。`message_id` 是 resume 的外部 contract，不能降级。

---

### R-04 stream.py: 断连时直接 resolve interrupt

**位置**：`stream.py:99-107`

stream 路由在 `CancelledError`（客户端断连）时直接调 `runner.store.resolve_interrupt()` auto-deny。

**问题**：
- stream 路由是传输层（事件搬运），不应做运行时状态决策
- 断连处理策略（立即 deny / grace period / 忽略）属于执行生命周期逻辑
- 与 F-05（断线 auto-deny 与重连冲突）相关

**归属**：断连后的 interrupt 处理逻辑应在 `ExecutionRunner` 或 RuntimeStore 层面统一管理。

---

### 不存在问题的路由

| 文件 | 评估 |
|------|------|
| `auth.py` | ✅ 纯 HTTP 认证 + token 操作，正确委托给 `auth` service |
| `artifacts.py` | ✅ 参数校验 + 委托给 `ArtifactManager`，无越界 |
| `stream.py`（除 R-04） | ✅ 事件消费 + SSE 格式化，正确委托给 `StreamTransport` |

> 注：对话相关的 CRUD 端点（list/get/delete/rename）直接在 `chat.py` 中，没有独立的 `conversations.py` 路由文件。

---

### 重构影响范围

R-01 ~ R-03 的修复与 F-01 高度重叠（都是把 chat.py 中的执行生命周期逻辑收敛到 `ExecutionRunner`），建议合并实施。R-04 与 F-05 重叠。

预期改动后的 `chat.py` `send_message`：

```python
@router.post("", response_model=ChatResponse)
async def send_message(
    request: ChatRequest,
    current_user: TokenPayload = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
    runner: ExecutionRunner = Depends(get_execution_runner),
):
    conversation_id = request.conversation_id or f"conv-{uuid4().hex}"
    message_id = f"msg-{uuid4().hex}"

    # 路由层只做鉴权
    if request.conversation_id:
        await _verify_ownership(conversation_id, current_user, conversation_manager)

    # 调度 — 一行搞定，所有生命周期管理在 runner 内部
    try:
        await runner.submit(conversation_id, message_id, ...,
                           user_id=current_user.user_id)
    except ConflictError:
        raise HTTPException(status_code=409, detail="Execution already active")

    return ChatResponse(conversation_id=conversation_id,
                       message_id=message_id,
                       stream_url=f"/api/v1/stream/{message_id}")
```

---

## 修复优先级总览

| 序号 | ID | 问题 | 等级 | 工作量 |
|------|-----|------|------|--------|
| 1 | F-01 | Lease fencing + 执行生命周期收敛到 Runner（含 R-01~R-03） | P0 | 中 |
| 2 | F-02 | Health readiness 深度检查 | P0 | 小 |
| 3 | F-04 | `DATABASE_URL` 配置层 fail-fast | P0 | 极小 |
| 4 | F-05 | 断线 auto-deny → grace period（含 R-04） | P1 | 小 |
| 5 | F-06 | XREAD 断连重试 | P1 | 中 |
| 6 | F-07 | Lua NOSCRIPT 容错 | P1 | 中 |
| 7 | F-08 | Redis 连接 retry 策略 | P1 | 小 |
| 8 | F-09 | Pub/Sub 断连捕获 | P2 | 中 |
| 9 | F-10 | 删除 event fallback 本地文件 | P2 | 极小 |
| 10 | F-03 | `get_stream_status` 接口统一 | P2 | 小 |
| 11 | F-11 | Stream MAXLEN 调大 | P2 | 极小 |
| 12 | F-12 | `push_event` 竞态 | P2 | 小 |
| 13 | F-13 | Redis key 命名空间 | P2 | 小 |
| 14 | F-14 | Stream TTL 调大 | P2 | 极小 |
| 15 | F-15 | InMemory cleanup O(n) | P2 | 小 |

**建议 PR 序列**（配置修正、结构重构、语义变更不混在同一 PR，方便 bisect）：

| PR | 内容 | 性质 | 回归面 |
|----|------|------|--------|
| **PR1** | F-02 + F-04 | 配置 + 运维 | 极小：health 端点 + config 默认值 |
| **PR2** | R-01 ~ R-03 | 结构重构 | 中：chat.py 瘦身 → Runner 接管生命周期，**行为不变** |
| **PR3** | F-01 | 并发语义变更 | 中：renew_lease → bool、lease lost → task.cancel()、跳过 post-processing |
| **PR4** | F-05（含 R-04）~ F-09（F-09 从 P2 提前合入） | Redis 韧性 | 中：XREAD 重试、Pub/Sub 重试、auto-deny 改 grace period、连接 retry、NOSCRIPT 容错 |
| **PR5** | F-10 ~ F-15 | 加固清理 | 小：按需挑选 |

**为什么 F-09 提前到 PR4**：只修 XREAD（F-06）不修 Pub/Sub（F-09），Redis failover 恢复只做了一半——主从切换时 XREAD 能重连但 Pub/Sub 断了，interrupt 仍然会异常退出。两者应同批处理。
