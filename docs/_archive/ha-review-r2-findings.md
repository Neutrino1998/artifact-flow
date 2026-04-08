# 高可用 Review 第二轮发现与修复建议

> PR1–PR5 全部合入后的二轮 review。
> 来源：自研 review + 外部 reviewer 反馈，合并去重后统一排列。
> 评估基线：异地双活（北京 + 上海），每中心两台服务器各一个实例，不涉及跨中心流量。各中心共享云托管 TDSQL + Redis，Redis 不跨中心同步。
> Redis：组 9 数据分析区，华为大数据平台 redis Cluster，Redis 5.0+，`cluster-node-timeout` 默认 15s，failover 窗口约 15-20+ 秒。
> TDSQL：YDB 资源组一，集中式实例（1 主 2 从，选 3 DN），DCN 同步，配置 3 PX 地址接入（无 VIP），REPEATABLE READ。
>
> **目标边界**：本轮修复范围限定在**单中心内**的双实例高可用。不考虑跨中心续跑——切中心时直接杀掉该中心所有 in-flight 任务，用户在目标中心重新发起。所有 finding 的方案设计不隐含"跨中心状态迁移"的预期。

---

## 一轮后的能力边界更新

| 能力 | 状态 | 变化 |
|------|------|------|
| 共享控制面（lease/interrupt/stream 跨 Worker） | ✅ | 无变化 |
| Fencing / split-brain 防护 | ✅ | 无变化（PR3） |
| 故障检测（readiness） | ✅ | 无变化（PR1） |
| Redis failover 恢复 | ✅ | 无变化（PR4） |
| 事件持久化保证 | ⚠️ 降级 | 无变化（PR5 清理了假 fallback） |
| Multi-PX TDSQL failover | ❌ 阻塞 | F-16（PR4）引入了 asyncmy 硬依赖，干净环境 `ModuleNotFoundError` |
| Stream 生命周期与执行生命周期对齐 | ✅ | PR7 已修复 — stream TTL 对齐 EXECUTION_TIMEOUT，consumer 断连不缩短 TTL |
| Compaction 跨实例互斥 | ✅ | PR7 已修复 — owner-key 原语 + 分布式锁 + 心跳续租 + lock-loss fencing |

---

## P0 — 必须修复

### F-17 Multi-PX failover 路径依赖未声明的 asyncmy

**来源**：Reviewer P0 + 环境验证

**问题**：F-16（PR4）在 `database.py:148-158` 添加了多 PX failover 支持，当 `DATABASE_URLS` 配置多个地址时切换到 `async_creator` 并 `import asyncmy`。但 `requirements.txt:40` 只声明了 `aiomysql`，干净环境执行 `import asyncmy` 直接 `ModuleNotFoundError`。

**注意**：不只是 import 问题。`_parse_db_url`（`database.py:99-113`）的注释也写着"解析出 asyncmy.connect kwargs"，整个 failover 路径都是按 asyncmy API 设计的。但主连接路径（`create_async_engine` + `mysql+aiomysql://`）用的是 aiomysql。两套驱动并存是隐患。

**涉及文件**：
- `src/db/database.py` — `_failover_creator` + `_parse_db_url`
- `requirements.txt`

**修复建议**：

统一到 aiomysql（最小改动，不引入新依赖）：

```python
# database.py:152-158
async def _failover_creator():
    """Primary-first: 按配置顺序尝试，首个成功即返回"""
    import aiomysql                      # ← 替换 asyncmy
    errors = []
    for target in parsed_urls:
        try:
            return await aiomysql.connect(**target, connect_timeout=5)
        except Exception as e:
            errors.append((target["host"], e))
            logger.warning(f"DB connect failed: {target['host']}: {e}")
    raise ConnectionError(
        f"All DB nodes unreachable: {[(h, str(e)) for h, e in errors]}"
    )
```

同步更新 `_parse_db_url` 注释：`asyncmy.connect kwargs` → `aiomysql.connect kwargs`。

