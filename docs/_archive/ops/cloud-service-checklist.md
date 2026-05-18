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

## 四、宿主机取证工具（事故复盘补，PR-forensics-bundle）

> **背景**：2026-05-14 事件循环卡死事故时，内网现场抓不到 `py-spy`，临时取证额外走了一大圈。复盘后:
> - **主路径**: PR-obs-lite 已集成 `faulthandler` deadman switch —— 硬 wedge 时**自动 dump Python 栈**到 stderr / `docker logs backend`(C 线程,不获取 GIL,C 扩展持 GIL 也能出栈)。事故现场首查 `docker logs backend` 即可定位卡在哪
> - **备份路径**: `py-spy` 静态二进制 + `pandas`/`numpy` 离线 wheels 打进 release bundle (`scripts/release.sh --with-forensics`),目标机器零联网完成安装。**py-spy 是 deadman 失效时的备份**,不是首选;pandas/numpy 是 analyst 离线分析工具(`scripts/observability_report.py`)
>
> 本段问题是**为备份路径就绪**做的最小对齐 —— 主路径(faulthandler)不依赖任何宿主机权限,理论上已自洽。

1. 🌟 **宿主机镜像是否预装 `gdb`、`strace`、`procps`(`ps`/`top`) ？**
   - 这是排查"服务卡死但未崩溃"的深挖工具集(参考 incident-2026-05-14 第 33-41 行)
   - **主路径不依赖** —— `faulthandler` dump 已经能告诉我们 Python 栈在哪
   - 备份/深挖时需要(看 syscall 序列、容器外资源全景)
   - 若未预装,确认我们能否在不联网情况下走云托管方提供的内部 yum/apt 源安装

2. **是否允许我们把 `py-spy` 二进制装到 `/usr/local/bin/`？**
   - `py-spy` 是 attach-by-PID 的 Python profiler/sampler,通过 `ptrace` syscall 读目标进程栈
   - **仅在 deadman 失效时使用** —— 主路径已经能 dump Python 栈;py-spy 用于"deadman 自己也失败 / 想看采样分布而非单帧栈"的低频场景
   - 不需要 setuid / root;权限由 Yama LSM (`ptrace_scope`) 控制,见下条

3. **`ptrace_scope` 内核参数 (`/proc/sys/kernel/yama/ptrace_scope`) 当前值是？**

   Yama LSM 四种模式(参考: [kernel.org Yama](https://www.kernel.org/doc/html/latest/admin-guide/LSM/Yama.html)):

   | mode | 语义 | 宿主机 py-spy 能否 attach 容器内 backend |
   |---|---|---|
   | 0 | classic ptrace(同 UID 即可) | ✅ 可直接 attach |
   | 1 | restricted(**Linux 默认**): 仅 descendant 或带 CAP_SYS_PTRACE | ⚠️ 不能直接 attach(容器不是 host shell 的子进程) |
   | 2 | admin only(CAP_SYS_PTRACE) | ❌ 普通账户失败 |
   | 3 | 禁用 | ❌ 全部失败 |

   **对备份路径的影响**: mode 1 是 Linux 发行版默认。`py-spy` 在宿主机 attach 容器内进程跨 PID namespace,**默认值下不可行**。两条变通:

   a. **宿主机 `ptrace_scope` 调到 0**(影响整机,需云托管方批准 + 评估安全暴露面)
   b. **以 host root 跑 py-spy / 部署账户加 CAP_SYS_PTRACE**(范围更小,但仍需云托管方授权)

   > 注: 还有一条路径是 "docker compose 加 `cap_add: [SYS_PTRACE]` + 把 py-spy 装进 backend 镜像",但当前 bundle 设计**不打 py-spy 进镜像、不动 compose**(避免镜像膨胀和事故反射性扩张)。若未来 deadman 失效频繁发生再启动该决策。

   **请云托管方确认**:
   - 当前 `ptrace_scope` 值
   - 若 a / b 都不允许,我们接受 "备份路径不可用,完全依赖 faulthandler dump 主路径" —— 不阻塞部署

4. **是否允许 `gcore <pid>` 抓 coredump？**(可选,深挖路径)
   - `gcore` 内部也是 ptrace,跟第 3 条一样受 Yama 约束
   - **主路径不依赖** —— faulthandler dump 已经覆盖 Python 栈;coredump 是 C 扩展异常 / 二进制层 bug 的进一步深挖工具
   - 若云托管允许,coredump 文件落到我们持久卷下 (`/app/data/`),不污染宿主机
   - dump 含进程地址空间,仅在事故现场用,事后人工清理

> 事故现场 SOP 优先级:① `docker logs backend` 找 faulthandler dump → ② `tail data/observability/loop-lag.jsonl` 看软退化事件 → ③ `GET /admin/runtime` 看在飞任务 → 都不行时上备份路径
> 部署侧 SOP 详见 `deployment-sop.md` → "取证就绪"小节;preflight 校验脚本: `deploy/scripts/preflight.sh`
