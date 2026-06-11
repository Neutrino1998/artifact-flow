# 沙盒执行

> Per-turn、默认全禁网的 Linux 容器，让 agent 安全执行不可信的模型生成代码、处理用户上传的多格式文件，产物显式回写成 artifact。三个模型面工具（`bash` / `mount` / `persist`）共享一个 per-turn `SandboxSession`。

## 定位

沙盒解决两类工作：跑模型生成的任意 shell / Python，和处理富格式上传（docx / pdf / xlsx / 图片）——这些字节在 artifact 系统里是无文本表示的 blob，只有进沙盒才能被检视 / 转换。镜像预装 Python 3.11 + 科学栈（numpy/pandas/matplotlib/openpyxl）+ pandoc + ripgrep。

**沙盒是显式 stage 进出的 scratch 工作区，不是 artifact store 的自动镜像。** mount-in 与回写都显式：模型显式把指定 artifact 物化进工作区、显式调 `persist` 回写，不自动物化整 session、也不 diff 整个目录。容器 fs 不是「artifact 的第三态」，而是临时工作区——copy-in → 容器内随便改 → 显式 `persist`，persist 落回来就**变成一次普通 artifact 写**（进 `ArtifactWorkingSet`，随 turn 末 `flush_all` 落盘，与 `update_artifact` 同路）。工作区对 artifact store 没有同步义务，故没有三态一致性问题（对比 Claude Code：磁盘工作副本 vs git 记录，`commit` 是显式桥）。

## 三个工具，一个 session

工具面是三个语义不同的动词、参数形状各异，分立比合成单一 `sandbox(action=...)` 参数面更小、对小模型更可读，也与现有 `*_artifact` 工具 idiom 及 per-verb 权限粒度一致。「共用启动 / 沙盒交互」是实现层事实，不构成合并工具面的理由。

| 工具 | 权限 | 参数 | 职责 |
|------|------|------|------|
| `bash` | **CONFIRM** | `command` | 在本轮容器内 `bash -c` 执行。跑不可信代码 → CONFIRM。 |
| `mount` | AUTO | `artifact_id` | 把一个 artifact 物化进工作区 `<workspace>/<artifact_id>`（显式 stage-in）。 |
| `persist` | AUTO | `path` | 把工作区某文件回写成**新** artifact（显式 stage-out）。 |

底下是一个 per-turn `SandboxSession`（`src/tools/builtin/sandbox_session.py`），owns 容器生命周期 + bind-mount 工作区 + watchdog 注册 + 绑定本 turn 的 `ArtifactWorkingSet`。三个工具都是其上的薄操作。`create_sandbox_tools`（`src/tools/builtin/sandbox_ops.py`）由 `controller_factory` 按请求调用，与 `create_artifact_tools` 同 idiom。

工具只挂在拥有它们的 agent 的 `tools` 白名单（当前 `lead_agent` / `research_agent`）。bash = CONFIRM 在 agent MD 里显式声明——这是沙盒对模型的唯一暴露面，不在白名单的 agent 看不到也调不动这三个工具。

### lazy 创建

容器 **lazy 于首个沙盒工具调用**，不是「首个 bash」——模型可能先 `mount` 再 `bash`，`mount` 也会 `ensure_container()`。无沙盒的 turn：`SandboxSession` 只是个零成本对象壳，从不起容器。

### 错误语义

- `bash`：命令退出码非零**不算工具失败**（`grep` 无命中 exit 1 是信息不是故障）——`success=True` + 输出里带 exit code，模型自己解读。`success=False` 只留给基建故障（容器起不来 / exec 通道卡死）。
- `persist`：sticky 失败优先于「nothing to persist」——超额杀 / 容器中途死后容器已置空，先报 sticky（本 turn 沙盒不可用），不开「抢救残留产物」通道（超额现场文件完整性不可信）。

## 生命周期与拆除

