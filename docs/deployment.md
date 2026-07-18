# 部署指南

ArtifactFlow 只有两条稳定运行路径：本地开发直接用 Compose；单机生产统一用 `afctl`。公网与内网不再是两套部署脚本，它们只是同一 release 的不同 capability 配置。多机 Ansible adapter 保留为实验性路径，尚未完成物理验收。

## 路径总览

| 场景 | 入口 | TLS | 产物 |
|---|---|---|---|
| 本地开发 / Quick Trial | `docker compose up -d` | 无 | 源码构建 |
| 内网单机 | `afctl apply` | `static` | 离线 release bundle |
| 公网单机 | `afctl apply` | `acme` | 同一 release bundle |
| 实验性内网或公网多机 | `afctl apply` | `static` / `acme` | 同一 bundle + Ansible inventory |

生产路径共同使用：

- `deploy/compose.base.yml`
- `deploy/compose.sandbox.yml`（始终启用）
- `deploy/compose.tls-acme.yml`（仅 `tls = "acme"`）
- `deploy/compose.multi-app.yml`（仅实验性 Ansible app host）

不存在生产 `build:`、`:latest`、Compose v1 fallback、sandbox disable 或缺少 runsc 时自动切到 runc 的行为。未知 CLI 参数、site 字段和 manifest 字段都会失败。

## 本地开发

```bash
cp .env.example .env
# 填 ARTIFACTFLOW_JWT_SECRET、ARTIFACTFLOW_CREDENTIAL_KEY 和模型 API key
docker compose up -d
docker compose exec backend python scripts/create_admin.py admin --password '<password>'
```

前端默认在 <http://localhost:3000>。这条路径使用 SQLite/InMemory，不能当作生产部署。

## 生产前置条件

构建机需要：

- Go 1.23+
- Docker + Docker Buildx + Compose v2
- Python 3
- 能拉取应用、sandbox 和可选 infra 镜像的网络

目标 Linux 主机需要：

- Docker daemon + Compose v2；不支持 Compose v1
- x86_64 或 arm64，与 bundle 的 `platform` 一致
- sandbox scratch 独立挂载点
- `sandbox_runtime = "runsc"` 时安装并注册 gVisor
- 静态 TLS 时由运维提供 `server.crt` 完整链和 `server.key`

实验性多机路径额外需要：

- 控制机能 SSH 到所有目标机
- 目标机 POSIX shell + Python 3.9+
- 控制机已加载 digest 固定的 Ansible Execution Environment
- inventory 中每个 app host 都有 LB 可达的 `af_advertise`
- 每个 app host 已由主机镜像/配置管理预置 runsc/runc 与 scratch mount；多机 apply 只验证

## 构建 release

release 必须来自干净 commit；脚本遇到 staged、unstaged 或 untracked 文件会失败。

```bash
# 日常应用更新：目标已有首包加载的 content-addressed infra 镜像
./scripts/release.sh 1.4.0 --app-only --platform linux/amd64

# 新环境首包：同时携带 infra 镜像
./scripts/release.sh 1.4.0 --with-infra --platform linux/amd64
```

脚本只负责构建，输出：

```text
dist/releases/1.4.0/
├── afctl
├── manifest.json
├── artifactflow-app-1.4.0.tar.gz
├── artifactflow-config-1.4.0.tar.gz
├── artifactflow-deploy-1.4.0.tar.gz
├── artifactflow-sandbox-1.4.0-amd64.tar.gz
└── artifactflow-infra-...tar.gz       # 仅 --with-infra
```

另有不重复压缩内部镜像 tar 的传输包：

```text
dist/artifactflow-release-1.4.0-amd64.tar
dist/artifactflow-release-1.4.0-amd64.tar.sha256
```

`manifest.json` 是唯一机器契约，包含 release id、类型、平台、镜像引用和每个 archive 的 SHA-256。app/frontend 使用 release tag，sandbox 与 caddy/postgres/redis 使用内容地址 tag；后续 release 不会覆盖 rollback 所依赖的 infra tag。`--app-only` 只省略这些已存在内容的传输 archive，不把引用降级为可变 upstream tag。一个 bundle 目录只能有一份固定名 manifest，因此不再需要 `AF_BUNDLE_VERSION` 猜测。

gVisor 是稳定主机能力，不属于每个应用 release。需要制作离线安装包时单独运行：

```bash
GVISOR_VERSION=20260706.0 ARCH=x86_64 \
  sandbox/gvisor-pkg/fetch-and-package.sh
```

## 首次单机部署

以下以 `/opt/artifactflow` 为目标目录。

```bash
sha256sum -c artifactflow-release-1.4.0-amd64.tar.sha256
tar xf artifactflow-release-1.4.0-amd64.tar

sudo ./1.4.0/afctl --root /opt/artifactflow site init --preset intranet
sudo vi /opt/artifactflow/control/site.toml
sudo vi /opt/artifactflow/control/.env
```