> **aiomysql vs asyncmy API 差异**：`aiomysql.connect()` 和 `asyncmy.connect()` 的核心参数（`host`, `port`, `user`, `password`, `db`, `connect_timeout`）完全兼容。`_parse_db_url` 的输出无需修改。

---

## P1 — 应该修复

### F-18 Stream 可恢复窗口远短于执行/权限窗口

**来源**：Reviewer P1

**问题**：三个 TTL/Timeout 窗口不对齐：

| 配置项 | 值 | 含义 |
|--------|-----|------|
| `STREAM_TTL`（→ `STREAM_CLEANUP_TTL`） | 60s | consumer 断连后 stream 存活时间 |
| `PERMISSION_TIMEOUT` | 300s | 单次权限确认等待上限 |
| `STREAM_TIMEOUT`（→ `EXECUTION_TIMEOUT`） | 1800s | 总执行时间上限 |

`redis_stream_transport.py:211-217`：consumer 断连后 CAS 回退到 pending 并设 `STREAM_TTL`（60s）过期。60s 后 stream key + meta key 被 Redis 清理。

**后果不是孤儿 stream**——`push_event`（`redis_stream_transport.py:105-107`）会先查 meta，meta 过期后返回 `False`，不会创建新 stream key。真正的后果是：

1. **事件静默丢弃**：producer 仍在运行，但 `push_event` 全部返回 `False`，后续事件无法到达任何 consumer
2. **流不可恢复**：`chat.py:149` 的 `active-stream` 只查 lease 不查 stream 状态，会返回一个已过期 stream 的 URL → 前端连接 404
3. **权限确认断路**：如果断连期间引擎触发了 `permission_request` 事件，该事件已被丢弃。即使重建空 stream，前端也看不到 pending interrupt → 权限确认只能等 `PERMISSION_TIMEOUT` 超时 deny

**时序示例**：
```
t=0s    用户发送消息，engine 开始执行
t=10s   engine 调用 CONFIRM 级别工具，触发 permission interrupt
t=15s   用户网络抖动，SSE 断连
t=15s   consumer finally → CAS 回退 pending + EXPIRE 60s
t=75s   stream key + meta key 过期，Redis 清理
t=76s   engine 推送 permission_request → push_event 查 meta 为 None → return False（事件丢弃）
t=80s   用户网络恢复，调 /active-stream → 返回 stream URL（lease 仍在）
t=80s   前端连接 stream URL → consume_events 查 meta 为 None → 404
        用户无法看到 pending interrupt，无法审批
t=310s  permission interrupt 超时 deny，engine 继续执行（工具被拒绝）
```

**涉及文件**：
- `src/api/services/redis_stream_transport.py` — `consume_events` finally 块、`push_event`
- `src/api/routers/chat.py` — `get_active_stream`
- `src/config.py` — `STREAM_TTL`

**修复建议**：

核心思路：**stream 的存活由执行生命周期决定，不由 consumer 断连决定**。`create_stream` 时一次性设足够长的 TTL（= 执行上限），执行结束时 `close_stream` 缩短为清理窗口。中间不需要任何续期操作。

> **配置项重命名**（语义对齐）：
> | 当前 | 改为 | 语义 |
> |---|---|---|
> | `STREAM_TIMEOUT` | `EXECUTION_TIMEOUT` | 执行时间上限。同时用作 stream lifetime、cancel/queue key TTL — 这些都跟随执行生命周期 |
> | `STREAM_TTL` | `STREAM_CLEANUP_TTL` | 执行结束后 consumer 读取剩余事件的清理窗口 |
>
> 所有引用点同步重命名：`config.py`、`dependencies.py`、`redis_stream_transport.py`、`redis_runtime_store.py`、`controller_factory.py`。
> ⚠️ 环境变量名也会变（`ARTIFACTFLOW_STREAM_TIMEOUT` → `ARTIFACTFLOW_EXECUTION_TIMEOUT`），已部署的 `.env` 需同步更新。

