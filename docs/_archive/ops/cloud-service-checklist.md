# 云托管服务确认清单

> 双机 active-active 部署前，需要和云托管方确认的问题。

---

## 一、部署与接入模式

> **已确认**：异地双活（北京 + 上海），每中心两台服务器各一个实例，不涉及跨中心流量。
> Redis：组 9（数据分析区），华为大数据平台 redis Cluster 集群，共用实例。
> TDSQL：YDB 资源组一（一般业务系统），集中式实例（1 主 2 从，选 3 DN），DCN 同步，配置 3 个 PX 地址接入（无 VIP）。

1. ~~**双活是同城还是异地？**~~ → ✅ 异地（北京 + 上海）

2. ~~**服务接入是否存在跨中心访问？**~~ → ✅ 不存在，各中心独立服务

3. ~~**跨中心的数据同步方式是什么？**~~ → ✅ 不影响。中心绑定路由，每个中心只写自己的数据，跨中心同步仅用于灾备。
   - TDSQL：实例内 DCN 同步（实例级切换），跨中心通过 DBBridge 异步复制
   - Redis：各中心独立，不跨中心同步

4. ~~**Redis 实例是否与其他系统共用？**~~ → ✅ 共用（组 8 数据分析区，华为大数据平台 redis Cluster）。云托管要求所有 key 加系统前缀，我们使用 `af:` 前缀。
   > **注意**：ArtifactFlow 使用 Redis 做协调（分布式锁、SSE Stream、Pub/Sub），不是缓存。Redis 不可用时降级策略为 health probe 返回 not ready，拒绝新请求，现有会话超时结束。

---

## 二、主从切换行为

> 主从切换分两种场景，时间窗口差异大，影响我们的超时和重试参数设计：
> - **Switchover（计划切换）**：运维主动触发（升级、维护），先同步数据再切换，通常亚秒~秒级
> - **Failover（故障切换）**：master 宕机，集群检测+选举 replica 提升。Redis Cluster 默认 `cluster-node-timeout=15s`（主观下线）+ 多数确认 + 投票选举，整个窗口约 **15-20+ 秒**；部分云托管数据库 switchover 也可能需要几十秒
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

2. ~~🌟 **TDSQL 的接入方式是 VIP 还是需要客户端连接多个 PX 地址？**~~ → ✅ 配置 3 个 PX 地址，无 VIP。客户端需自行实现多地址故障切换。

3. ~~🌟 **TDSQL 默认的事务隔离级别是什么？**~~ → ✅ REPEATABLE READ，符合预期

4. ~~**TDSQL 是否支持 `SELECT 1` 做连通性检查？**~~ → ✅ MySQL 兼容，支持

5. ~~**Redis 版本是多少？**~~ → ✅ 5.0+。Stream/Lua 可用；无 Sharded Pub/Sub（7+ 才有），使用普通 Pub/Sub 广播模式，对我们影响不大（仅用于 interrupt 通知，流量小）

6. 🌟 **Redis 是否有禁用或限制的命令？**
   特别关注：`SCRIPT LOAD`、`EVALSHA`（Lua 脚本原子操作依赖这两个）、`KEYS`（我们不用，仅确认）

7. **Redis 和 TDSQL 单中心的最大连接数和内存上限？**
   我们用连接池 + Redis Stream 缓冲事件数据，需要确认上限。
   参考值：连接池 maxTotal ≈ CPU 核数 × 2 + 冗余系数(3-5)，文档建议 50

---

## 四、宿主机深挖工具（事故复盘补，PR-forensics-bundle）

> **背景**：2026-05-14 事件循环卡死事故时，内网现场缺取证工具，临时排查走了一大圈。复盘后我们把取证能力**分三层**铺好,目前已**完全脱离云托管协调**前两层:
>
> | 层 | 工具 | 装哪 | 何时用 |
> |---|---|---|---|
> | **主路径** | `faulthandler` deadman dump | backend 进程自己(PR-obs-lite 已内置) | 硬 wedge → `docker logs backend` 看自动 dump 的 Python 栈 |
> | **备份路径** | `py-spy` | backend **镜像里**(Dockerfile builder stage)+ compose `cap_add: [SYS_PTRACE]` | deadman 失效 / 想看采样分布 → `docker exec backend py-spy ...` |
> | **深挖路径** | `gdb` / `strace` / `procps` (`ps`/`top`) | 宿主机 yum/apt 装 | syscall 序列 / coredump 分析 / 全机器视图 |
>
> 前两层零云托管依赖(镜像 + 容器级 cap 自洽);第三层依赖宿主机标准工具,**本段就是跟云托管方对齐这一层的预装/安装路径**。

1. 🌟 **宿主机镜像是否预装 `gdb`、`strace`、`procps`(`ps`/`top`)？**
   - 第三层深挖工具,事故复盘里实际用到的就是这套(参考 incident-2026-05-14 第 33-41 行)
   - 若未预装,确认我们能否在不联网情况下走云托管方提供的内部 yum/apt 源安装
   - 若云托管方也不允许装,**前两层仍然就绪**(faulthandler dump 在容器内自洽,py-spy 在容器里),只是第三层 syscall/coredump 级别的深挖能力缺失,事故时复杂场景排查会更费劲

2. **是否允许 `gcore <pid>` 抓 coredump？**(可选,真出现需要看进程内存才用)
   - 内部也是 ptrace —— 但目标是**宿主机层**对容器内 PID,不是容器内 attach 自家进程
   - 需要宿主机 `CAP_SYS_PTRACE` 或 `ptrace_scope=0`
   - **前两层不依赖** —— Python 栈已经能 dump,coredump 是 C 扩展异常 / 二进制层 bug 进一步深挖
   - coredump 文件可指定落到我们持久卷下 (`/app/data/`),不污染宿主机;dump 含进程地址空间,仅事故现场用,事后人工清理

> 事故现场 SOP 优先级:① `docker logs backend` 看 faulthandler dump → ② `docker exec backend py-spy dump --pid 1` 看采样栈 → ③ `tail data/observability/loop-lag.jsonl` 看软退化事件 + `GET /admin/runtime` 看在飞任务 → ④ 都不够时上宿主机 strace / gdb 深挖
> 部署侧 SOP 详见 `deployment-sop.md` → "取证就绪"小节;preflight 校验脚本: `deploy/scripts/preflight.sh`
