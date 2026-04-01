# 云托管服务确认清单

> 双机 active-active 部署前，需要和云托管方确认的问题。

---

## 一、部署与接入模式

> **已确认**：异地双活（北京 + 上海），每中心两台服务器各一个实例，不涉及跨中心流量。

1. ~~**双活是同城还是异地？**~~ → ✅ 异地（北京 + 上海）

2. ~~**服务接入是否存在跨中心访问？**~~ → ✅ 不存在，各中心独立服务

3. ~~**跨中心的数据同步方式是什么？**~~ → ✅ 不影响。中心绑定路由，每个中心只写自己的数据，跨中心同步仅用于灾备。
   - TDSQL：DBBridge 异步复制（已确认架构含 DG 节点）
   - Redis：各中心独立，不跨中心同步

4. ~~**Redis 实例是否与其他系统共用？**~~ → ✅ 共用（华为云 DCS 公共区 Cluster 集群）。云托管要求所有 key 加系统前缀，我们使用 `af:` 前缀。
   > **注意**：ArtifactFlow 使用 Redis 做协调（分布式锁、SSE Stream、Pub/Sub），不是缓存。Redis 不可用时降级策略为 health probe 返回 not ready，拒绝新请求，现有会话超时结束。

---

## 二、主从切换行为

> 主从切换分两种场景，时间窗口差异大，影响我们的超时和重试参数设计：
> - **Switchover（计划切换）**：运维主动触发（升级、维护），先同步数据再切换，通常亚秒~秒级
> - **Failover（故障切换）**：master 宕机，集群检测+选举 replica 提升，通常数秒；部分云托管数据库 switchover 也可能需要几十秒
>
> 我们的设计已覆盖两种场景（XREAD 重试 + Pub/Sub 断连降级），以下确认用于参数调优，不阻塞实施。

1. **TDSQL 和 Redis 的 switchover / failover 时间窗口分别是多少？**
   影响 lease TTL、心跳间隔、重试等待时间的具体数值。如果 switchover 需要几十秒，lease TTL 需要相应放宽

2. **Redis 主从切换时，已有的 Pub/Sub 连接和 XREAD 长连接会怎样？**
   是自动重连还是直接断开？我们业务层已有降级方案（F-06 XREAD 重试、F-09 Pub/Sub catch-and-deny），确认后用于调优重试参数

3. ~~**Redis 主从切换后，Lua script cache 是否会清空？**~~ → ✅ 不需要确认。使用 `register_script` 自动处理 NOSCRIPT → re-load → retry，无论是否清空都无影响。

---

## 三、兼容性与资源限制

1. ~~**TDSQL 兼容的 PostgreSQL / MySQL 版本是多少？**~~ → ✅ 基本确认 MySQL 兼容（应用通过 JDBC 连接 PX 节点）。Python 端使用 MySQL 方言（asyncmy + SQLAlchemy）。

2. **TDSQL 的接入方式是 VIP 还是需要客户端连接多个 PX 地址？**
   已知 3 PX 节点，需确认连接方式

3. **TDSQL 默认的事务隔离级别是什么？**
   我们的并发写入行为依赖隔离级别（预期 REPEATABLE READ）

4. **TDSQL 是否支持 `SELECT 1` 做连通性检查？**
   健康检查端点需要用

5. **Redis 版本是多少？**
   影响 Cluster 模式下 Sharded Pub/Sub 支持（Redis 7+）和其他特性

6. **Redis 是否有禁用或限制的命令？**
   特别关注：`SCRIPT LOAD`、`EVALSHA`（Lua 脚本原子操作依赖这两个）、`KEYS`（我们不用，仅确认）

7. **Redis 和 TDSQL 单中心的最大连接数和内存上限？**
   我们用连接池 + Redis Stream 缓冲事件数据，需要确认上限