**1. `create_stream` 设 `EXECUTION_TIMEOUT` TTL**

```python
# redis_stream_transport.py — create_stream
await self._redis.hset(meta_key, mapping={
    "owner": owner_user_id or "",
    "status": "pending",
})
await self._redis.expire(meta_key, self._execution_timeout)  # ← 改：执行上限，而非短 TTL
```

**2. `push_event` 删除 per-write TTL 续期**

当前 `push_event` 在首次 XADD 后设 `STREAM_TTL`。不再需要 per-write 续期——stream key 生命周期由 `create_stream` / `close_stream` 管理。但 stream key 在首次 XADD 时才被 Redis 创建，需要在首次 XADD 后补设与 meta_key 对齐的 TTL：

```python
# redis_stream_transport.py — push_event
# 首次 XADD 后设 EXECUTION_TIMEOUT（与 meta_key 对齐，防孤儿 key）
if first_push:
    await self._redis.expire(stream_key, self._execution_timeout)
```

**3. `consume_events` 连接时：删除 `persist()` 调用**

> ⚠️ **实现陷阱**：当前 `consume_events`（`redis_stream_transport.py:159-160`）在 consumer 连接时调用 `persist(meta_key)` / `persist(stream_key)` 移除 TTL。在新方案下这两行必须删除，否则 consumer 一连接就把 TTL 清空，如果 producer crash 未调 `close_stream`，stream 会成为永久孤儿 key。

```python
# redis_stream_transport.py — consume_events
# 标记为 streaming（保留现有逻辑）
consumer_id = os.urandom(8).hex()
await self._redis.hset(meta_key, mapping={
    "status": "streaming",
    "consumer_id": consumer_id,
})
# 删除以下两行：
# await self._redis.persist(meta_key)   ← 删
# await self._redis.persist(stream_key) ← 删
```

**4. consumer 断连回退：不动 TTL**

```python
# redis_stream_transport.py — consume_events finally 块
# CAS 回退 meta 到 pending，但不缩短 TTL（stream 已有 EXECUTION_TIMEOUT TTL）
reverted = await self._script_revert_to_pending(
    keys=[meta_key],
    args=[consumer_id],  # ← 删除 ttl 参数
)
# 不再 EXPIRE stream_key — 保持原有 TTL 不变
```

对应 Lua 脚本 `_LUA_REVERT_TO_PENDING` 也简化：删除 `EXPIRE` 调用，只做状态回退。

**5. `close_stream` 缩短到 `STREAM_CLEANUP_TTL`**

```python
# redis_stream_transport.py — close_stream（现有逻辑已正确，仅字段重命名）
cleanup_ttl = max(self._cleanup_ttl, 10)
```

**6. `active-stream` 增加 stream 存活性校验**

```python
# chat.py — get_active_stream
message_id = await runner.store.get_leased_message_id(conv_id)
if not message_id:
    raise HTTPException(status_code=404, detail="No active execution")

# 校验 stream meta 是否仍存在
stream_alive = await stream_transport.is_stream_alive(message_id)
if not stream_alive:
    raise HTTPException(status_code=410, detail="Stream expired, execution still running")
```

`is_stream_alive` 只需 `HGET meta_key status`，返回 `status is not None and status != "closed"`。

> **`permission_request` 恢复**：新方案下 stream 在整个执行期间存活，断连期间写入的事件（包括 `permission_request`）不会丢失。用户重连后 `consume_events` 从上次 cursor 继续 `XREAD`，会读到所有未消费的事件。只要 `PERMISSION_TIMEOUT`（300s）未到期，用户看到 `permission_request` 后仍可正常审批。不需要额外的 REST API 查询 pending interrupt。

---

### F-19 Compaction 跨实例互斥缺失

**来源**：Reviewer P1 + 自研 Review

**问题**：`compaction.py:49` 的 `_running: Dict[str, asyncio.Event]` 是纯进程内状态。`controller.py:107` 的 `is_running()` 和 `wait_if_running()` 也只看本地字典。双实例场景下：

