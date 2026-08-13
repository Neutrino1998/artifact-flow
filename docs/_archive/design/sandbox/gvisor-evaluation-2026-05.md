# 沙盒方案选型与 Kylin V10 SP2 兼容性评估 (2026-05)

> 归档时间：2026-05-13  
> 状态：诊断阶段已完成；工具实现阶段未启动  
> 相关产物：`dist/sandbox-gvisor-20260512.tar.gz`（gVisor 离线安装包，未入库 git）

## 1. 我们要做什么

给 ArtifactFlow 的 agent 增加一个**沙盒化的 bash + 文件系统执行工具**，让 lead/research agent 可以安全执行任意 shell 命令、运行 Python 脚本、处理用户上传的文件。

### 核心需求

- **沙盒执行**：agent 跑的代码完全隔离于 host，不可访问 host 文件系统/网络/进程
- **用户多格式文件上传**：用户在前端上传任意格式文件（pdf / xlsx / csv / 图片 / 代码 / 压缩包等）
  → 走 `create_from_upload` 落到 **artifact 系统**（已有，立即提交 DB）
  → 沙盒启动时把 session 下相关 artifact **物化挂载**进容器文件系统
  → agent 在沙盒里读写文件
  → turn 结束时**显式 commit**：通过 `ArtifactManager.create/update_artifact` 把产物文件写回 artifact 系统
  → controller post-processing 阶段 `flush_all()` 一次性落库（沿用现有 write-back 语义）
- **session 粒度生命周期**：沙盒和 `session_id`（= conversation_id）绑定，按 turn ephemeral，turn 结束即销毁。匹配现有 artifact、lease、cancel/interrupt 的语义边界
- **多 worker 高可用兼容**：沙盒挂在 `message_id` 的 Redis lease 上，worker 间不会抢，worker 挂掉后沙盒一起没（artifact 已落库不丢）

### 部署形态

单机部署，沙盒和 backend/frontend 同 host，由 **同一个 Docker daemon** 起：

```
Host (Kylin V10 SP2)
└─ Docker daemon (注册了 runsc 作为可选 runtime)
   ├─ artifactflow-backend       (runtime=runc, 现有服务)
   ├─ artifactflow-frontend      (runtime=runc, 现有服务)
   ├─ postgres / redis / nginx   (runtime=runc)
   └─ sandbox-<session-xxx>      (runtime=runsc, 临时, --rm)
```

backend 容器挂 `/var/run/docker.sock`，通过 Docker HTTP API（`aiodocker`）按需起沙盒容器。这是标准的 **Docker-out-of-Docker (DooD)** 模式，GitLab Runner / Jenkins 等都是这套。

---

## 2. 服务器环境

### 生产目标：Kylin Linux Advanced Server V10 (Sword) SP2

| 项 | 值 |
|---|---|
| OS | Kylin Linux Advanced Server V10 (Sword) |
| 内核 | `4.19.90-24.4.v2101.ky10.x86_64`（基于 openEuler 20.03 LTS / RHEL 8 谱系） |
| 架构 | x86_64 |
| 硬件 | 物理机（`systemd-detect-virt = none`） |
| 配置 | 32C / 256G RAM / 物理 KVM |
| `/dev/kvm` | 存在（perm 660, group=kvm） |
| VT-x | 32 个核全部支持 |
| Docker | 28.0.1 + containerd, runc |
| cgroup | v1（驱动 cgroupfs，**非** systemd） |
| SELinux | Disabled |
| AppArmor | 未加载 |

### 测试机器

诊断阶段用了两台 Kylin V10 SP2（同 OS、同内核版本字符串、同一份 kysec 策略文件）：

- **milvus2** — 沙盒可用的"参考画像"
- **RH2288-07** — 沙盒被加固拦截，详见第 4 节

---

## 3. 技术选型：gVisor (`runsc`)

最终选型：**gVisor**，KVM 平台模式（系统有 `/dev/kvm` 时），Systrap 平台模式兜底。

### 行业现状（2025-2026）

生产级 AI agent 沙盒的两大主流方案：

| 方案 | 代表产品 | 隔离边界 | Cold-start | 硬件依赖 |
|---|---|---|---|---|
| **gVisor** | Modal、Google GKE Agent Sandbox（Gemini 自家用）、Ant 蚂蚁规模化、Alibaba OpenSandbox | 用户态 Sentry 内核拦截 syscall | 数百 ms | 无（Systrap），有 KVM 时性能更好 |
| **Firecracker microVM** | E2B、Daytona、Fly.io、AWS Lambda 谱系 | 硬件级（KVM 起独立 microVM） | ~125 ms | 强依赖 `/dev/kvm` + 物理机或开启嵌套虚拟化 |