内网 preset 生成的 `site.toml` 是：

```toml
schema = 1
executor = "local"
tls = "static"
infra = "bundled"
sandbox_runtime = "runsc"
scratch_root = "/data/artifactflow/sandbox"
backend_replicas = 2
ready_timeout_seconds = 120
```

公网只把 `tls` 设为 `acme`，并在 `control/.env` 填 `AF_DOMAIN` 和 `AF_ACME_EMAIL`；ACME 固定要求公网 80/443。托管数据库把 `infra` 改为 `external` 并填写外部 PostgreSQL/Redis 地址。

### 准备 sandbox 主机能力

推荐生产使用 runsc。`afctl` 不以 root 安装 runtime、不格式化磁盘，也不修改 `/etc/fstab`；这些稳定主机能力由主机镜像、配置管理或明确的 commissioning SOP 预置。离线 gVisor 包的校验、安装和 smoke test 见 [`sandbox/gvisor-pkg/README.md`](https://github.com/Neutrino1998/artifact-flow/blob/main/sandbox/gvisor-pkg/README.md)。`scratch_root` 必须在运行 `doctor` 前成为独立挂载点。

如果明确是 trusted/dev 环境，可以在 `site.toml` 写 `sandbox_runtime = "runc"`。这是显式降低隔离强度；`afctl` 会告警，但不会替你决定，也不会从 runsc 静默降级。

所有生产部署都启用 sandbox overlay。它把 Docker socket 挂进 backend，等价于授予该进程宿主 Docker root 能力；边界、固定容器参数和 `--network=none` 设计见[沙盒架构](architecture/sandbox.md)。

### TLS

静态 TLS 必须先放置：

```text
/opt/artifactflow/control/certs/server.crt   # leaf + intermediates
/opt/artifactflow/control/certs/server.key
```

私钥权限必须是 `0600`。不会生成自签名证书作为 fallback；缺少证书或权限过宽时 `doctor/apply` 会失败。

### 检查、计划和应用

```bash
sudo ./1.4.0/afctl --root /opt/artifactflow doctor
sudo ./1.4.0/afctl --root /opt/artifactflow plan apply ./1.4.0
sudo ./1.4.0/afctl --root /opt/artifactflow apply ./1.4.0
sudo install -m 0755 ./1.4.0/afctl /opt/artifactflow/bin/afctl
```

`plan` 只读，不拿 mutation lock、不创建 release、不加载镜像。`apply` 始终：校验 → materialize 完整 release → 加载精确镜像 → 开维护页 → Compose reconcile → 通过 Caddy 探活 → 原子写 `state.json` → 关维护页。最后一条 `install` 是成功后的显式控制器升级；apply 本身不会覆盖或回滚正在使用的 `afctl`。

## 日常操作

### 新版本更新

```bash
sudo /media/1.4.1/afctl --root /opt/artifactflow plan apply /media/1.4.1
sudo /media/1.4.1/afctl --root /opt/artifactflow apply /media/1.4.1
sudo install -m 0755 /media/1.4.1/afctl /opt/artifactflow/bin/afctl
```

使用 bundle 自带控制器可以确保它理解该 bundle 的 manifest；只有 apply 成功后才更新稳定入口。安装失败不会改变已经探活成功的服务状态，可修复权限后单独重试。

### 临时修改 model YAML endpoint

生产机不需要 git、release.sh 或 Docker build：

```bash
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow \
  config checkout /tmp/model-hotfix

sudo vi /tmp/model-hotfix/config/models/models.yaml

sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow \
  config apply --id hotfix-model-20260718-153000 /tmp/model-hotfix
```

checkout 记录当前 release 和配置摘要。`config apply` 在 mutation lock 内重新检查两者，生成保留于 `.artifactflow/hotfix-bundles/<id>` 的 config bundle，再走同一个 apply。它继承 app/deploy/sandbox，不构建或加载 app 镜像；若期间有其他 release 生效，会失败而不是重基线。

### 修改 `.env` 或证书

目标机可变配置只有 `control/`：

```bash
sudo vi /opt/artifactflow/control/.env
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow site validate
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow plan apply current
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow apply current
```

换静态证书也是覆盖 `control/certs/` 后 apply current。`docker restart` 不会重读 env，因此不作为 env 更新方法。

`POSTGRES_*` 只参与空 volume 的首次 init。已有数据库改密码必须先执行 `ALTER USER`，再同步 `POSTGRES_PASSWORD` 与应用 URL；单纯改 env 不会改变数据库内部密码。

### 回滚、状态和维护页

```bash
afctl --root /opt/artifactflow plan rollback
afctl --root /opt/artifactflow rollback
afctl --root /opt/artifactflow status
afctl --root /opt/artifactflow maintenance on "模型端点维护"
afctl --root /opt/artifactflow maintenance off
```

rollback 解析 `state.previous` 后调用同一个 apply executor，不存在独立 rollback 状态机。`apply` 与 `rollback` 总是持有 kernel advisory lock；进程退出会释放，不需要 stale lock 清理。

日志保持透明，可直接使用 Docker：

```bash
docker ps --filter label=com.docker.compose.project=artifactflow
docker logs artifactflow-backend-1 --tail 200
docker logs artifactflow-caddy-1 --tail 200
```

## 实验性多机部署

复制 [`deploy/inventory.ini.example`](https://github.com/Neutrino1998/artifact-flow/blob/main/deploy/inventory.ini.example) 到 `/opt/artifactflow/control/inventory.ini`，然后修改 site：

```toml
executor = "ansible"
backend_replicas = 1
inventory = "control/inventory.ini"
ansible_ee_image = "registry.internal/artifactflow-ansible-ee@sha256:<digest>"
```

Execution Environment 必须预先以精确 digest 加载。`doctor` 在尚无真实验收环境时只检查控制机 inventory 与 EE，不伪装成远端 dry-run；apply 开始时会在任何服务 mutation 前逐机 loud-fail 检查 Docker/Compose、架构、runsc 和 scratch mount。初版 playbook 只使用 `ansible.builtin`，通过 Compose CLI 操作远端，不依赖 community collection。apply 顺序为：远端 capability check → 分发完整 release → infra → 唯一 release gate → app host `serial: 1` → 汇总所有 backend/frontend upstream → LB → Caddy 健康。

`backend_replicas = 1` 表示每个 app inventory host 运行一份 backend/frontend；多机扩容通过增加 app host，而不是在一台机器上再 scale。一个物理机承担多个角色时，在多个 group 重复同一个 inventory hostname，不要为同一个 `ansible_host` 起多个 alias。Ansible 路径不会安装 runsc 或创建 scratch filesystem，以免应用发布顺手改变稳定宿主能力；commissioning 前应先用基础镜像或既有配置管理完成这些准备。

实验性多机入口仍是完全相同的：

```bash
afctl --root /opt/artifactflow doctor
afctl --root /opt/artifactflow plan apply /media/1.4.1
afctl --root /opt/artifactflow apply /media/1.4.1
```

第一套真实多机环境尚未完成物理验收。`executor = "ansible"` 会在每次校验时明确告警。首次 commissioning 必须安排维护窗口，验证 SSH 权限、目标 Python、LB→app 路由、防火墙、部分主机失败和 rollback；在此之前它不属于 production-supported contract，也不宣称零停机 SLA。

## 从 Fleet v1 迁移

若目录里已有 `deploy/.env`，`site init` 会拒绝生成新密钥。使用显式迁移：

```bash
sudo ./1.4.0/afctl --root /opt/artifactflow site migrate-v1 \
  --preset intranet --sandbox-runtime runsc
```

它只迁移 target-local `.env` 与证书，并删除已废弃的 `AF_ENABLE_SANDBOX`。旧 `.fleet-state`、`.artifactflow/current` 和 `.af-release` 不会被同步进新状态；同步两套状态正是 v1 复杂性的来源。随后先 `doctor/plan`，再应用一个完整 v2 bundle，成功后建立唯一 `.artifactflow/state.json`。旧 release 只作人工应急参考，不是 v2 rollback 候选。

如果安装早到 Docker volumes 仍使用 `deploy_*` 前缀，第一次 v2 apply 前先按 [`deploy/MIGRATION-project-name.md`](https://github.com/Neutrino1998/artifact-flow/blob/main/deploy/MIGRATION-project-name.md) 在维护窗口复制到固定 `artifactflow_*` 名称；afctl 不会猜测或自动移动生产数据。

## 环境变量完整参考

`site.toml` 只放部署能力，不放 secret。应用 secret 和 tunable 放 `control/.env`：

- 必填：`ARTIFACTFLOW_JWT_SECRET`、`ARTIFACTFLOW_CREDENTIAL_KEY`
- 数据库：`ARTIFACTFLOW_DATABASE_URL` 或 `ARTIFACTFLOW_DATABASE_URLS`
- Redis：`ARTIFACTFLOW_REDIS_URL`、`ARTIFACTFLOW_REDIS_KEY_PREFIX`
- 公网 ACME：`AF_DOMAIN`、`AF_ACME_EMAIL`
- 模型/工具 key：`DASHSCOPE_API_KEY`、`OPENAI_API_KEY`、`BOCHA_API_KEY` 等
- 并发/超时/池大小：所有 `ARTIFACTFLOW_*` 默认值以 [`src/config.py`](https://github.com/Neutrino1998/artifact-flow/blob/main/src/config.py) 为准

`AF_ENABLE_SANDBOX` 已删除；sandbox runtime/scratch 由 `site.toml` 唯一控制。不要在 `.env` 重复配置不同 runtime，`site validate` 会拒绝冲突。
