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

> **背景**：2026-05-14 事件循环卡死事故时，内网现场抓不到 `py-spy`，临时取证额外走了一大圈。复盘后我们把 `py-spy` 静态二进制 + `pandas`/`numpy` 离线 wheels 打进 release bundle（`scripts/release.sh --with-forensics`），目标机器零联网完成安装。**但宿主机层的 `gdb`/`strace`/`procps` 仍需云托管方在主机镜像里预装或允许我们装**。

1. 🌟 **宿主机镜像是否预装 `gdb`、`strace`、`procps`(`ps`/`top`) ？**
   - 这是排查"服务卡死但未崩溃"的最小工具集（参考 incident-2026-05-14 第 33-41 行）
   - 若未预装，确认我们能否在不联网情况下走云托管方提供的内部 yum/apt 源安装

2. 🌟 **是否允许我们把 `py-spy` 二进制装到 `/usr/local/bin/`？**
   - `py-spy` 是 attach-by-PID 的 Python profiler/sampler，通过 `ptrace` syscall 读目标进程栈
   - 不需要 setuid / root；权限由 Yama LSM (`ptrace_scope`) 控制，见下条

3. 🌟 **`ptrace_scope` 内核参数 (`/proc/sys/kernel/yama/ptrace_scope`) 当前值是？**

   Yama LSM 四种模式（参考：[kernel.org Yama](https://www.kernel.org/doc/html/latest/admin-guide/LSM/Yama.html)）：

   | mode | 语义 | py-spy 能否 attach docker 内 backend |
   |---|---|---|
   | 0 | classic ptrace（同 UID 即可） | ✅ 可直接 attach（最宽松） |
   | 1 | restricted（**默认**）：仅 descendant，或被 `prctl(PR_SET_PTRACER)` 显式放行，或 CAP_SYS_PTRACE | ⚠️ 默认值下**不能**直接 attach 已运行的容器进程（容器不是 host shell 的子进程） |
   | 2 | admin only（CAP_SYS_PTRACE） | ❌ 普通用户失败 |
   | 3 | 禁用 | ❌ 全部失败 |

   **对我们的影响**：mode 1 是 Linux 发行版默认（Ubuntu / RHEL / Debian），意味着部署账户**无法**直接 `py-spy dump --pid <backend container PID>`。三条变通路径，按优先序：

   a. **docker compose 加 `cap_add: [SYS_PTRACE]`**（推荐，最小改动）——给容器一个能在自身 namespace 内 ptrace 的能力。`py-spy dump --pid 1` 在容器内即可工作。事故时 `docker exec backend py-spy dump --pid 1`，无宿主机参与。

   b. **`ptrace_scope` 调到 0**（次选）——影响整机所有进程，攻击面比 a 大。仅在云托管不允许 a 时考虑。

   c. **由 backend 自己调 `prctl(PR_SET_PTRACER, PR_SET_PTRACER_ANY)`**（最后兜底）——需要应用代码改动，跨平台麻烦，不推荐。

   **请云托管方确认**：
   - 当前 `ptrace_scope` 值
   - 是否允许我们给 backend 容器加 `cap_add: [SYS_PTRACE]`（这是 docker capabilities 而非 host kernel cap，作用域仅容器内）
   - 若 b/c 都不允许，确认 a 是接受的

4. **是否允许 `gcore <pid>` 抓 coredump？**
   - `gcore` 内部也是 ptrace，跟第 3 条一样受 Yama 约束 —— 解法同上（最便利路径：`cap_add: [SYS_PTRACE]` 后 `docker exec backend gcore 1`）
   - coredump 文件落到我们持久卷下 (`/app/data/`)，不污染宿主机
   - dump 含进程地址空间，仅在事故现场用，事后人工清理

> 部署侧 SOP 详见 `deployment-sop.md` → "取证就绪"小节；preflight 校验脚本：`deploy/scripts/preflight.sh`