**裸 Docker / runc 已被踢出"跑不可信代码"的可选范围**：2025-11 又爆 runc 3 个 CVE（CVE-2025-31133/52565/52881），叠加 2024-21626，AI agent 场景被安全圈点名高危。

### 选 gVisor 而不是 Firecracker 的理由

| 维度 | gVisor 优 | Firecracker 优 |
|---|---|---|
| 工程量 | ★★★★★ 一个 OCI runtime 二进制 + `docker --runtime=runsc` | ★★ 需自管 microVM 生命周期、kernel image、rootfs、vsock |
| 集成成本 | ★★★★★ 复用现有 Docker workflow、所有 OCI 镜像 | ★★ 镜像格式不一样，要重做编排 |
| 硬件兼容 | ★★★★★ Systrap 平台不需 KVM；有 KVM 时切 KVM 平台性能接近原生 | ★★ 严格 KVM；VM 上要嵌套虚拟化 |
| 隔离强度 | ★★★★ 用户态内核足够防 LLM 生成代码逃逸 | ★★★★★ 硬件级隔离，更强 |
| Syscall 兼容性 | ★★★ Sentry 实现 ~200 syscall，某些 C 扩展会踩 ENOSYS | ★★★★★ 真内核，原生兼容 |
| 性能 | ★★★ 系统调用 ~10x 损耗（CPU/IO 工作负载接近原生） | ★★★★ 接近原生 |

**判断**：
- ArtifactFlow 的沙盒主要跑 Python / bash / 数据处理，**CPU + IO 居多，syscall 不密集**，gVisor 性能损耗可接受
- 隔离强度对"agent 跑 LLM 生成的 Python"够用（Modal、GKE Agent Sandbox 都是这条路线，已规模化验证）
- Kylin V10 SP2 这台 RH2288 是物理机 + `/dev/kvm` 可用，**gVisor 走 KVM 平台模式**性能最佳
- 工程量小一个数量级，能更快进入实际工具实现阶段

**保留 Firecracker 作为后手**：如果某些 agent workload（io_uring、特殊 syscall）在 gVisor Sentry 里挂掉，再切。但**不是 MVP 范围**。

### gVisor 版本

锁定 `release-20260504.0`，来源 `https://storage.googleapis.com/gvisor/releases/release/20260504.0/x86_64/`。
打成离线包 `dist/sandbox-gvisor-20260512.tar.gz`（46MB），内含 `runsc` + `containerd-shim-runsc-v1`（Linux x86_64 静态 ELF）+ 校验和 + 安装脚本 + 烟测脚本。

---

## 4. 诊断过程：SSH 卡 25s + gVisor 起不来

两台同版本 Kylin 行为分裂。最终定位是**内核运行时拒绝 `CLONE_NEWUSER` (user namespace 创建)**，且无任何用户态可见开关。

### 4.1 现象

**RH2288-07**：

```bash
$ ssh root@RH2288-07         # 卡 25s 才能进入
$ time hostnamectl --transient
Could not get property: Failed to activate service 'org.freedesktop.hostname1':
  timed out (service_start_timeout=25000ms)
real    0m25.019s

$ sudo runsc --platform=systrap do echo test
creating container: cannot create gofer process: gofer: fork/exec /proc/self/exe:
  operation not permitted
```

**milvus2**：以上命令全部秒返回成功。

### 4.2 排查走过的弯路（全部排除）

| 假设 | 验证方式 | 结论 |
|---|---|---|
| EDR（奇安信、360、深信服等）反进程伪装 | argv[0] 改名 fork+exec 实验 | ❌ 两台均通过 |
| 多 FD donation 触发 EDR 规则 | Python 8 FD + argv 伪装 execve | ❌ 两台均通过 |
| 已知 EDR 守护进程 | 服务/模块/RPM 关键字扫描 | ❌ 无命中 |
| `kysec`（麒麟安全增强） | `/sys/kernel/security/kysec/status` | ❌ 两台均 status=0，policy 文件 sha256 一致 |
| `kmodprotect-init` | `systemctl cat` + 包内容 | ❌ 两台均一次性 oneshot 退出，unit 文件一致 |
| SELinux / AppArmor / Yama / kernel lockdown / IMA | sysfs / sysctl | ❌ 均未启用 |
| firewalld 策略 | `systemctl is-active` | ❌ 两台均 inactive |
| `kernel.unprivileged_userns_clone` sysctl | `/proc/sys/kernel/...` | ❌ Kylin 4.19 没这个 sysctl |
| cgroup driver 差异 | `systemd-run --scope` / `--systemd-cgroup=false` | ❌ 无效 |
| runsc 二进制损坏 | sha256 | ❌ 两台一致 |
| `user.max_*_namespaces` 配额 | `sysctl` | ❌ 两台均 1024871 |
| mount noexec / no capability / NoNewPrivs | `mount` / `capsh --print` | ❌ 均正常 |