1. **重复压缩**：A 实例触发 compaction → B 实例不感知 → B 实例同时触发 → 两个 compaction 读到相同的历史、各自生成摘要、各自写入 → 后写者覆盖先写者（last-writer-wins），或两条摘要同时存在
2. **上下文读取不一致**：B 实例新执行不会等待 A 的 compaction → 可能读到半写状态的摘要（A 正在写新摘要、删旧消息的过程中）

**涉及文件**：
- `src/core/compaction.py` — `_running` dict、`is_running()`、`wait_if_running()`、`maybe_trigger()`
- `src/core/controller.py` — `is_running()` 调用点

**修复建议**：

**设计思路**：不新增一套平行的 lock 机制，也不把 compaction 硬塞进现有 conversation lease API。而是**抽出底层 owner-key 原语**，conversation lease 和 compaction lock 都基于它实现。

当前 `RedisRuntimeStore` 的 Lua 脚本已经是通用的——`_LUA_COMPARE_AND_DEL`（检查 owner 再删除）、`_LUA_COMPARE_AND_EXPIRE`（检查 owner 再续期）、`_LUA_ACQUIRE_LEASE`（SET NX 或返回持有者）不含任何 conversation 语义，但被直接嵌在 conversation lease 方法里。应该把这层提出来：

```
┌───────────────────────┐  ┌──────────────┐
│ Conversation Lease     │  │ Compaction   │   ← 业务层
│ - 双 key 联动          │  │ Lock         │
│   (lease + interactive)│  │ - 单 key     │
│ - 心跳续租             │  │ - wait 语义  │
│ - 返回持有者 (409)     │  │              │
└────────┬──────────────┘  └──────┬───────┘
         │                        │
         ▼                        ▼
┌────────────────────────────────────────┐
│ Owner-Key Primitive (RuntimeStore)      │   ← 原语层
│ acquire(key, owner, ttl) → Optional     │
│ renew(key, owner, ttl) → bool           │
│ release(key, owner) → None              │
│ get_owner(key) → Optional[str]          │
└────────────────────────────────────────┘
         │
         ▼
   Redis: SET NX EX + Lua CAS（复用现有脚本）
   InMemory: dict
```

**1. RuntimeStore Protocol 新增原语层**

```python
# runtime_store.py — Protocol 新增
async def acquire(self, key: str, ttl: int, *, owner: Optional[str] = None) -> Tuple[Optional[str], str]:
    """原子获取 key。

    Args:
        key: 锁 key
        ttl: 过期时间（秒）
        owner: 调用方指定的 owner token（可选）。不传则内部自动生成 uuid4。
               适用场景：conversation lease 传 message_id（业务上需要通过 get_owner 查出 message_id），
               compaction 等纯互斥场景不传，由原语自动生成唯一 token。

    Returns:
        (None, token): 获取成功，token 为实际使用的 owner（传入的或自动生成的）
        (existing_owner, ""): 冲突，返回当前持有者
    """
    ...
async def renew(self, key: str, owner: str, ttl: int) -> bool:
    """CAS 续期。仅当 owner 匹配时 EXPIRE。"""
    ...
async def release(self, key: str, owner: str) -> None:
    """CAS 释放。仅当 owner 匹配时 DEL。"""
    ...
async def get_owner(self, key: str) -> Optional[str]:
    """查询当前持有者。"""
    ...
```

**Redis 实现**直接复用现有 Lua 脚本：
- `acquire` → `_LUA_ACQUIRE_LEASE`（已有，SET NX 或返回持有者）。`owner` 未传时内部 `uuid4().hex` 生成
- `renew` → `_LUA_COMPARE_AND_EXPIRE`（已有）
- `release` → `_LUA_COMPARE_AND_DEL`（已有）
- `get_owner` → `GET key`

**InMemory 实现**用一个通用 dict 替代当前各自独立的 dict。`owner` 未传时同样内部生成。

**2. 重构 conversation lease 为原语调用**