```
controller_factory（每 turn）
  └─ SandboxSession(conv, msg)            # 对象壳，零成本
  └─ runner.register_cleanup(msg, session.close)   # 拆除句柄进 _wrapped finally
        ↓ 首个沙盒工具调用
  └─ ensure_container()                   # lazy：create + start，起 watchdog
        ↓ turn 结束（任意退出路径）
  └─ session.close()                      # 停 watchdog + 删容器 + 清 scratch
```

**拆除挂执行器 `_wrapped` 的真 `finally`（`cleanup_execution` 隔壁），与 lease 释放同一层。** bash 本身 IO 等待、天然可取消，**但容器在协程被取消后仍在烧 CPU**——成功 / 超时 / 协作取消 / 外部取消 / 崩溃五条退出都在解栈时执行拆除，容器与 lease 都是绑 turn 的易失态。**绝不放 post-processing**——那是被设计成可被 late-cancel 抢占的区域，兜不住会烧 CPU 的活资源；漏拆 = 孤儿容器。`close()` 幂等、不依赖 DB session，故晚于 controller context manager 退出也安全。cleanup 回调有 30s 有界弃等：aiodocker 的 delete 无默认超时，daemon 卡死曾会扣死 lease / stream。

进程**自身崩溃**（SIGKILL / OOM，`finally` 根本不执行）留下的孤儿，由 [reaper](#孤儿回收-reaper) 兜底。

## 隔离边界

沙盒跑不可信代码，三条**正交**边界各自封死，缺一不可：

| 边界 | 威胁 | 封法 |
|------|------|------|
| 逃逸隔离 | 容器内代码突破到宿主内核 | **gVisor（`runsc`）** 用户态内核拦系统调用。prod `SANDBOX_RUNTIME=runsc`，本机 dev 留空 = daemon 默认 `runc`。 |
| DooD socket | backend 通过 docker.sock 拥有 host root | **容器创建参数绝不被模型内容污染**——镜像 / 命令 / 挂载全是 backend 常量，模型只能影响容器**内**执行的字节。 |
| 网络封闭 | 不可信代码出网做外泄 / 横移 | **`--network=none` 默认全禁网**。 |

第三条尤其要紧：网络封闭**不能降级成靠 CONFIRM 对命令授权来控**。授权是 *consent*（人同意了某条命令的意图），网络是 *confinement*（不管谁同意，容器代码够得着什么）。开网后被授权的 `pip install` = 任意代码执行，且同容器里没被授权的代码（传递 import / 被污染 wheel / 生成代码任意一行）也拿到了网。内网 web 工具已禁、沙盒无任何合法公网需求，故 `--network=none` 是零成本纯收益。

**依赖因此全离线投递、绝不靠出网**，分三层（同一套 `pip --no-index --find-links` 机制、不同生命周期）：① 烤进镜像（python / 科学栈 / pandoc / ripgrep，环境定义级）；② 离线 wheel bundle 挂固定位（常驻 extras）；③ skill 自带 asset（场景 specific 长尾，随 skill 激活按需挂）。依赖 ≠ artifact——artifact 是用户拥有的数据（走 mount / persist），依赖是执行环境（走镜像 / bundle）。

其余容器硬约束：`ReadonlyRootfs`（rootfs 只读）、非 root（uid 1000）、`SANDBOX_MEM_LIMIT_MB` 内存上限（MemorySwap 设同值 = 禁 swap）、`SANDBOX_CPU_LIMIT` CPU 核数、`SANDBOX_PIDS_LIMIT` fork 炸弹闸。

## Staging：宿主直读直写

`mount` 写、`persist` 读，**都走宿主侧直接读写 scratch 目录**（`session.workspace_dir`），不走 `docker cp` / `exec`。这是为保住该机制（tmpfs 方案会逼 staging 改 exec+tar 流）。

读写两侧都做 **realpath 圈地 + `O_NOFOLLOW`**（`src/tools/builtin/sandbox_fs.py`）：容器内代码（含 bash 留下的后台进程）能在工作区造任意 symlink，宿主侧跟链会读 / 写池外文件。所有工作区文件访问一律走 `sandbox_fs` 的逐级 `openat` 原语，**业务代码不得再手写 `os.walk` / `os.open` / `os.path` 访问工作区**。`mount` 写后 `fchmod(fd, 0o666)`——绕过 backend umask，否则 umask 077 下落 0600、容器 uid 1000 读不了。

- `mount` 的字节来源二分：blob artifact 取原始字节（本轮 staged 上传经 `get_blob`，其余走 DB）；文本 artifact 取 WorkingSet overlay 的**当前内容**（本轮 dirty / new 必须可 mount，直读 DB 是空的）按 UTF-8 写盘。on-disk 名 = artifact id（已是 fs-safe 句柄）。
- `persist` 永远产新 artifact（同名 `_N` dedup）。文本 / 二进制二分：可严格 UTF-8 解码且 ≤ `SANDBOX_PERSIST_MAX_TEXT_BYTES` → 文本 artifact（可编辑、版本化）；否则 blob（不可变单版、可下载）。

## 磁盘配额：三层

bind mount 无界之外，容器 rootfs overlay upper 同样无界（容器内 `/tmp` 写的是宿主 `/var/lib/docker`）。三层堵：

1. **loop 池子（硬墙）**——部署时 `fallocate + mkfs.ext4 + mount -o loop` 一个定容文件系统作 `SANDBOX_SCRATCH_ROOT`。硬墙落在正确的爆炸半径边界：race 窗口写穿也只是池子满、宿主无恙；独立 inode 表顺带兜住百万小文件轴。host-prep 几行进部署脚本 / fstab，dev mac 不做、风险接受。
2. **per-turn watchdog（软配额）**——容器活着期间，worker 每 `SANDBOX_WATCHDOG_INTERVAL_SEC` 对本 turn scratch 做 `du`（块占用，`to_thread`），超 `SANDBOX_WORKSPACE_QUOTA_MB` → 杀容器 + sticky 失败。管 turn 间公平，池子兜住其 race 缺陷后够用。
3. **ReadonlyRootfs + 容器 `/tmp` bind 到该 turn scratch 子目录**——堵 rootfs upper 洞，所有可写路径落池子，零内存开销。

另对池子 `statvfs` 做起容器**准入水位**（`SANDBOX_POOL_MIN_FREE_MB`，O(1)）：剩余空间低于阈值时拒绝新沙盒，已在跑的 turn 不受影响（软配额归 watchdog）。

> 否决了 tmpfs 方案（内存账难看：额度 × 并发预留 RAM、冷文件全程占内存）。

## 命令超时与输出溢出

- **每条命令超时** = 容器内 `timeout --signal=KILL` 包 argv（exec argv 数组无引号问题），`SANDBOX_COMMAND_TIMEOUT` 秒。超时强杀 → exit 137，按时长归因为超时（与 OOM-kill 的同码区分）。
- **输出溢出两层**：session 侧 `SANDBOX_MAX_OUTPUT_CHARS` 硬帽（超出继续 drain 但丢弃，防内存放大，带显式截断标记）；剩下 > `max_result_size_chars`（50k）的部分由引擎的[超长工具结果落盘](tools.md#超长工具结果自动落盘) idiom 接手，引擎零改动。

## 孤儿回收（reaper）

进程死亡时 `finally` / `close()` 不执行（SIGKILL / OOM）会留下孤儿容器 + scratch 目录。`SandboxReaper`（`src/api/services/sandbox_reaper.py`）是这条路径的二级兜底，在 FastAPI lifespan 起停（仿 observability bootstrap）。

**lease 是唯一的 liveness 真相源**，但从 lease 永远发现不了「无 lease 的孤儿」，所以枚举必须**资源侧**、lease 作减法掩码：

```
孤儿 = （daemon 命名空间 label 容器 ∪ scratch 根直属目录） − list_active_executions() 活跃集
```

两条纪律：

- **对账粒度 per-turn**：活跃判定是 `active.get(conv) == msg`，不是「该 conv 有没有任何活跃 turn」——否则同会话新 turn 持的活 lease 会永久屏蔽前一 turn 漏下的孤儿。
- **scratch 根枚举走 `sandbox_fs.list_dir`**（fd 钉住单层、不递归）：活容器能把子目录换成池外 symlink（目录 TOCTOU）。

零误杀靠：lease（真相源）+ **grace**（`SANDBOX_REAP_GRACE_SEC`，躲「刚建、lease 还没可见」的差一拍；资源恒在 lease 之后创建，故 Redis 下这个竞争实际不存在）+ namespace 防御复核。

**worker-id 归属**：每进程 import 时生成 `WORKER_ID`，盖进每个容器的 label + scratch 目录名第三段。停机时 `final_sweep` 只对 `worker == WORKER_ID` 的资源**绕 grace** 立即回收（我的 turn 全停了 → 我的资源必是孤儿），与副本数无关；别的 worker / legacy 无 worker 目录仍走普通周期 grace，绝不被 no-grace 误删。

**部署边界**：reaper 默认只在共享 `RuntimeStore`（Redis）下启动。InMemory store 是进程内状态、单 worker 契约——多 worker 下 reaper 会误杀兄弟进程的活沙盒（InMemory 把「找不到 lease」的退化升级成了「删别人的活容器」的破坏）。InMemory 下默认不起 reaper，须经 `SANDBOX_REAP_ALLOW_LOCAL_STORE` 显式 opt-in（并自担单 worker 之责）。无沙盒部署（无 docker / 不授 bash）可 `SANDBOX_REAP_ENABLED=False` 免空轮询刷日志。

## 配置常量

全部 `src/config.py`、`ARTIFACTFLOW_` 前缀环境变量可覆盖。模型不可调（克制工具参数面）。

| 常量 | 默认 | 含义 |
|------|------|------|
| `SANDBOX_IMAGE` | `artifactflow-sandbox:latest` | 沙盒镜像（与 backend 镜像 / requirements.lock 解耦，自己的 runtime） |
| `SANDBOX_RUNTIME` | `""` | Docker runtime；`""` = daemon 默认（dev=runc），prod=`runsc` |
| `SANDBOX_SCRATCH_ROOT` | `/tmp/artifactflow-sandbox` | scratch 根；prod 挂成定容 loop 文件系统 |
| `SANDBOX_COMMAND_TIMEOUT` | `120` | 单条命令秒上限（容器内 `timeout --signal=KILL`） |
| `SANDBOX_START_TIMEOUT` | `60` | 容器 create+start 秒上限（daemon 卡死 loud-fail，不 wedge 整 turn） |
| `SANDBOX_MEM_LIMIT_MB` | `1024` | 容器内存上限（MemorySwap 同值 = 禁 swap） |
| `SANDBOX_CPU_LIMIT` | `1.0` | CPU 核数上限 |
| `SANDBOX_PIDS_LIMIT` | `256` | fork 炸弹闸 |
| `SANDBOX_MAX_OUTPUT_CHARS` | `200_000` | 单命令输出捕获硬帽 |
| `SANDBOX_WORKSPACE_QUOTA_MB` | `2048` | per-turn scratch 软配额（watchdog 超额杀） |
| `SANDBOX_WATCHDOG_INTERVAL_SEC` | `5` | watchdog 巡检周期 |
| `SANDBOX_POOL_MIN_FREE_MB` | `1024` | 起容器准入水位 |
| `SANDBOX_PERSIST_MAX_TEXT_BYTES` | `20 MiB` | persist 文本判定上限，超此按 blob |
| `SANDBOX_REAP_ENABLED` | `True` | reaper 总开关 |
| `SANDBOX_REAP_INTERVAL_SEC` | `60` | reaper 周期扫间隔 |
| `SANDBOX_REAP_GRACE_SEC` | `60` | 只回收存活超此且无活跃 lease 的资源 |
| `SANDBOX_REAP_ALLOW_LOCAL_STORE` | `False` | InMemory 下显式 opt-in reaper（自担单 worker 之责） |
