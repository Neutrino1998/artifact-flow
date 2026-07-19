# 生产主机准备

本页只负责 commissioning：把一台 Linux 主机准备成能够接受 ArtifactFlow Release 的目标机。这里不构建镜像，也不启动应用。

## 支持边界

- 当前正式支持：单机 `executor = "local"`。
- CPU 架构：`x86_64/amd64` 或 `arm64/aarch64`，必须与 Release platform 一致。
- 容器：Docker daemon 和 Compose v2，不支持 Compose v1。
- Sandbox：生产使用 gVisor `runsc`；`runc` 仅用于明确的 trusted/dev 环境。
- 数据：生产使用 PostgreSQL 和 Redis，可由本站点 bundled 管理，也可连接外部服务。
- 多机 Ansible executor 尚未完成物理验收，不属于正式生产支持范围。

## 1. 计算与存储规划

项目不声明一个脱离负载的通用“最低配置”。按以下上界估算：

- 每个 Backend 容器默认内存上限 2 GiB；
- Frontend 1 GiB；
- bundled PostgreSQL 2 GiB；
- bundled Redis 768 MiB；
- 每个同时运行的 Sandbox 默认 1 GiB 内存、2 GiB workspace 软配额；
- scratch 文件系统还应保留至少 1 GiB 准入余量；
- Docker 数据目录需要同时容纳当前版、上一版、应用镜像、Sandbox 镜像和基础设施镜像。

因此主机内存和 scratch 容量应按“基础服务 + 预期并发 Sandbox”计算，并留出 OS 与升级窗口余量。不要把 `MAX_CONCURRENT_TASKS=32` 误当成一台普通主机必然能承载 32 个重任务。

生产数据盘至少分开考虑：

1. Docker data root：镜像、容器和 named volume；
2. Sandbox scratch：临时、模型可写的工作区，必须是独立挂载的定容文件系统；
3. 备份空间：不得与唯一在线 PostgreSQL volume 共用一个故障域。

## 2. 基础系统

在主机交付给应用部署人员前确认：

```bash
uname -m
getconf PAGE_SIZE
timedatectl status
df -h
df -i
```

- 系统时间必须由 NTP 同步；JWT、TLS、事件时间和跨节点诊断都依赖正确时钟。
- `arm64` 使用 runsc 时需要 4 KiB page size，即 `getconf PAGE_SIZE` 返回 `4096`。
- 确保文件句柄、进程数和 inode 能覆盖预期并发；大量小文件任务首先消耗的是 inode。
- 主机安全基线、SELinux/firewalld 策略和日志采集由现场基础设施团队负责，不由 `afctl` 修改。

麒麟 V10 arm64 的 4K kernel 离线准备见仓库中的 [kernel-4k-pkg README](https://github.com/Neutrino1998/artifact-flow/blob/main/sandbox/kernel-4k-pkg/README.md)。只有确实是 64K page kernel 的机器才需要该步骤。

## 3. Docker 与 Compose

安装完成后必须通过：

```bash
docker info
docker compose version
```

目标主机不需要 Go、Git、Node.js 或应用 Python 环境；这些属于构建机。气隙裸机可使用仓库提供的 [Docker offline package](https://github.com/Neutrino1998/artifact-flow/blob/main/sandbox/docker-pkg/README.md)，也可以使用组织已有的主机镜像或配置管理系统。

应用 Backend 会挂载 `/var/run/docker.sock` 创建同级 Sandbox 容器。这等价于给 Backend 宿主 Docker 管理权限，因此：

- Docker daemon 只能由可信管理员访问；
- 不要把 Backend 当成低信任租户容器；
- 不要让模型控制镜像、runtime、host bind path 或网络模式。

## 4. 安装 runsc

使用组织认可的 gVisor 安装方式，或在联网构建机制作仓库提供的离线包。目标机完成：

```bash
runsc --version
docker info --format '{{json .Runtimes}}'
```

第二条输出必须包含 `runsc`。随后运行 gVisor 包内的 smoke test；安装、Docker runtime 注册和验证过程见 [gVisor offline package](https://github.com/Neutrino1998/artifact-flow/blob/main/sandbox/gvisor-pkg/README.md)。

`afctl` 只检查 runsc，不会安装它，也不会在缺失时静默降级到 runc。

## 5. 准备 Sandbox scratch

选择一个稳定绝对路径，例如：

```text
/data/artifactflow/sandbox
```

由存储或主机团队为它准备独立、定容的文件系统并持久挂载。具体块设备、LVM、loop 文件和 `/etc/fstab` 写法取决于现场，应用发布不能替主机做这些不可逆选择。

验收：

```bash
findmnt -rn /data/artifactflow/sandbox
df -h /data/artifactflow/sandbox
df -i /data/artifactflow/sandbox
```

`findmnt` 必须成功。只创建一个普通目录不算完成；`afctl doctor` 会拒绝非挂载点。

多套 ArtifactFlow 共用一个 Docker daemon 时，每套部署必须使用不同的 scratch root，避免 reaper 互相清理工作区。

## 6. 网络、DNS 与 TLS

入站：

- 静态 TLS 通常开放 443；如保留 HTTP 跳转同时开放 80；
- ACME 必须由公网访问 80/443，且 DNS 的 `AF_DOMAIN` 指向本机；
- 不对外发布 Backend 8000、Frontend 3000、PostgreSQL 5432 或 Redis 6379。

出站按现场能力开放：

- 配置的 LLM endpoint；
- HTTP Tool、MCP 和 Web Fetch 所需 endpoint；
- ACME CA（仅 `tls = "acme"`）；
- external PostgreSQL/Redis。

Sandbox 容器保持 `--network=none`。需要联网的数据应通过可信 Backend Tool 获取，再作为 Artifact 显式挂入 Sandbox。

静态 TLS 需要运维提供：

```text
/opt/artifactflow/control/certs/server.crt  # leaf + intermediates
/opt/artifactflow/control/certs/server.key
```

客户端必须信任签发根 CA，私钥权限必须为 `0600`。ArtifactFlow 不生成自签名 fallback。

## 7. PostgreSQL 与 Redis

使用 external 基础设施时，在首次部署前完成：

- PostgreSQL 数据库、账号、TLS/网络策略和备份策略；
- PostgreSQL session timezone 为 UTC，应用连接层也会强制 UTC；
- Redis 连接地址和专用 key prefix；
- Redis `maxmemory-policy=noeviction`，避免 lease、interrupt、cancel 或 stream 被静默驱逐；
- 从目标主机验证 DNS、端口和凭证连通性。

使用 bundled 基础设施时，`site init` 会生成数据库参数；数据落在固定 named volume。即使 bundled，也必须在投产前确定 PostgreSQL 备份落点和恢复演练责任人。

## Commissioning 完成条件

在应用部署前，以下项目应全部有明确结果：

- [ ] 架构、page size、时间同步符合要求；
- [ ] Docker daemon 和 Compose v2 正常；
- [ ] runsc 已安装、注册并通过 smoke test；
- [ ] scratch root 是独立挂载点，容量和 inode 有余量；
- [ ] 入站端口、DNS、证书或 ACME 条件已准备；
- [ ] LLM、Tool、MCP、PostgreSQL、Redis 的出站路径已确认；
- [ ] 备份位置、监控、日志采集和 on-call 责任已确定。

完成后进入[首次部署](first-deployment.md)。`afctl doctor` 会在 `site init` 之后再次验证可机器检查的条件。