现有 conversation lease 方法保留签名不变（不破坏上层调用），内部改为调原语 + 联动 interactive key：

```python
# RedisRuntimeStore — conversation lease（重构后）
async def try_acquire_lease(self, conversation_id: str, message_id: str) -> Optional[str]:
    # 传 message_id 做 owner — get_owner 可直接返回正在执行的 message_id
    existing, _token = await self.acquire(self._lease_key(conversation_id), self._lease_ttl, owner=message_id)
    return existing  # None = acquired, str = existing holder

async def release_lease(self, conversation_id: str, message_id: str) -> None:
    await self.release(self._lease_key(conversation_id), message_id)

async def renew_lease(self, conversation_id: str, message_id: str, ttl: float) -> bool:
    lease_ok = await self.renew(self._lease_key(conversation_id), message_id, int(ttl))
    await self.renew(self._interactive_key(conversation_id), message_id, int(ttl))
    return lease_ok

async def get_leased_message_id(self, conversation_id: str) -> Optional[str]:
    return await self.get_owner(self._lease_key(conversation_id))
```

**3. CompactionManager 调用原语**

```python
class CompactionManager:
    LOCK_TTL = 30  # 秒，短 TTL + 心跳续租（与 execution lease 同模式）

    def __init__(self, db_manager, agents, *, runtime_store=None):
        self._db_manager = db_manager
        self._agents = agents
        self._store = runtime_store        # Optional[RuntimeStore]
        self._local_events: Dict[str, asyncio.Event] = {}  # 仅用于本地 await
        self._lock_tokens: Dict[str, str] = {}  # conv_id → owner token

    def _lock_key(self, conv_id: str) -> str:
        return f"compact:{conv_id}"

    async def _try_acquire(self, conv_id: str) -> bool:
        if self._store:
            # 不传 owner → 原语层自动生成 uuid4 token
            existing, token = await self._store.acquire(
                self._lock_key(conv_id), ttl=self.LOCK_TTL
            )
            if existing is None:  # 成功
                self._lock_tokens[conv_id] = token
                return True
            return False
        return conv_id not in self._local_events

    async def _release(self, conv_id: str) -> None:
        token = self._lock_tokens.pop(conv_id, None)
        if self._store and token:
            await self._store.release(self._lock_key(conv_id), token)

    async def _renew_loop(self, conv_id: str, task: asyncio.Task) -> None:
        """心跳续租循环（TTL/3 间隔）。丢锁时 cancel compaction task（fencing）。"""
        interval = self.LOCK_TTL // 3  # 10s
        while True:
            await asyncio.sleep(interval)
            token = self._lock_tokens.get(conv_id)
            if not token:
                return
            try:
                still_owner = await self._store.renew(
                    self._lock_key(conv_id), token, ttl=self.LOCK_TTL
                )
            except Exception:
                logger.warning(f"Compaction lock renewal failed for {conv_id} (transient)")
                continue
            if not still_owner:
                logger.error(f"Compaction lock lost for {conv_id} — fencing compaction")
                task.cancel()
                return

    async def is_running(self, conv_id: str) -> bool:
        if self._store:
            return await self._store.get_owner(self._lock_key(conv_id)) is not None
        return conv_id in self._local_events

    async def wait_if_running(self, conv_id: str, poll_interval: float = 2.0) -> bool:
        # 本地有 event 直接等
        local_event = self._local_events.get(conv_id)
        if local_event is not None:
            await local_event.wait()
            return True
        # 远端 compaction: 轮询 Redis 锁
        if self._store:
            if not await self.is_running(conv_id):
                return False
            while await self.is_running(conv_id):
                await asyncio.sleep(poll_interval)
            return True
        return False
```

`_run_compaction` 中启动/清理心跳（与 `execution_runner._wrapped` 同模式）：

