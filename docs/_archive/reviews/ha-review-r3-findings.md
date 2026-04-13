# 高可用 Review 第三轮发现与修复建议

> PR6–PR8 全部合入后的三轮 review。
> 来源：外部 reviewer 反馈 + 自研评估，合并去重后统一排列。
> 评估基线：异地双活（北京 + 上海），每中心两台服务器各一个实例，不涉及跨中心流量。各中心共享云托管 TDSQL + Redis，Redis 不跨中心同步。
> Redis：组 9 数据分析区，华为大数据平台 redis Cluster，Redis 5.0+，`cluster-node-timeout` 默认 15s，failover 窗口约 15-20+ 秒。
> TDSQL：YDB 资源组一，集中式实例（1 主 2 从，选 3 DN），DCN 同步，配置 3 PX 地址接入（无 VIP），REPEATABLE READ。
>
> **目标边界**：同二轮——本轮修复范围限定在**单中心内**的双实例高可用。不考虑跨中心续跑。

---

## 二轮后的能力边界更新

| 能力 | 状态 | 变化 |
|------|------|------|
| 共享控制面（lease/interrupt/stream 跨 Worker） | ✅ | 无变化 |
| Fencing / split-brain 防护 | ✅ | 无变化（PR3） |
| 故障检测（readiness） | ✅ | 无变化（PR1） |
| Redis failover 恢复 | ✅ | 无变化（PR4） |
| 事件持久化保证 | ⚠️ 降级 | 无变化（PR5 清理了假 fallback） |
| Multi-PX TDSQL failover | ✅ | PR6 修复 asyncmy → aiomysql |
| Stream 生命周期与执行生命周期对齐 | ✅ | PR7 已修复 |
| Compaction 跨实例互斥 | ✅ | PR7 已修复 |
| 前端首连 SSE 失败重试 | ✅ | PR8 已修复 |
| DB 瞬断函数级重试 | ✅ | PR8 已修复 |
| **Lease 续租 fail-closed（对齐 compaction）** | ✅ | **F-23（PR9 已修复）** |
| **执行崩溃 reconciliation** | 延后 | **F-24（本轮评估，defer）** |
| **Producer 崩溃后 stream 快速收敛** | ✅ | **F-25（PR9 已修复）** |

---

## 测试覆盖情况

Reviewer 运行了定向测试：`110 passed in 20.01s`，覆盖 ExecutionRunner、RuntimeStore、chat/artifact API、artifact writeback 等关键路径。

Redis 集成测试因本地无 Redis 实例全部跳过（`26 skipped`）。同时发现 `test_redis_runtime_store.py:62-68` 的 fixture 仍使用已过期的 `stream_timeout` 参数（当前签名为 `execution_timeout`），说明该文件自 PR7 重命名后未被执行过。**多实例 Redis 路径目前没有可靠的可执行回归保护。**

---

## P1 — 应该修复

### F-23 ExecutionRunner `_renew_loop` 异常分支未对齐 CompactionManager 的 fail-closed 策略

**来源**：Reviewer 严重 → 自研评估降级为 P1

**问题**：`execution_runner.py:149-171` 的 `_renew_loop` 在续租异常时无限 `continue`：

```python
# execution_runner.py:162-164
except Exception:
    logger.warning(f"Heartbeat renewal failed for {task_id} (transient error)")
    continue  # ← 无限 continue，task 继续运行
```

而 PR7（F-19）实现的 `compaction.py:199-232` 的 `_renew_loop` 已采用连续失败计数 + cancel：

```python
# compaction.py:220-228
except Exception as e:
    consecutive_failures += 1
    if consecutive_failures > max_transient_failures:  # max=2
        compact_task.cancel()
        return
```

两处代码是同一模式（TTL/3 心跳续租 + fencing），但策略不一致。ExecutionRunner 是更早写的，PR7 加固 compaction 时没有同步更新。

**Reviewer 的攻击路径评估**：Redis 不可用超过 LEASE_TTL（90s）→ lease key 过期 → 旧 task 继续跑 → Redis 恢复 → 新实例 SET NX 成功 → 双执行窗口。