### 4.3 关键转折：SSH 卡 25s 与 gVisor 是同一个根因

`systemctl status systemd-hostnamed.service` 在 RH2288-07 上：

```
Active: failed (Result: exit-code)
Main process exited, code=exited, status=226/NAMESPACE
```

**`226/NAMESPACE` = systemd "无法建立 unit 所需 namespace，启动失败"**。systemd-hostnamed.service 的 unit 包含 `PrivateUsers=yes` 等 sandbox 指令，需要创建 user namespace。

→ user namespace 创建被拒，systemd-hostnamed 启不来  
→ hostnamectl 调用 D-Bus 走 systemd 激活机制，等 25 秒超时 → SSH 卡顿  
→ 同样的 user namespace 创建被拒，gVisor gofer fork() 后的 `clone(CLONE_NEWUSER)` 失败 → gofer 起不来 → "fork/exec EPERM"（Go 错误包装）

### 4.4 实锤证据：raw C clone() = EPERM

```c
// 直接 clone(CLONE_NEWUSER | SIGCHLD)，root 身份
int pid = clone(child, stack+8192, CLONE_NEWUSER|SIGCHLD, NULL);
// → clone failed: 1 (Operation not permitted)
```

`unshare` 实验也一致：

| flag | milvus2 | RH2288-07 |
|---|---|---|
| `-m` mount | exit=0 | exit=0 |
| `-n` net | exit=0 | exit=0 |
| `-i` ipc | exit=0 | exit=0 |
| `-u` uts | exit=0 | exit=0 |
| **`-U` user** | **exit=0** | **❌ EPERM exit=1** |
| `-p --fork` pid | exit=0 | exit=0 |

**只有 user namespace 被拒**，其他 namespace 类型全部正常。

### 4.5 根因（确认 + 未完全揭开的部分）

**确认**：RH2288-07 的内核运行时拒绝 `clone(CLONE_NEWUSER)`，即使 root 身份且 capability bounding set 完整。

**已排除作为原因**：
- `CONFIG_USER_NS=y`（内核构建时启用了 user namespace，不是编译关闭）
- 任何标准 sysctl（不存在任何 userns 控制项）
- `/proc/cmdline` 启动参数
- 已注册的 LSM（`/sys/kernel/security/lsm = capability` 单一）
- kysec（status=0，policy 一致）
- 所有 namespace 配额（1024871，充足）

**剩余未解开的部分**：拒绝逻辑藏在 Kylin 私有内核补丁里，不通过任何用户态接口暴露。可能是：
- 装机时套用的未公开加固包 / initramfs hook
- 出厂内核的非 mainline CONFIG 开关
- 某个固件位 / TPM 状态触发

**这部分需要 Kylin 厂商支持**。

---

## 5. 实操结论（生产可用）

### 5.1 部署前预检（一行）

```bash
# root 身份，秒返回:
sudo unshare -U /bin/true && echo "PASS: gVisor 可用" || echo "BLOCKED: 跑不了沙盒"

# 等价的无 root 命令（任何用户都能跑）:
time hostnamectl --transient
# 秒返回 = OK; 卡 25s = BLOCKED
```

**任何 Kylin 节点入沙盒服务池前必须先跑这一行预检**。BLOCKED 的机器禁止部署沙盒服务。

### 5.2 生产部署规则

- **以 milvus2 为参考画像**：拿 milvus2 的 system image 做基线，复制到其它沙盒节点
- **不要复用 RH2288-07 上的 system image** 做沙盒节点
- **如果生产节点出现 unshare -U 失败**：把第 5.3 节的证据包甩给运维/厂商，要求关掉 user namespace 限制补丁

### 5.3 给运维 / 厂商的证据包