```python
async def _run_compaction(self, conv_id: str, ...):
    heartbeat = None
    try:
        if self._store:
            # 获取当前 task，传给心跳用于 fencing cancel
            current_task = asyncio.current_task()
            heartbeat = asyncio.create_task(self._renew_loop(conv_id, current_task))
        await self._compact(conv_id, ...)
    except asyncio.CancelledError:
        logger.warning(f"Compaction for {conv_id} cancelled (lock fencing or shutdown)")
    finally:
        if heartbeat:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        await self._release(conv_id)
```

> **工作量**：原语层 ~40 行（Protocol + Redis + InMemory），conversation lease 重构 ~30 行（签名不变，内部改为调原语），CompactionManager ~60 行（含 token 管理 + 心跳循环），controller 调用点适配 ~10 行。总计 **~140 行**（含测试适配）。

**注意**：`is_running()` 和 `wait_if_running()` 需要从同步改为异步。`controller.py:107` 的调用点需要加 `await`：

```python
# controller.py
if self.compaction_manager and await self.compaction_manager.is_running(conversation_id):
```

> **为什么不用 Pub/Sub 通知完成**：compaction 完成频率低（每次执行最多触发一次），轮询 2s 间隔完全够用。Pub/Sub 增加复杂度但收益不大。

---

## P2 — 建议修复

### F-20 前端首连 SSE 失败不走重连路径

**来源**：Reviewer P2

**问题**：`sse.ts:45-47` 在 HTTP 非 2xx 或无 body 时直接调 `handlers.onError`。`useSSE.ts:464-466` 的 `onError` 回调执行 `setError` + `endStream`，**不触发 `attemptReconnect`**。而 `onClose`（`useSSE.ts:468-472`）才走重连逻辑。

线上场景：负载均衡 502、网关抖动、滚动发布期间的瞬时错误 → 前端视为终态 → 后端引擎实际还在跑 → 用户必须手动刷新。

**涉及文件**：
- `frontend/src/lib/sse.ts` — `connectSSE`
- `frontend/src/hooks/useSSE.ts` — `onError` 回调

**修复建议**：

在 `useSSE.ts` 的 `onError` 回调中区分可重试 vs 不可重试错误：

```typescript
// useSSE.ts — connect 函数内
onError: (err) => {
  // 不可重试：认证失效、资源不存在
  if (err.message.includes('401') || err.message.includes('404')) {
    setError(err.message);
    endStream();
    return;
  }
  // 可重试：502/503/网络错误 — 走与 onClose 相同的重连路径
  setReconnecting(true);
  attemptReconnect(conversationId, connection.lastEventId, controller);
},
```

同步在 `sse.ts` 中让错误信息携带 HTTP 状态码（当前只有 `SSE connection failed: ${res.status}` 字符串），或把状态码作为 Error 的属性暴露：

```typescript
// sse.ts
if (!res.ok || !res.body) {
  const err = new Error(`SSE connection failed: ${res.status}`);
  (err as any).status = res.status;
  handlers.onError?.(err);
  return;
}
```

---

### ~~F-21 心跳续约失败无重试容忍度~~ — 已撤回

**来源**：自研 Review → Reviewer 二轮反馈后撤回

**撤回原因**：`renew_lease` 返回 `False` 的语义是"你不是 owner"（CAS 检查失败或 key 不存在），此时另一个 worker 已经可以 `SET NX` 成功。如果在 `False` 后再让旧任务多跑 3 秒，就重新打开了 split-brain 窗口——这正是 F-01（PR3）修复的核心问题。

当前代码（`execution_runner.py:162-170`）的行为是正确的：
- **异常** → `continue`（网络抖动，下一个 TTL/3 间隔自然重试，还有 2 次机会）
- **`False`** → 立即 `task.cancel()`（fail-stop，不做妥协）

Redis Cluster failover 期间 lease key 丢失导致误杀的场景确实存在，但正确的应对是**接受误杀**（执行中断，用户重新发起），而不是放松 fencing 语义。误杀代价是一次执行中断；放松 fencing 的代价是双写导致数据损坏。

---

### F-22 引擎执行中 DB 瞬断无重试