**实际影响**：这个窗口是**有界的**。Redis 恢复后，旧 task 的下一次 `renew_lease` 成功执行时会得到 `False`（CAS 检查：key 已被新 owner 持有，或 key 不存在），然后立即 `task.cancel()`。所以双执行窗口最长 = 一个心跳间隔（30s），而非 Reviewer 描述的"无限"。Fencing 机制本身是完好的（R2 F-21 已确认），问题仅在于异常分支的容忍度过高。

**严重程度**：降级为 P1。Fencing 在 `False` 分支工作正常，`exception` 分支只是缺少上限。加连续失败计数器可缩短双执行窗口（从"Redis 恢复后最多 30s"缩短为"连续 2 次失败即 cancel，不等 Redis 恢复"），但不改变 fencing 的基本保证。

**涉及文件**：
- `src/api/services/execution_runner.py` — `_renew_loop`

**修复建议**：

对齐 `compaction.py` 已采用的模式——增加连续失败计数器，`max_consecutive_failures=2`：

```python
# execution_runner.py — _renew_loop
async def _renew_loop(self, conversation_id: str, task_id: str) -> None:
    interval = self._lease_ttl // 3
    max_consecutive_failures = 2
    consecutive_failures = 0
    while True:
        await asyncio.sleep(interval)
        try:
            still_owner = await self.store.renew_lease(
                conversation_id, task_id, ttl=self._lease_ttl
            )
        except Exception:
            consecutive_failures += 1
            logger.warning(
                f"Heartbeat renewal failed for {task_id} "
                f"({consecutive_failures}/{max_consecutive_failures})"
            )
            if consecutive_failures >= max_consecutive_failures:
                logger.error(
                    f"Lease renewal failed {consecutive_failures} times for {task_id} "
                    f"— fencing execution (fail-closed)"
                )
                task = self._tasks.get(task_id)
                if task:
                    task.cancel()
                return
            continue

        consecutive_failures = 0  # 成功重置
        if not still_owner:
            logger.error(f"Lease lost for {task_id} — fencing execution")
            task = self._tasks.get(task_id)
            if task:
                task.cancel()
            return
```

---

### F-25 Producer 崩溃后 stream 假活，前端挂在 ping-only 连接上最长 30 分钟

**来源**：Reviewer 高

**问题**：`create_stream`（`redis_stream_transport.py:77-95`）创建的 stream meta TTL = `EXECUTION_TIMEOUT`（默认 1800s）。producer 崩溃后不会调 `close_stream()`。`consume_events`（`redis_stream_transport.py:132-214`）在无新事件时持续发 `__ping__`，consumer 看到连接"活着"但永远等不到 terminal event。

前端表现：显示"正在执行..."长达 30 分钟，实际 agent 早已死亡。

**加重因素**：
1. `chat.py:95-117` 的 `execute_and_push` 闭包在初始化异常分支只推 error event 到 stream，不主动 `close_stream()`。虽然 `run_and_push` 的 `finally`（`controller_factory.py:167`）会 close，但如果异常发生在 `create_controller` 之前，`run_and_push` 根本不会被调用。
2. `execution_runner.py:130-139` lease fencing cancel task 后，`_wrapped` 的 `except CancelledError` 分支走 `coro.close()`（generator close，非 await），`run_and_push` 的 `finally: close_stream()` 跑不到。

**涉及文件**：
- `src/api/services/redis_stream_transport.py` — `create_stream`、`consume_events`
- `src/api/services/execution_runner.py` — `_wrapped` finally
- `src/api/routers/chat.py` — `execute_and_push` 闭包

**修复建议**：

核心思路：**lease 是"producer 活着"的唯一权威信号。Consumer 侧检查 lease 存活性，`_wrapped` 确保所有退出路径都 close stream。**

两层修复覆盖两类场景：

**层 1：Task cancel / 异常退出（进程还活着）— `_wrapped` finally 补 close_stream**

`_wrapped` 的 finally 已有 `cleanup_execution`（释放 lease），在其后补上 `close_stream`。这样无论正常完成、cancel、异常，stream 都被 close。正常路径下 `run_and_push` finally 先 close 一次，这里再 close 一次——`close_stream` 是幂等的（已 closed 返回 False），无副作用。