```
症状: ArtifactFlow 沙盒服务无法启动
环境: Kylin Linux Advanced Server V10 (Sword), 内核 4.19.90-24.4.v2101.ky10.x86_64
故障复现 (root):
  $ sudo unshare -U /bin/true
  unshare: unshare 失败: 不允许的操作
  $ sudo runsc --platform=systrap do echo test
  cannot create gofer process: gofer: fork/exec /proc/self/exe: operation not permitted

关联症状: hostnamectl --transient 卡 25s; systemd-hostnamed.service status=226/NAMESPACE

仅 CLONE_NEWUSER 被拒，CLONE_NEWNS/NEWNET/NEWPID/NEWIPC/NEWUTS 全部正常。
raw C clone(CLONE_NEWUSER|SIGCHLD) → errno=1 EPERM。

已排除 (相同 OS 内核版本另一台 milvus2 机器上以上全部 PASS):
- CONFIG_USER_NS=y 已开启
- 所有 user.max_*_namespaces 配额充足 (1024871)
- kernel.unprivileged_userns_clone sysctl 不存在
- /proc/cmdline 启动参数无 userns 相关项
- SELinux/AppArmor/Yama/lockdown/IMA 均未启用
- kysec status=0; policy 文件 sha256 与正常机一致
- capability bounding set 完整; Securebits unlocked
- 用户态 sysctl/配置文件无任何 userns 相关项

请协助确认本机是否套用了非公开的 user namespace 限制补丁/策略，以及关闭方式。
```

---

## 6. 下一阶段（未开工）

诊断与选型阶段已完成。下一步实施需要：

1. **沙盒镜像**：基于 `python:3.11-slim` + 数据科学常用包预热（numpy/pandas/matplotlib/requests），按 CLAUDE.md "离线 tar 打包" 流程出 `artifactflow-sandbox-<date>.tar.gz`
2. **`BashTool` 工具实现**：实现 `BaseTool` 子类，参数表只暴露 `command` / `timeout`；隐藏常量（max_output_bytes、default_timeout）放 `src/config.py`
3. **`SandboxManager` 生命周期**：按 `session_id` 起容器，挂 artifact 物化目录，复用 `RuntimeStore.lease` 机制；turn 结束通过 `ArtifactManager.create/update_artifact` 显式 commit 产物文件
4. **DooD 集成**：`docker-compose.prod.yml` 给 backend 加 `/var/run/docker.sock` 挂载；用 `aiodocker` 调 Docker API 起沙盒
5. **资源/超时配额**：每沙盒 `--memory` / `--cpus` / `--pids-limit` / 自定义 docker network（限制出站）
6. **部署文档**：在 `deploy/` 加入 gVisor 装包流程；docker-compose 加 runsc runtime 注册说明

---

## 附录 A: 离线包内容

`dist/sandbox-gvisor-20260512.tar.gz`（46MB，git ignore）：

```
sandbox-gvisor-20260512/
├── VERSION                            # release-20260504.0, x86_64
├── README.md
├── bin/
│   ├── runsc                          # gVisor OCI runtime (Linux x86_64 static)
│   ├── runsc.sha512                   # 上游签名
│   ├── containerd-shim-runsc-v1
│   └── containerd-shim-runsc-v1.sha512
├── install.sh                         # 校验签名 + 装到 /usr/local/bin + 写 daemon.json (jq/python3 合并)
├── smoke-test.sh                      # 五层渐进烟测 (binary / Sentry / Docker runtime / 容器 / syscall 探针)
└── uninstall.sh
```

安装：
```bash
sudo ./install.sh              # 不自动 reload docker
sudo systemctl reload docker   # 用 reload 而非 restart，不影响运行中容器
sudo ./smoke-test.sh
```

## 附录 B: 已验证可用的烟测路径（milvus2）

- Tier 1 `runsc --version` ✓
- Tier 2 `runsc --platform=systrap/kvm do echo` ✓（两个平台都通）
- Tier 3 `docker info` 列出 runsc runtime ✓
- Tier 4 `docker run --rm --runtime=runsc --entrypoint /bin/sh <image> -c 'echo …'` ✓
- Tier 5 容器内 `uname -a` / `ls /proc` syscall 探针 ✓

完整生命周期（gVisor debug log 验证）：sandbox 起 → app 跑 → gofer reap exit 0 → cgroup 清理 → 网络/iptables 清理 → exit 0。

## 附录 C: 时间线

- 2026-05-12 提出沙盒需求 → 调研选型 → 内网摸底（CentOS 7 测试机内核太老不能跑 → 确认生产用 Kylin V10 SP2）
- 2026-05-13 milvus2 验证 gVisor 全功能可用 → RH2288-07 暴露 gofer EPERM 问题 → 多轮诊断（EDR / kysec / 加固模块 / cgroup / namespace）→ 最终定位 user namespace 内核拒绝 → 串联 SSH 25s 卡顿
- 2026-05-13 归档（此文）