**来源**：自研 Review

**问题**：TDSQL 主从切换窗口（约 10-20s）内，engine loop 中的 DB 操作（如 `format_conversation_history_async`、`flush_all`）会抛 `OperationalError` / `DisconnectionError`。当前无重试 → 执行直接崩溃。

`pool_pre_ping=True` 只在**获取连接时**检测断连，不保护已获取连接上的操作。`pool_recycle=300s` 是定期回收，不覆盖主从切换。

**涉及文件**：
- `src/core/controller.py` — `stream_execute` 中的 DB 操作
- `src/repositories/` — 各 Repository 方法

**修复建议**：

对明确的**读操作和幂等写操作**做函数级重试，不做通用 session context manager 包装。重试工具放在 `database.py`，与 multi-PX failover（`_failover_creator`）同文件——两者都是 DB 韧性机制，只是层级不同（前者是连接级 failover，后者是操作级重试）。

> **为什么不做 session-level context manager 重试**：Python 的 `@asynccontextmanager` 在 `yield` 后控制权交给调用方。调用方代码抛异常回来后，generator 不能再次 `yield` 一个新 session 重跑调用方代码块——`yield` 在 generator 里只能执行一次。必须用回调/闭包形式，把要重试的操作包成函数传入，由重试逻辑控制调用次数。

> **关键约束：重试必须拿到 fresh session**。当前 Repository 方法（如 `conversation_repo.py:398` 的 `get_conversation_path`）跑在调用方传入的 `AsyncSession` 上。如果底层连接已断，在同一个 session 上重试仍然会失败。因此重试不能加在 Repository 层（它不控制 session 生命周期），必须加在**能创建新 session 的层级**——即 controller / manager 层。

```python
# src/db/database.py — 与 _failover_creator 同文件
import asyncio
from sqlalchemy.exc import OperationalError, DisconnectionError

async def retry_on_db_transient(
    fn,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
):
    """函数级重试，仅对 DB 瞬断异常重试。

    fn 应是一个 async callable，每次调用时内部获取 fresh session。
    仅用于读操作或幂等写操作。
    """
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except (OperationalError, DisconnectionError) as e:
            if attempt == max_retries:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning(
                f"DB transient error (attempt {attempt + 1}/{max_retries}): {e}, "
                f"retrying in {delay}s"
            )
            await asyncio.sleep(delay)
```

**应用层级**——在 controller / manager 中包装，每次重试拿新 session：

```python
# controller.py 或 conversation_manager.py — 调用示例
async def _load_history_with_retry(self, conversation_id, ...):
    async def _attempt():
        # 每次重试都走 db_manager.session() 拿新连接
        async with self._db_manager.session() as session:
            repo = ConversationRepository(session)
            return await repo.get_conversation_path(conversation_id, ...)

    return await retry_on_db_transient(_attempt)
```

**应用范围**：

```python
# 适合加重试的操作（读 / 幂等写，能包装成 fresh-session-per-attempt）：
# - format_conversation_history_async（读）
# - get_conversation（读）
# - update_response_async（幂等覆盖写）

# 不适合加重试的操作：
# - flush_all — 已有独立错误处理（controller.py:229-235）
# - batch_create（事件持久化）— 已有 3 次重试逻辑（controller.py:338-386）
# - Repository 方法本身 — 不控制 session 生命周期，加装饰器无效
```

---

## 不纳入本轮修复的 Findings

以下问题在自研 review 中被识别，但优先级低于 P2 或属于运维侧配置，不阻塞本轮上线：