```python
# execution_runner.py — _wrapped finally
finally:
    if heartbeat is not None:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
    coro.close()
    self._tasks.pop(task_id, None)
    await self.store.cleanup_execution(conversation_id, task_id)
    await stream_transport.close_stream(task_id)  # ← 新增：确保 stream 收敛
```

> `stream_transport` 已在 `_wrapped` 的闭包作用域中（`submit` 参数）。

同步补上 `execute_and_push` 初始化失败分支：

```python
# chat.py — execute_and_push
except Exception as e:
    logger.exception(f"Failed to initialize execution: {e}")
    await stream_transport.push_event(message_id, sanitize_error_event({...}))
    await stream_transport.close_stream(message_id)  # ← 补上
```

**层 2：进程崩溃（finally 都跑不到）— consumer 侧 lease 检查**

Lease 由 `_renew_loop` 每 30s 续租。进程崩了 → 续租停止 → lease 90s 后过期。Consumer 在 XREAD 超时时检查 lease 是否还在，不在就返回 error 断开。

实现：`create_stream` 时在 stream meta 中存入 lease 的完整 Redis key（opaque string），consumer 直接 `GET` 这个 key 判断存活。Transport 不需要知道 lease 语义。

```python
# redis_stream_transport.py — create_stream 新增 lease_check_key 参数
async def create_stream(self, stream_id, owner_user_id=None, lease_check_key=None):
    await self._redis.hset(meta_key, mapping={
        "owner": owner_user_id or "",
        "status": "pending",
        "lease_check_key": lease_check_key or "",  # ← opaque key
    })
```

```python
# redis_stream_transport.py — consume_events，XREAD 超时分支
if not result:
    # 检查 producer lease 是否还在
    lease_key = await self._redis.hget(meta_key, "lease_check_key")
    if lease_key:
        lease_val = await self._redis.get(lease_key)
        if lease_val is None:
            # lease 已过期/释放 → producer 已死
            yield {
                "type": "error",
                "data": {
                    "success": False,
                    "error": "Execution ended (lease expired)",
                },
            }
            return
    yield {"type": "__ping__"}
    continue
```

调用方传入 lease key：

```python
# execution_runner.py — submit 中 create_stream 调用
await stream_transport.create_stream(
    task_id,
    owner_user_id=user_id,
    lease_check_key=self.store._lease_key(conversation_id) if hasattr(self.store, '_lease_key') else None,
)
```

> **收敛时间**：进程崩溃后，lease TTL 过期（≤90s）+ consumer 下一次 XREAD 超时（≤15s）= 最长 ~105s。相比原来的 30 分钟，大幅改善。
>
> **不存在误判风险**：工具卡 3 分钟、permission interrupt 等待 5 分钟等场景下，lease 心跳仍在续租，key 一直存在，consumer 不会误断。Lease 是唯一的 source of truth。

---

## 已评估但不纳入的 Findings

### F-24 执行崩溃后无 reconciliation，DB 留下半成品消息 — 延后

**来源**：Reviewer 严重 → 自研评估 defer

**问题**：实例崩溃时，DB 中留下 `response=NULL` 的消息。Engine 状态纯进程内存，无法跨实例恢复。

**方案**：startup reconciliation——启动时扫描 `response IS NULL AND created_at < now() - EXECUTION_TIMEOUT` 的消息，标记为失败。

**defer 原因**：`response=NULL` 不阻塞后续操作（lease 已过期，用户可以正常发新消息）。前端连着 SSE 的场景由 F-25（heartbeat 超时）收敛。剩下的只是"用户几天后回来看到一条空白消息"的体验问题，收益不足以支撑增加 startup 扫描逻辑。后续如有需要可随时捡回来。

---

### Reviewer Finding 4：Artifact flush 盲吞 IntegrityError

**来源**：Reviewer 高

**Reviewer 原文**：`artifact_ops.py:770` 把 `DuplicateError/IntegrityError` 一律视为成功，没有像 `MessageEvent` 那样回查业务幂等键。一旦出现真实的版本冲突或并发写碰撞，会静默丢失数据。

**评估结论**：不成立，不纳入。

**分析**：`flush_all` 的调用时机受 conversation lease 保护。完整调用链：

```
send_message
  → runner.submit(lease acquire)
    → execute_and_push
      → ctrl.stream_execute
        → post-processing: flush_all(session_id)   ← lease 仍持有
      → _wrapped finally: cleanup_execution(release_lease)
```

`session_id = conversation_id`。Lease 保证同一 conversation 同一时刻只有一个执行在跑。因此同一 session 的 artifact 不可能有两个并发 writer。

能触发 `IntegrityError` 的唯一场景是 `with_retry` 的重试幂等：第一次 attempt 实际已 commit 但连接断开报 `OperationalError`，fresh session 重跑写入相同 `(artifact_id, session_id, version)` → `uq_artifact_version` 冲突。这正是应该吞掉的。

Reviewer 提到的"双执行 split-brain"场景是 F-23（lease fail-open）的后果。修复 F-23 后，这个前提不存在。在 lease 正确工作的前提下，当前的 catch 逻辑是安全且正确的。

### 其他运维侧事项

| 问题 | 处置 |
|------|------|
| 无 Rate Limiting | 前置 Nginx `limit_req` 覆盖，不必改应用层 |
| 日志非 JSON / 无 trace ID | 当前 `conv_id` + `message_id` context 已覆盖核心链路，后续迭代 |
| 无 Prometheus metrics | `/health/ready` 已覆盖基本检测，后续迭代 |
| Semaphore 进程本地 | 接受 per-instance 语义，`MAX_CONCURRENT_TASKS` 设为期望总量 / 实例数 |
| Redis 集成测试 fixture 过期参数 | ✅ PR9 已修复（`stream_timeout` → `execution_timeout`） |

---

## 修复优先级总览

| 序号 | ID | 问题 | 等级 | 工作量 |
|------|-----|------|------|--------|
| 1 | F-23 | `_renew_loop` 异常分支未对齐 compaction 的 fail-closed 策略 | ✅ 已修复 | PR9 |
| 2 | F-25 | Producer 崩溃后 stream 假活 30 分钟 | ✅ 已修复 | PR9 |
| — | F-24 | 执行崩溃无 reconciliation → DB 半成品消息 | 延后 | `response=NULL` 不阻塞后续操作，体验问题 |
| — | (F-R4) | Artifact flush 盲吞 IntegrityError | 不纳入 | Lease 保护下行为正确，见上方分析 |

---

## 建议 PR 序列

| PR | 内容 | 性质 | 回归面 |
|----|------|------|--------|
| **PR9** ✅ | F-23 + F-25 + Redis 测试 fixture 修复 | 崩溃收敛 + 策略对齐 | 已完成 |

**合并理由**：两项都是 P1，构成"实例崩溃容错"主题。F-23 对齐 fail-closed 策略、F-25 让 stream 跟随 lease 生命周期收敛。改动互不冲突，合并减少 review 轮次。

---

## 验证计划

| PR | 验证项 |
|----|--------|
| PR9 | **F-23**<br>1. `_renew_loop` 连续 2 次异常后 task 被 cancel（单元测试 mock Redis 抛异常）<br>2. 单次异常后恢复正常续租不影响执行（瞬断容忍）<br>3. `renew_lease` 返回 False 仍立即 cancel（原有行为不变）<br>4. Redis 集成测试 `test_redis_runtime_store.py` 能正常跑通（fixture 参数修复）<br><br>**F-25 层 1（task cancel/异常）**<br>5. Lease fencing cancel task 后 stream 被 close（consumer 收到 error terminal）<br>6. 初始化失败（`execute_and_push` 异常）后 stream 被 close<br>7. 正常完成路径 close_stream 幂等，不报错<br><br>**F-25 层 2（进程崩溃）**<br>8. Producer 进程崩溃后 consumer 在 ≤105s（lease TTL + ping interval）内检测到并断开<br>9. 工具长时间卡住（3 分钟）期间 lease 仍在续租，consumer 不误断<br>10. Permission interrupt 等待期间 lease 仍在续租，consumer 不误断 |