| 问题 | 原因 | 处置 |
|------|------|------|
| 无登录限流 | 可由前置 Nginx `limit_req` 覆盖，不必改应用层 | 运维侧配置 |
| CORS `["*"]` 过于宽松 | 上线前由 `.env` 配置收紧，代码已支持 | 部署 checklist |
| 缺少安全响应头（HSTS/CSP） | 前置 Nginx 统一注入更合理 | 运维侧配置 |
| 无 JWT 刷新/吊销 | 当前 `is_active` DB 查询已覆盖禁用场景，7 天过期可接受 | 后续迭代 |
| 日志非 JSON 格式 | 可由 Filebeat/Fluentd 做转换 | 后续迭代 |
| 无 Request ID 链路追踪 | 当前 `conv_id` + `message_id` context 已覆盖核心链路 | 后续迭代 |
| SIGTERM handler | Uvicorn 已内置 SIGTERM → graceful shutdown，30s 超时 | 验证确认 |
| Artifact flush 前崩溃丢数据 | 执行通常 < 30s 完成，崩溃窗口极小；`initial_state` fallback 保证对话不丢 | 后续迭代（中间 checkpoint） |
| 连接池指标暴露 | `/health/ready` 已覆盖基本检测 | 后续迭代（Prometheus） |

---

## 修复优先级总览

| 序号 | ID | 问题 | 等级 | 工作量 |
|------|-----|------|------|--------|
| 1 | F-17 | ~~Multi-PX failover asyncmy → aiomysql~~ | P0 | ✅ PR6 已合入 |
| 2 | F-18 | ~~Stream 生命周期与执行生命周期对齐（TTL 策略简化 + `STREAM_TTL` → `STREAM_CLEANUP_TTL`）~~ | P1 | ✅ PR7 已合入 |
| 3 | F-19 | ~~Compaction 分布式锁（抽 owner-key 原语 + conversation lease 重构 + 心跳续租）~~ | P1 | ✅ PR7 已合入 |
| 4 | F-20 | ~~前端首连 SSE 可重试~~ | P2 | ✅ PR8 已合入 |
| 5 | ~~F-21~~ | ~~心跳续约二次确认~~ | — | 已撤回（会重开 split-brain） |
| 6 | F-22 | ~~DB 瞬断函数级重试~~ | P2 | ✅ PR8 已合入 |

---

## 建议 PR 序列

| PR | 内容 | 性质 | 回归面 |
|----|------|------|--------|
| **PR6** ✅ | F-17 | 依赖修正 | 极小 — 只改 import 和注释，不改逻辑 |
| **PR7** ✅ | F-18 + F-19 | 分布式协调 | 中 — stream TTL 策略简化 + owner-key 原语抽取 + compaction 分布式锁，需回归 SSE 重连、lease 续租和 compaction 场景 |
| **PR8** ✅ | F-20 + F-22 | 容错加固 | 小 — 前端重试策略 + DB 读操作重试，各自独立 |

**PR 拆分理由**：
- **PR6 独立**：P0 阻塞项，改动极小，可立即合入解除 multi-PX 启动阻塞
- **PR7 合并 F-18 + F-19**：两者都是分布式协调层的修复，F-18 修改 `stream_transport` 的 TTL 语义，F-19 让 `compaction` 感知 `RuntimeStore`。改动有交叉（都涉及 `config.py` 和 runtime 依赖注入），合并避免冲突
- **PR8 合并 P2**：两个独立的容错改进，互不影响但都属于"韧性加固"类改动，合并减少 review 轮次

---

## 验证计划

| PR | 验证项 |
|----|--------|
| PR6 | 1. 干净 venv `pip install -r requirements.txt` 后 `import aiomysql` 成功<br>2. 配置 3 PX 地址启动，断一个 PX 后自动切换 |
| PR7 | 1. SSE 断连 > 60s 后重连仍能恢复事件流（producer 续期保活）<br>2. Permission interrupt 期间断连 < 30min，重连后 stream 仍在、可从 cursor 处继续消费<br>3. `active-stream` 在 stream 过期时返回 410 而非返回无效 URL<br>4. 双实例同时触发 compaction，只有一个实际执行<br>5. A 实例 compaction 进行中，B 实例新执行会等待完成 |
| PR8 | 1. 前端首连遇到 502 后自动重试（不需要手动刷新）<br>2. TDSQL 主从切换期间读操作（format_conversation_history 等）自动重试不中断 |
