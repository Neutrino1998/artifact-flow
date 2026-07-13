# 部署指南

> 五种部署模式覆盖从本地试用到内网离线的全部场景。

## 部署模式总览

```mermaid
graph TD
    Start{选择部署模式}
    Start -->|本地试用| M1[Mode 1: Quick Trial]
    Start -->|生产部署| Prod{基础设施}
    Start -->|内网离线| Intra{基础设施}

    Prod -->|自建 PG + Redis| M2A[Mode 2A: 自建基础设施]
    Prod -->|云数据库 RDS| M2B[Mode 2B: 云数据库]
    Intra -->|自建 PG + Redis| M3A[Mode 3A: 内网自建]
    Intra -->|内部 DB 服务| M3B[Mode 3B: 内网托管DB]
```

| Mode | 场景 | 服务 | 数据库 | Compose 文件 |
|------|------|------|--------|-------------|
| **1: Quick Trial** | 本地试用 | backend + frontend | SQLite + InMemory | `docker-compose.yml` |
| **2A: Prod 自建** | 生产 + 自建基础设施 | Caddy + backend + frontend + PG + Redis | 容器化 | `docker-compose.prod.yml --profile infra` |
| **2B: Prod 云数据库** | 生产 + RDS/ElastiCache | Caddy + backend + frontend | 外部托管 | `docker-compose.prod.yml` |
| **3A: 内网 自建** | 离线/内网部署 | Caddy + backend + frontend + PG + Redis | 容器化 | `deploy/docker-compose.intranet.yml --profile infra` |
| **3B: 内网 托管DB** | 离线 + 内部DB服务 | Caddy + backend + frontend | 内部托管 | `deploy/docker-compose.intranet.yml` |

**关键区别：**

- **Mode 2 vs 1：** Caddy 反向代理（自动 HTTPS / Let's Encrypt，端口 80+443）、PG + Redis 持久化、Alembic 自动迁移
- **Mode 3 vs 2：** 同为 Caddy，但 **TLS 姿态相反**——内网是气隙环境，ACME 不可达，用**静态证书**（公司测试中心/内部 CA 签发，放 `deploy/certs/`，见该目录 README）+ 全局 `auto_https off`（防手滑写出裸域名 site 触发 ACME 拨号卡死）；`image:` 替代 `build:`，通过 `docker save/load` 离线部署，无需访问外部镜像仓库
- **代理配置的组织：** 两个模式共用 `deploy/caddy/common.caddy`（路由顺序 / 维护页 gate / SSE flush / 上传总量闸 / X-Real-IP / 多副本轮询 + wedge 副本被动摘除，全部模式无关机制只写一遍），入口文件各留薄壳——`deploy/caddy/Caddyfile`（Mode 2：ACME + AF_DOMAIN）/ `deploy/caddy/Caddyfile.intranet`（Mode 3：静态 tls + HTTP→HTTPS 跳转）。`deploy/caddy/` 整目录挂载进容器（单文件挂载 pin inode，编辑/tar 覆盖后 reload 会断）。维护页 flag 机制（`deploy/maintenance/`）同一套。
- **2A/3A vs 2B/3B：** `--profile infra` 控制是否启动 PG/Redis 容器

**`deploy/` 下的独立文档**（本文档覆盖不到的细节，按需展开）：

- [`deploy/FLEET.md`](https://github.com/Neutrino1998/artifact-flow/blob/main/deploy/FLEET.md) — `fleet.sh` 单机→多机统一发布入口（见下方「扩缩容与多机发布」）
- [`deploy/MULTI-REPLICA.md`](https://github.com/Neutrino1998/artifact-flow/blob/main/deploy/MULTI-REPLICA.md) — release-vs-serve 多副本拆分的完整设计 + 真机验收记录/清单
- [`deploy/MIGRATION-project-name.md`](https://github.com/Neutrino1998/artifact-flow/blob/main/deploy/MIGRATION-project-name.md) — 一次性 `deploy_*` → `artifactflow_*` volume 改名迁移（仅升级自 pre-pin 版本的老部署需要，新部署跳过）

---

## Mode 1: Quick Trial

最简部署，SQLite + InMemory RuntimeStore，适合本地试用和开发。

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 API Keys 和 JWT secret

# 2. 启动
docker compose up -d

# 3. 创建管理员
docker compose exec backend python scripts/create_admin.py admin --password <your-password>

# 4. 访问
# 前端: http://localhost:3000
# API 文档: http://localhost:8000/docs（需设置 ARTIFACTFLOW_DEBUG=true）
```

**注意事项：**

- 前端 3000 → 后端 8000 跨端口，CORS 默认开启
- 数据存储在 Docker named volume `artifactflow_data`
- 不支持多副本（InMemory RuntimeStore 是单进程的）

---

## Mode 2A: Production（自建基础设施）

完整生产部署，PG + Redis 容器化，Caddy 反向代理（自动 HTTPS）。

### 前置准备

```bash
# 1. 从模板创建 .env
cp deploy/.env.prod.example .env

# 2. 编辑 .env，必须填写：
#    - ARTIFACTFLOW_JWT_SECRET（生成: python -c "import secrets; print(secrets.token_urlsafe(32))"）
#    - POSTGRES_PASSWORD（强密码）
#    - DASHSCOPE_API_KEY（默认模型必填）
#    - AF_DOMAIN（公网域名，如 app.example.com — Caddy 给它签证书）
#    - AF_ACME_EMAIL（Let's Encrypt 账户邮箱，到期提醒发这里）

# 3. 域名 DNS 必须先解析到本机公网 IP，再启动 —— 否则 Caddy 的 ACME
#    HTTP-01 验证失败、拿不到证书。主机防火墙需放行 80（ACME 验证 + 跳 https）
#    和 443（HTTPS）。
#
# 4. 【首次迁移一次性】确保宿主机 80 / 443 没有别的进程占用 —— Caddy 要独占这两
#    个端口。若机器上原本跑着裸 nginx / apache（或旧版本用 nginx 容器的 compose），
#    先停掉再启动，否则 Caddy 绑端口失败起不来：
#      sudo systemctl stop nginx && sudo systemctl disable nginx   # 宿主机 nginx
#      # 或：docker rm -f <旧 nginx 容器>
```

### 启动

```bash
docker compose -f docker-compose.prod.yml --profile infra up -d

# 或用一键脚本（带 .env 必填项预检 + tail caddy 证书日志）：
# ./deploy/scripts/deploy-prod.sh
```

> **首次启动看证书签发：** `docker compose -f docker-compose.prod.yml logs -f caddy`，
> 看到 `certificate obtained successfully` 即成功。卡住通常是 DNS 未生效或 80 端口
> 未放行（ACME HTTP-01 需要 80 可达）。证书写入 `caddy_data` 卷，到期前 30 天 Caddy
> 自动续期，无需运维。**`caddy_data` 卷务必持久化**（compose 已声明）—— 丢卷会触发
> 重新签发，频繁重建可能撞 Let's Encrypt 每域每周 50 张的频控。

### 首次初始化

```bash
# Alembic 自动迁移（容器 entrypoint 自动完成，无需手动）
# 确认迁移成功：
docker compose -f docker-compose.prod.yml logs backend | grep -i "alembic"

# 创建管理员
docker compose -f docker-compose.prod.yml exec backend \
  python scripts/create_admin.py admin --password <your-password>
```

### 验证

```bash
# 健康检查（经 Caddy 内部端口 :2021 真正过反代，验证配置已加载 + Caddy→backend
# 通；该端口不发布到宿主机，避开 TLS-on-localhost 域名不匹配）
docker compose -f docker-compose.prod.yml exec caddy \
  wget -qO- http://localhost:2021/health/ready
# 预期: {"status":"ok","db":"ok","redis":"ok"}

# 公网（DNS 已解析 + 证书已签发后）
curl https://$AF_DOMAIN/health/ready
open https://$AF_DOMAIN
```

### 扩缩容

```bash
# 水平扩展 backend（Caddy 自动负载均衡）
docker compose -f docker-compose.prod.yml --profile infra up -d --scale backend=2

# 注意：一次性 release 服务先跑迁移 + reconcile 并退出，backend 靠 compose
# depends_on 等它完成后再起来 serve（release-vs-serve 拆分，非各副本抢锁）
# 详见「运维参考 → 数据库迁移」与 deploy/MULTI-REPLICA.md
```

---

## Mode 2B: Production（云数据库）

使用外部 RDS + ElastiCache/Redis，不启动数据库容器。Caddy / backend / frontend
与 2A 相同，仅数据库来源不同。

### 配置

```bash
cp deploy/.env.prod.example .env
# 编辑 .env：
# - AF_DOMAIN / AF_ACME_EMAIL（同 2A，Caddy 自动 HTTPS 必填）
# - 修改连接地址：
#   ARTIFACTFLOW_DATABASE_URL=postgresql+asyncpg://user:pass@your-rds-endpoint:5432/artifactflow
#   ARTIFACTFLOW_REDIS_URL=redis://your-redis-endpoint:6379
# - 删除或注释掉 POSTGRES_* 相关变量
```

### 启动

```bash
# 不加 --profile infra，不启动 PG/Redis 容器
docker compose -f docker-compose.prod.yml up -d

# 或用一键脚本（2B 必须 --no-infra，否则会多起没用的本地 PG/Redis）：
# ./deploy/scripts/deploy-prod.sh --no-infra
```

---

## Mode 3: 内网离线部署

适用于无法访问外部网络的环境。使用预构建镜像，通过 `docker save/load` 传输。

> **`docker compose` vs `docker-compose`：** 下面的命令用 V2 写法（`docker compose`，带空格）。CentOS 7 等老环境如果只有 V1（`docker-compose`，带横线），把所有 compose 调用替换成 V1 即可，compose 文件本身两版都解析。`pause.sh` / `resume.sh` 自动探测，无需替换。

### 构建发布包（在有网络的构建机上）

```bash
# 滚动更新（默认 --app-only，不打 infra 镜像）—— 95% 场景走这条
./scripts/release.sh 1.0.0
# 产出（dist/）：
#   artifactflow-app-1.0.0.tar.gz         (~240MB, backend + frontend)
#   artifactflow-config-1.0.0.tar.gz      (~10KB, config/)
#   artifactflow-deploy-1.0.0.tar.gz      (~15KB, deploy/)
#   artifactflow-1.0.0.manifest.txt       发版清单（commit、镜像 id、关键文件）
#   *.sha256                              逐 tar 校验和

# 首次部署 / caddy-pg-redis 版本升级 —— 显式加 infra
./scripts/release.sh 1.0.0 --with-infra
# 额外产出：
#   artifactflow-infra-caddy2.10-pg16-redis7.tar.gz  (~130MB)
# 文件名按 base image 版本内容寻址 —— 目标机已有同名 tar 就可跳过传输

# 首次部署且要启用 bash/mount/persist 沙盒工具 —— 再加 sandbox 宿主前置包
./scripts/release.sh 1.0.0 --with-infra --with-sandbox
# 额外产出：
#   artifactflow-sandbox-1.0.0-amd64.tar.gz       sandbox 运行镜像
#   artifactflow-sandbox-verify-1.0.0.tar.gz      gVisor 验证探针
#   sandbox-gvisor-<date>-x86_64.tar.gz           runsc 离线安装包

# release 是阶段化的；如果网络在后段下载 gVisor / wheels 时抖掉，
# 修复网络后用 --resume 重跑，会跳过已完成且输入未变的 app/infra/sandbox-image 等阶段。
./scripts/release.sh 1.0.0 --with-infra --with-sandbox --resume
# 如需强制重跑某一阶段：
./scripts/release.sh 1.0.0 --with-infra --with-sandbox --resume --force gvisor
```

> **拆 4 tar 按变更频率分层：**
> - `config` / `deploy` 是 bind-mount 进容器的,改 prompt / Caddyfile / scripts 重传对应 tar 即可,不动镜像。
> - `app` 是 backend + frontend,几乎每次发版都改。
> - `infra` 是 caddy / postgres / redis 三个 base image,版本动得极少（半年一次量级）,默认**不打**,显式 `--with-infra` 才生成。命名带 base image 版本号方便目标机一眼看出"我已经有这个 infra tar 了"。

> **目标平台默认 `linux/amd64`。** Apple Silicon 上跑 `release.sh` 会自动通过 buildx + QEMU 交叉编译，省得装到 x86_64 服务器后撞 `exec format error`。要构建 arm64 目标，用 `./scripts/release.sh 1.0.0 --platform linux/arm64`（或继续传 `PLATFORM=linux/arm64`）。一次 release 只产出一个目标架构的 bundle。

### 首次部署（在目标内网机器上）

推荐把**发布包目录**和**运行目录**分开：发布 tar 保持原样放在 bundle
目录，运行目录只放解出的 `deploy/`、`config/`、`.env`、证书和 fleet 状态。

```bash
VERSION=1.0.0
BUNDLE=/root/workspace/tmp/$VERSION
APP=/root/workspace/artifactflow

# 1. 用现场批准的介质/流程把发布文件放到 $BUNDLE/
#    必备：
#      artifactflow-{app,config,deploy}-$VERSION.tar.gz{,.sha256}
#      artifactflow-$VERSION.manifest.txt
#    首次部署 / infra 镜像变更时还需要：
#      artifactflow-infra-caddy2.10-pg16-redis7.tar.gz{,.sha256}
#    启用 sandbox 时还需要 sandbox image / verify / gVisor 三件套及其 .sha256。
mkdir -p "$BUNDLE" "$APP"

# 2. 先只解 deploy/ 到运行目录，拿到 fleet/verify 脚本。
cd "$APP"
tar xzf "$BUNDLE/artifactflow-deploy-$VERSION.tar.gz"
deploy/scripts/verify-bundle.sh "$BUNDLE"

# 3. 初始化单机拓扑和 .env。
#    init-local 首次创建 deploy/.env 时会自动填充 JWT secret、Fernet
#    credential key、Postgres password，并同步 DATABASE_URL；已有 .env 不覆盖。
deploy/scripts/fleet.sh init-local --scale 2
vi deploy/.env        # 填 API keys / CORS / 并发等部署专属值
vi deploy/fleet.conf  # app local scale=N 即 docker compose --scale backend=N

# 4. 正式配置应在构建机改 config/ 后再 release。
#    不要在目标机先解 config/、改完再 fleet deploy：deploy 会重新从 bundle 解 config，
#    覆盖这些现场修改。模型 endpoint 临时热修见下方「运行时配置变更」。

# 5. 启用沙盒时，先以 root 准备 runsc / sandbox 镜像 / scratch 根。
#    仅当 release bundle 用 --with-sandbox 构建、且本部署要启用沙盒时执行本步。
#    生产不要用 8G starter；按并发 × SANDBOX_WORKSPACE_QUOTA_MB 估算。
# sudo env \
#   AF_BUNDLE_VERSION="$VERSION" \
#   AF_SANDBOX_POOL=/data/artifactflow/sandbox-pool.img \
#   AF_SANDBOX_SCRATCH_ROOT=/data/artifactflow/sandbox-scratch \
#   AF_SANDBOX_POOL_SIZE=80G \
#   deploy/scripts/fleet.sh prepare-sandbox "$BUNDLE"

# 6. 启动。fleet 会校验 bundle、解 config/deploy、load 镜像、跑 release gate、等待健康。
#    启用沙盒时在命令前追加 AF_ENABLE_SANDBOX=1。
AF_BUNDLE_VERSION="$VERSION" deploy/scripts/fleet.sh deploy "$BUNDLE"

# 7. 创建管理员
docker compose -f deploy/docker-compose.intranet.yml exec backend \
  python scripts/create_admin.py admin --password <your-password>
```

### 扩缩容与多机发布（fleet.sh）

`deploy/scripts/fleet.sh` 是从「一台机器」到「多台机器」的统一发布入口，把上面「校验 + 解包 + 加载 + 启动 + 探活」
收成一条命令，并原生支持 `--scale`：

```bash
deploy/scripts/fleet.sh init-local           # 生成单机 fleet.conf，并从模板种 deploy/.env
deploy/scripts/fleet.sh preflight            # 单机/每台机器就绪检查
deploy/scripts/fleet.sh deploy <bundle-dir>  # 校验 → 解包 → load → release 门 → up → 探活
deploy/scripts/fleet.sh deploy --dry-run <d> # 只打印计划，不改动任何东西
deploy/scripts/fleet.sh prepare-sandbox <d>  # 从 bundle 单独准备 runsc/sandbox/verify
deploy/scripts/fleet.sh status               # 各机器 compose ps + LB 健康
deploy/scripts/fleet.sh rollback             # 回退到上一个成功版本
```

计划性更新 `config/`、`deploy/` 或 backend/frontend 镜像时，主路径都是重新生成
release bundle 后跑 `fleet deploy <bundle-dir>`。单机路径会从 bundle 解出
`artifactflow-{config,deploy}-*.tar.gz`，加载 `artifactflow-app-*.tar.gz` 里的
backend/frontend 镜像，再跑 release gate 和 compose up；下面的手工命令只用于现场热修。

拓扑写在 `deploy/fleet.conf`（从 `fleet.conf.example` 复制，gitignored），单机场景下四个角色
（`infra`/`release`/`app`/`lb`）都填 `local`；`app` 行的 `scale=N` 就是 `--scale backend=N`
的等价物。完整命令、多机时序、以及 TLS 证书自动兜底（`up` 前自动跑
`ensure-cert.sh`），见 [`deploy/FLEET.md`](https://github.com/Neutrino1998/artifact-flow/blob/main/deploy/FLEET.md)。

> **现状：** 单机路径（含 `--scale`）已跑通并推荐日常使用；**多机路径目前 gated off**
> （`fleet.sh` 里显式 `die`，只有 `--dry-run` 能打印计划），要等第二台机器上跑通验收后才解封——
> 详见 FLEET.md「Multi-host: unexercised seams」。多机到位前，扩容仍只能在单机上
> `--scale`；跨机负载均衡 / 多机数据库 URL 等留在文档里作为**已设计未验证**的路径。
>
> `fleet.sh` 默认只包装 `deploy/docker-compose.intranet.yml`（Mode 3 基础栈）。
> 如果 bundle 带 sandbox 且要启用 `bash` / `mount` / `persist`，设置
> `AF_ENABLE_SANDBOX=1`；但宿主前置是 root 级操作，需先显式运行
> `sudo env ... deploy/scripts/fleet.sh prepare-sandbox <bundle-dir>`，再 deploy。
> deploy/preflight 会追加 `deploy/docker-compose.sandbox.yml` overlay，并把 runsc /
> sandbox 镜像 / scratch root 缺失视为 blocker。
> Mode 2（公网）扩缩容仍用上面 Mode 2A「扩缩容」小节里的裸 `--scale` +
> `deploy-prod.sh`。
>
> `fleet.sh deploy` 是直接 `up`（数秒 blip，无维护页）；要维护页包住整个升级窗口，
> 在 deploy 前后用 `maintenance.sh on|off` 包住即可。

### 滚动更新已有部署

新版本到位后，`fleet deploy` 接管校验、解包、加载镜像、起服务和探活；需要维护页窗口时，用 `maintenance.sh on|off` 包住它。

```bash
VERSION=1.0.1
BUNDLE=/root/workspace/tmp/$VERSION
APP=/root/workspace/artifactflow

# 在内网机（假设新 release 文件已通过现场介质放到 $BUNDLE/ 下，typically 不含 infra）
cd "$APP"

# 1. 校验（不影响在跑容器，可在维护开始前做）
./deploy/scripts/verify-bundle.sh "$BUNDLE"    # 一次性校验 bundle 下所有 tar

# 1b. 自举新版 deploy 脚本。旧版 fleet.sh 不知道如何从 bundle 解 deploy/config，
#     所以调用 fleet deploy 前，必须先让新版 fleet.sh 落盘。
tar xzf "$BUNDLE/artifactflow-deploy-$VERSION.tar.gz"

# 如本次 bundle 带 sandbox 且要刷新 sandbox 前置，先以 root 执行：
# 32 路沙盒按默认 2G workspace 估算应准备 80G 级别池子。
# sudo env AF_BUNDLE_VERSION="$VERSION" \
#   AF_SANDBOX_POOL=/data/artifactflow/sandbox-pool.img \
#   AF_SANDBOX_SCRATCH_ROOT=/data/artifactflow/sandbox-scratch \
#   AF_SANDBOX_POOL_SIZE=80G \
#   ./deploy/scripts/fleet.sh prepare-sandbox "$BUNDLE"

# 2. 进维护窗口（可选；fleet deploy 本身是直接 up）
./deploy/scripts/maintenance.sh on "升级到 $VERSION"

# 3. fleet 接管解包 + docker load + compose up + 健康检查；成功后关闭维护页
#    启用沙盒时在命令前追加 AF_ENABLE_SANDBOX=1。
AF_BUNDLE_VERSION="$VERSION" ./deploy/scripts/fleet.sh deploy "$BUNDLE" && \
  ./deploy/scripts/maintenance.sh off
```

> **nginx→Caddy 一次性切换（既有内网部署首次升到 Caddy 版）：** 新 compose 里反向代理服务从 `nginx` 换成了 `caddy`，`up -d` 不会自动移除旧服务的容器。切换步骤：证书就位（`deploy/certs/server.crt` + `server.key`，完整链，见该目录 README）→ 常规 prep（verify-bundle / docker load / tar xzf deploy+config）→ `docker compose -f deploy/docker-compose.intranet.yml --profile infra down` → `AF_VERSION=<版本> ... up -d`（infra 服务定义变更走 down/up，不走 pause/resume）。防火墙记得放行 HTTPS 端口（默认 443，`AF_HTTPS_PORT` 可改）。切换后旧的 `nginx:*` 镜像可 `docker rmi` 回收。

> **涉及 compose infra config 变更的升级（罕见）：** 多数升级只改 backend / frontend 镜像和它们用到的 `ARTIFACTFLOW_*` env，`pause/resume` 已覆盖（resume.sh `up backend frontend` → compose 自动 diff config-hash → 改了就 recreate）。但若本版本动了 `postgres` / `redis` / `caddy` 服务块的 HostConfig 字段（`image` / `logging` / `mem_limit` / `volumes` / `ports` / `cap_add` / `command`），`deploy/caddy/` 下的配置，或 `.env` 里 `AF_HTTP_PORT` / `AF_HTTPS_PORT`（caddy `ports:` interpolation），resume.sh 不触碰 infra 容器，新配置永远不生效。**前提**：先按上面常规流程完成 `verify-bundle.sh` + `docker load` + `tar xzf deploy/config`，让新 compose + 新 Caddyfile 就位，再进入下面两个时机（否则 recreate 用的是旧 compose / 旧 Caddyfile）：
>
> **(a) caddy 块 / Caddyfile / `AF_HTTP(S)_PORT` 变了 → 在 `pause.sh` 之前 recreate**：
> ```bash
> docker compose -f deploy/docker-compose.intranet.yml --profile infra \
>     up -d --force-recreate --no-deps caddy
> ```
> 接受 1–2 秒 caddy 重启的连接 RST（可由维护窗口公告吸收）。放 pause 之前只是流程简单（维护页全程可用）；Caddy 按请求时解析 upstream，backend 停止时启动也不会崩，没有旧 nginx 静态 upstream 的顺序硬约束。仅改 `deploy/caddy/` 配置内容（不动 compose 字段）时也可以用零停机的 `docker compose ... exec caddy caddy reload --config /etc/caddy/conf/Caddyfile.intranet --adapter caddyfile` 代替 recreate。
>
> **(b) postgres 或 redis 块变了 → 在 `pause` 与 `resume` 之间 recreate**：
> ```bash
> docker compose -f deploy/docker-compose.intranet.yml --profile infra \
>     up -d --force-recreate --no-deps <postgres redis 中实际变了的>
> ```
> 此时 backend/frontend 已 stop，无活跃应用连接，recreate 干净。
>
> ⚠️ **`POSTGRES_*` 不属于本流程**：`POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` 是 init-only —— `postgres:16-alpine` 的 entrypoint 只在 `postgres_data` 卷**为空**时用这些 env 跑 `initdb`，已有卷的情况下完全忽略。改 .env 里这些值然后 recreate postgres，**库内用户/密码不会变**：backend 会用 `DATABASE_URL` 里的新密码、PG 库内仍是旧密码 → 认证失败 → backend 起不来（`pg_isready` 不做认证 → PG healthy 但 backend 连不上，故障表现更隐蔽）。旋转密码 / 改用户的正确流程：连进 PG 跑 `ALTER USER artifactflow WITH PASSWORD '...';`（或 `CREATE USER` / `CREATE DATABASE`），改完同步 `.env` 里 `ARTIFACTFLOW_DATABASE_URL`（backend 真正用的值）和 `POSTGRES_PASSWORD`（仅作 .env 文档，给未来 fresh-init 用），然后走 pause/resume（无需 infra recreate）。`POSTGRES_*` 真正生效的场景只有清空数据卷重新 init（生产几乎不会做）。
>
> 两条 recreate 都必须 `--no-deps`：不加 compose 会顺手把 backend/frontend 也起来（caddy `depends_on` 它俩），违反前提，且会用 `${AF_VERSION:-latest}` 拉镜像（内网通常没有 `latest` tag）。数据安全：named volume 在 recreate 中不动；PG 走 crash recovery 启动（5–15s），Redis 控制状态全是 TTL key 应用层自愈，caddy 无状态（静态证书在 bind-mount 目录，不在容器里）。
>
> **`.env` 变量归属(`.env.{intranet,prod}.example` 头注释也复述了同样规则)**：
>
> | 变量 | 走哪条路径 |
> |---|---|
> | `ARTIFACTFLOW_*`（JWT / DATABASE_URL / REDIS_URL / MAX_CONCURRENT_TASKS / API keys 等） | 常规 pause/resume（resume.sh up backend → compose 检测 env_file 变化 → recreate backend） |
> | `AF_HTTP_PORT` / `AF_HTTPS_PORT`（caddy `ports:` interpolation；后者兼作 HTTP→HTTPS 跳转目标端口） | 上面 (a) —— caddy pre-pause force-recreate |
> | `POSTGRES_*` | 见上方 ⚠ 块 —— **不能**走 recreate，必须 SQL `ALTER USER` |
> | `AF_VERSION` | `resume.sh <VERSION>` 显式传入即可 |
>
> **验证 HostConfig 已生效**（recreate 完毕后、resume 之前；容器名按当前 compose project 实际命名，默认 `artifactflow-<svc>-1`）：
>
> ```bash
> for s in caddy backend frontend postgres redis; do
>   echo "--- $s ---"
>   docker inspect artifactflow-${s}-1 --format \
>     '{{.HostConfig.LogConfig.Type}} {{.HostConfig.LogConfig.Config}} mem={{.HostConfig.Memory}}'
> done
> # 期望 LogConfig.Type=json-file，Config 含 max-size:100m / max-file:3；
> # Memory（字节）：caddy=0 / backend=2147483648 / frontend=1073741824 /
> # postgres=2147483648 / redis=805306368
> ```

### 运行时配置变更（无需 rebuild / 重新传镜像）

正式发布的 `config/` 变更应先在构建机改好、提交、再 release；`fleet deploy`
会从 bundle 覆盖目标机上的 `config/`。下表适合现场临时热修，下一次 deploy 会被
bundle 内容覆盖。

| 变更类型 | 操作 | 生效命令 |
|---------|------|---------|
| `config/models/models.yaml`（模型 / base_url） | 直接编辑宿主机文件 | `docker compose -f deploy/docker-compose.intranet.yml restart backend` |
| `config/agents/*.md` / `config/tools/*.md` / `config/skills/`（DB seeded registry） | 推荐重新打 config release 并 `fleet deploy`；紧急热修才直接编辑宿主机文件 | 跑一次 release/reconcile gate 后再重启 backend 进程；compose flags 必须与当前部署一致（启用沙盒时也带 `-f deploy/docker-compose.sandbox.yml`） |
| `config/site/notifications.json`（左栏通知） | 管理员菜单「通知管理」或直接编辑宿主机文件，schema 见 `config/site/README.md` | **无需 restart** — backend 只写 `config/site` 挂载目录，frontend 只读服务同一目录；前端 60s 轮询自动重拉（标签回前台时立即重拉） |
| `config/site/welcome_tips.json` / `branding.json`（欢迎页提示 / 版权页脚） | 直接编辑宿主机文件；`branding.json` 首次启用需 `cp branding.example.json branding.json` 再填值（仓库 `.gitignore` 排除真实文件） | **无需 restart**，但**只在挂载时拉一次、不轮询**——改完需用户刷新页面才看到。文件缺失 / schema 错位 → 对应 UI 自动隐藏或回落默认（fail-closed）。 |
| `deploy/.env`（任何 `ARTIFACTFLOW_*` 变量） | 直接编辑 | 用当前 compose flags 执行 `AF_VERSION="$VERSION" ... up -d backend`（restart 不会重读 .env，up 会检测 env 变化重建容器） |
| `deploy/caddy/`（Caddyfile.intranet / common.caddy） | 直接编辑（或 tar 覆盖） | `docker compose -f deploy/docker-compose.intranet.yml exec caddy caddy reload --config /etc/caddy/conf/Caddyfile.intranet --adapter caddyfile`（零停机；`restart caddy` 也行） |
| `deploy/certs/`（换证书） | 覆盖 `server.crt` / `server.key` | 同上 — `caddy reload` |
| `deploy/docker-compose.intranet.yml`（端口、profile 等） | 直接编辑 | `up -d` |

> **关键区别：** `models.yaml` 是 backend 进程直接读文件，改完 restart backend；
> agents/tools/skills 会 reconcile 进 DB registry，改文件后必须重新跑 release/reconcile
> gate。改 `.env` 用 `up -d`（让 compose 重建容器注入环境变量）；手动
> `run release` / `up -d backend` 都必须显式传当前 `AF_VERSION`，否则 compose 会回落到
> `${AF_VERSION:-latest}`。`config/site/` 是例外：同一个宿主目录只读挂在 frontend、
> 可写挂在 backend 的通知管理入口，无需重启；其中 `notifications.json` 前端轮询
> 自动重拉，`welcome_tips.json` / `branding.json` 只在页面加载时读取，运维改完需用户刷新。

紧急热修 seeded registry 的命令形状如下；`VERSION` 填当前正在运行的镜像版本。
Mode 3A（本机 PG/Redis）才追加 `--profile infra`，Mode 3B（外部 PG/Redis）不要加。
沙盒部署还要把 `COMPOSE` 加上 `-f deploy/docker-compose.sandbox.yml`：

```bash
VERSION=2026.07.09-intranet.1  # current running version
COMPOSE="docker compose -f deploy/docker-compose.intranet.yml"
# Mode 3A only:
# COMPOSE="$COMPOSE --profile infra"
# Sandbox deployment only:
# COMPOSE="$COMPOSE -f deploy/docker-compose.sandbox.yml"

AF_VERSION="$VERSION" $COMPOSE run --rm release
AF_VERSION="$VERSION" $COMPOSE restart backend
```

### 仅推送 config 更新（不动镜像）

推荐仍然走 release bundle + fleet，让 `config/` 的来源、校验和版本记录保持一致。
如果只是现场临时改 `config/models/models.yaml` 这类文件，可以按上表直接重启
backend；如果要把配置变更固化成一次发布，按下面流程。

```bash
# 构建机：轻量手工包 config + deploy。
# deploy tar 用来让目标机先自举到新版 fleet/verify 脚本。
VERSION=1.0.1
tar czf artifactflow-config-1.0.1.tar.gz config/
tar czf artifactflow-deploy-1.0.1.tar.gz deploy/
sha256sum artifactflow-config-1.0.1.tar.gz > artifactflow-config-1.0.1.tar.gz.sha256
sha256sum artifactflow-deploy-1.0.1.tar.gz > artifactflow-deploy-1.0.1.tar.gz.sha256
```

目标机：

```bash
VERSION=1.0.1
BUNDLE=/root/workspace/tmp/$VERSION
APP=/root/workspace/artifactflow

cd "$APP"
tar xzf "$BUNDLE/artifactflow-deploy-$VERSION.tar.gz"
deploy/scripts/verify-bundle.sh "$BUNDLE"
tar xzf "$BUNDLE/artifactflow-config-$VERSION.tar.gz" -C "$APP"

# models.yaml 只需 backend 进程重读；agents/tools/skills 需要跑 release/reconcile gate。
docker compose -f deploy/docker-compose.intranet.yml restart backend
```

> 如果这次 config-only bundle 改了 `config/agents/`、`config/tools/` 或
> `config/skills/`，用上方「运行时配置变更」里的 release/reconcile gate 命令替代
> 直接 restart。只改 `config/site/*.json` 时不需要任何 docker 命令；其中
> `notifications.json` 前端 60s 轮询自己生效，`welcome_tips.json` /
> `branding.json` 只在挂载时拉一次，需要用户刷新页面才看到。

### 更新 backend / frontend 镜像（app tar）

后端代码或前端代码变化时重新打 app release。通常不需要重传 infra；如果没有改
sandbox 镜像，也不需要重传 sandbox/gVisor。

```bash
# 构建机
VERSION=1.0.1
./scripts/release.sh "$VERSION" --platform linux/arm64
```

把这些文件放到目标机 `$BUNDLE/`：

```text
artifactflow-app-$VERSION.tar.gz{,.sha256}
artifactflow-config-$VERSION.tar.gz{,.sha256}
artifactflow-deploy-$VERSION.tar.gz{,.sha256}
artifactflow-$VERSION.manifest.txt
```

目标机：

```bash
VERSION=1.0.1
BUNDLE=/root/workspace/tmp/$VERSION
APP=/root/workspace/artifactflow

cd "$APP"
tar xzf "$BUNDLE/artifactflow-deploy-$VERSION.tar.gz"
deploy/scripts/verify-bundle.sh "$BUNDLE"

# 启用沙盒时在命令前追加 AF_ENABLE_SANDBOX=1。
AF_BUNDLE_VERSION="$VERSION" deploy/scripts/fleet.sh deploy "$BUNDLE"
```

`fleet deploy` 会解出新的 `config/` / `deploy/`、`docker load` 新 backend +
frontend 镜像、跑一次 release gate，然后按 `deploy/fleet.conf` 里的 `scale=N`
重新 `up`。这是更新 backend/frontend 镜像的主路径。

---

## 沙盒执行环境（可选 overlay）

`bash` / `mount` / `persist` 三个沙盒工具需要宿主侧前置 + 一个 compose overlay。**没有沙盒需求的部署跳过本节**——基础 compose 不挂 `docker.sock`，没有这个暴露面。架构与全部 `SANDBOX_*` 旋钮见 [架构 · 沙盒执行](architecture/sandbox.md)。

随发布包额外携带三个传输单元（与应用包同一构建机产出）：**沙盒镜像** tar（`scripts/build-sandbox-image.sh`，注意按目标机选 `PLATFORM`）、**verify 探针** tar（同脚本产出，arch 无关）、**gVisor 包** tar（`sandbox/gvisor-pkg/fetch-and-package.sh`）。

### 宿主前置（一次性）

先显式运行 `prepare-sandbox` 从 release bundle 完成宿主前置。这个步骤会安装/注册
runsc、写 `/etc/fstab` 并挂载 scratch loop，所以需要 root；排障时也可以直接跑
同一套底层动作（安装 gVisor、加载 sandbox 镜像、
创建 scratch loop、跑 smoke/verify，并把 `ARTIFACTFLOW_SANDBOX_SCRATCH_ROOT` /
`ARTIFACTFLOW_SANDBOX_RUNTIME` 写入 `deploy/.env`）：

```bash
# 默认 scratch pool 为 8G starter；可按并发 × ARTIFACTFLOW_SANDBOX_WORKSPACE_QUOTA_MB 调大。
# 例：32 路都可能跑沙盒且 workspace quota 默认 2G 时，用 80G 级别池子更稳。
sudo env AF_SANDBOX_POOL_SIZE=80G deploy/scripts/fleet.sh prepare-sandbox .
# 或直接：
# sudo env AF_SANDBOX_POOL_SIZE=80G deploy/scripts/prepare-host.sh sandbox
```

它等价于以下手工步骤，保留在这里便于排障：

1. **gVisor（runsc）**：解开 gVisor 包，`sudo ./install.sh && sudo systemctl reload docker && sudo ./smoke-test.sh`（内含 `unshare -U` 预检）。arm / 鲲鹏注意：Kylin V10 arm 默认 64K 页内核，gVisor 拒启——先用 `sandbox/kernel-4k-pkg/` 换 4K 内核再装（x86 跳过）。
2. **沙盒镜像**：`gunzip -c artifactflow-sandbox-<ver>-<arch>.tar.gz | docker load`。
3. **scratch 根 = 定容 loop 文件系统**（磁盘配额的硬墙层：watchdog race 窗口内写穿也只是池子满，宿主盘无恙；独立 inode 表顺带兜住海量小文件）：

   ```bash
   POOL=/var/lib/artifactflow/sandbox-pool.img
   ROOT=/var/lib/artifactflow/sandbox-scratch
   sudo mkdir -p "$(dirname "$POOL")" "$ROOT"
   sudo fallocate -l 80G "$POOL"          # 容量 ≈ 并发 turn 数 × SANDBOX_WORKSPACE_QUOTA_MB(默认2G) + 余量
   sudo mkfs.ext4 -m 0 "$POOL"
   echo "$POOL $ROOT ext4 loop,nosuid,nodev 0 0" | sudo tee -a /etc/fstab
   sudo mount "$ROOT" && df -h "$ROOT"    # 期望:定容 ext4 挂载成功
   ```

   不加 `noexec`——模型在工作区 `chmod +x` 后直接执行脚本是合法用法。
4. **验证**：`tar xzf artifactflow-sandbox-verify-<ver>.tar.gz`，`IMAGE=artifactflow-sandbox:<ver>-<arch> bash verify/run-all.sh`，全绿后记录 manifest 里的 image id 作为本部署的冻结锚点。

### 启动

`deploy/.env` 增加（**路径必须与宿主一致**——overlay 把 scratch 根以同一绝对路径挂进 backend 容器，因为 backend 把工作区路径作为 bind source 传给 daemon、daemon 按宿主路径解析；改路径只改这一处 env，compose 两侧与应用配置同步取值）：

```bash
ARTIFACTFLOW_SANDBOX_SCRATCH_ROOT=/var/lib/artifactflow/sandbox-scratch
# ARTIFACTFLOW_SANDBOX_RUNTIME 默认 runsc(overlay 内兜底),无需显式写
```

```bash
AF_VERSION=1.0.0 docker compose -f deploy/docker-compose.intranet.yml \
                              -f deploy/docker-compose.sandbox.yml \
                              --profile infra up -d

# 使用 fleet 单机入口:
sudo env AF_SANDBOX_POOL_SIZE=80G deploy/scripts/fleet.sh prepare-sandbox .
AF_ENABLE_SANDBOX=1 deploy/scripts/fleet.sh deploy .
# 如需 deploy 前单独检查，先准备再 preflight：
# AF_ENABLE_SANDBOX=1 deploy/scripts/fleet.sh preflight
```

> **安全提醒**：overlay 把 `/var/run/docker.sock` 挂进 backend 容器（等同宿主 root）。这是 DooD 架构的固有前提，防线是代码侧纪律——容器创建参数全为 backend 常量、绝不被模型内容污染（见架构文档「隔离边界」）。不要把这个 overlay 用在不需要沙盒的部署上。

### 验证沙盒链路

前端对话里让 agent 在沙盒里跑一条命令（例如"用 bash 运行 echo ok"）：应弹出权限确认 → 批准后返回输出。turn 结束后 `docker ps -a --filter label=artifactflow.sandbox` 应无残留容器、scratch 根下无残留目录（崩溃残留由 reaper 周期回收）。

---

## 环境变量完整参考

所有应用级变量使用 `ARTIFACTFLOW_` 前缀（通过 Pydantic Settings 自动映射），定义在 `src/config.py`。

### 核心

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARTIFACTFLOW_DEBUG` | `false` | 调试模式（详细日志 + 错误信息不脱敏 + 启用 Swagger 文档） |

### JWT 认证

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARTIFACTFLOW_JWT_SECRET` | — (**必填**) | HS256 签名密钥 |
| `ARTIFACTFLOW_JWT_ALGORITHM` | `HS256` | 签名算法 |
| `ARTIFACTFLOW_JWT_EXPIRY_DAYS` | `7` | Token 有效期（天） |

### 数据库

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARTIFACTFLOW_DATABASE_URL` | — (**必填**) | 连接串，如 `sqlite+aiosqlite:///data/artifactflow.db` 或 `postgresql+asyncpg://...` |
| `ARTIFACTFLOW_DATABASE_URLS` | `""` | 逗号分隔多地址列表，启用 primary-first failover（按顺序尝试，首个可连即用）；非空时优先于 `DATABASE_URL`，所有地址必须同一 driver（MySQL 或 PostgreSQL） |
| `ARTIFACTFLOW_DATABASE_POOL_SIZE` | `10` | 连接池大小 |
| `ARTIFACTFLOW_DATABASE_MAX_OVERFLOW` | `20` | 连接池溢出上限 |
| `ARTIFACTFLOW_DATABASE_POOL_TIMEOUT` | `30` | 获取连接超时（秒） |
| `ARTIFACTFLOW_DATABASE_POOL_RECYCLE` | `300` | 连接回收周期（秒） |

### Redis

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARTIFACTFLOW_REDIS_URL` | `""` | 空 = InMemory 回退；非空 = Redis 模式 |
| `ARTIFACTFLOW_REDIS_CLUSTER` | `false` | Redis Cluster 模式 |
| `ARTIFACTFLOW_REDIS_KEY_PREFIX` | `""` | Key 命名空间前缀（启用 Redis 时**必填**） |
| `ARTIFACTFLOW_REDIS_MAX_CONNECTIONS` | `64` | 连接池上限 |
| `ARTIFACTFLOW_LEASE_TTL` | `90` | 对话租约 TTL（秒），心跳每 TTL/3 续租 |

### SSE 与执行超时

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARTIFACTFLOW_SSE_PING_INTERVAL` | `15` | 心跳间隔（秒），保持连接活跃 |
| `ARTIFACTFLOW_EXECUTION_TIMEOUT` | `1800` | 总执行上限（秒），含 permission 等待 |
| `ARTIFACTFLOW_STREAM_CLEANUP_TTL` | `60` | 执行结束后 stream 清理窗口（秒） |
| `ARTIFACTFLOW_PERMISSION_TIMEOUT` | `300` | 单次权限等待超时（秒） |

### LLM 与 MCP HTTP 分阶段超时

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARTIFACTFLOW_LLM_CONNECT_TIMEOUT` | `5` | LLM provider DNS/TCP/TLS 建连超时（秒） |
| `ARTIFACTFLOW_LLM_READ_TIMEOUT` | `600` | LLM 首个/下一个响应 chunk 等待超时（秒）；`models.yaml` 的 `params.timeout` 只覆盖此 read 值 |
| `ARTIFACTFLOW_LLM_WRITE_TIMEOUT` | `60` | LLM 请求体写入超时（秒） |
| `ARTIFACTFLOW_LLM_POOL_TIMEOUT` | `5` | LLM HTTP 连接池等待超时（秒） |
| `ARTIFACTFLOW_MCP_CONNECT_TIMEOUT` | `5` | MCP `tools/list` discovery 与 `tools/call` 的 DNS/TCP/TLS 建连超时（秒） |
| `ARTIFACTFLOW_MCP_WRITE_TIMEOUT` | `60` | MCP 请求体写入超时（秒） |
| `ARTIFACTFLOW_MCP_POOL_TIMEOUT` | `5` | MCP HTTP 连接池等待超时（秒） |

MCP 单元的现有 `provider_config.timeout`（默认 `60`）继续表示 per-server read / MCP request 等待上限。这些分阶段变量把错 IP 的建连等待与合法的长 TTFT/长任务等待拆开；流式 read timeout 表示“连续多久没有新数据”，整个 turn 仍由 `ARTIFACTFLOW_EXECUTION_TIMEOUT` 封顶。

### Compaction 与上下文

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARTIFACTFLOW_COMPACTION_TOKEN_THRESHOLD` | `100000` | 单次 LLM 调用 input+output 超此值时，引擎内立即触发 compaction |
| `ARTIFACTFLOW_COMPACTION_TIMEOUT` | `300` | 单次 compact LLM 调用的超时（秒） |
| `ARTIFACTFLOW_INVENTORY_PREVIEW_LENGTH` | `200` | Artifact 清单预览截断长度 |

> 旧版本的 `COMPACTION_PRESERVE_PAIRS` / `CONTEXT_MAX_TOKENS` / `TRUNCATION_PRESERVE_AI_MSGS` 已随异步后台 compaction 与 token-预算截断一起移除。现在引擎不做独立截断，压缩完全由上述阈值驱动（详见 [engine.md → Compaction 机制](architecture/engine.md#compaction-机制)）。

### CORS

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARTIFACTFLOW_CORS_ORIGINS` | `["http://localhost:3000"]` | 允许的跨域来源 |
| `ARTIFACTFLOW_CORS_ALLOW_CREDENTIALS` | `true` | 允许携带凭证 |
| `ARTIFACTFLOW_CORS_ALLOW_METHODS` | `["*"]` | 允许的 HTTP 方法 |
| `ARTIFACTFLOW_CORS_ALLOW_HEADERS` | `["*"]` | 允许的请求头 |

> **启动期 footgun 守卫**：`CORS_ALLOW_CREDENTIALS=true`（默认）与 `CORS_ORIGINS` 含 `"*"` **不兼容** —— Starlette 在该组合下反射请求 Origin，等于"任意站点都能读到携带凭证的响应"。命中即**拒绝启动**（`config.py` 启动校验）。要放开跨域，显式列出 origin（`ARTIFACTFLOW_CORS_ORIGINS='["https://app.example.com"]'`）；确需通配时须同时设 `ARTIFACTFLOW_CORS_ALLOW_CREDENTIALS=false`。

### 其他

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARTIFACTFLOW_MAX_CONCURRENT_TASKS` | `32` | 最大并发引擎执行数 |
| `ARTIFACTFLOW_MAX_UPLOAD_SIZE` | `209715200` | 单文件上传大小限制（字节，默认 200MB）；批量总字节由代理层独立封顶（约 200MiB 内容 + multipart 开销）。注：文本转换另有更低的独立闸 20MB——超闸**不 422、落为二进制 blob artifact**（可下载、可 mount 进沙盒处理） |
| `ARTIFACTFLOW_DEFAULT_PAGE_SIZE` | `20` | 分页默认每页条数 |
| `ARTIFACTFLOW_MAX_PAGE_SIZE` | `100` | 分页最大每页条数 |

### 舰队可观测（Phase C）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AF_VERSION` | `latest` | 镜像版本,由 compose 注入 backend、透出到实例面板(非 `ARTIFACTFLOW_` 前缀) |
| `ARTIFACTFLOW_OBS_HEARTBEAT_TTL_SEC` | `300` | 心跳 key TTL;放长于 STALE,给 wedge 实例留「在册显红」窗口 |
| `ARTIFACTFLOW_OBS_HEARTBEAT_STALE_SEC` | `60` | 心跳 `ts` 超此值 = 陈旧 → 面板红(约 2× sample 周期) |
| `ARTIFACTFLOW_OBS_ERROR_WINDOW_SEC` | `300` | 最近 ERROR / autoheal 在此窗口内 → 面板黄 |
| `ARTIFACTFLOW_OBS_AUTOHEAL_MARKER_PATH` | `` | autoheal marker 容器内路径;compose 已设为 `/app/autoheal/restart-marker.jsonl`,空=不读 |

### LLM 与工具 API Key

以下变量**不使用** `ARTIFACTFLOW_` 前缀，由 LiteLLM / 工具直接读取：

| 变量 | 说明 |
|------|------|
| `DASHSCOPE_API_KEY` | 通义千问 API（**默认模型必填**） |
| `OPENAI_API_KEY` | OpenAI API |
| `DEEPSEEK_API_KEY` | DeepSeek API |
| `BOCHA_API_KEY` | Bocha Web 搜索 |
| `JINA_API_KEY` | Jina Reader（网页抓取） |

### 启动校验规则

应用启动时会验证以下条件，不满足则拒绝启动：

1. `ARTIFACTFLOW_JWT_SECRET` 必须设置
2. `ARTIFACTFLOW_DATABASE_URL` 或 `ARTIFACTFLOW_DATABASE_URLS` 必须设置
3. 启用 Redis（`ARTIFACTFLOW_REDIS_URL` 非空）时，`ARTIFACTFLOW_REDIS_KEY_PREFIX` 必须设置
4. `ARTIFACTFLOW_CORS_ALLOW_CREDENTIALS=true` 时 `ARTIFACTFLOW_CORS_ORIGINS` 不得含 `"*"`（见上方 [CORS](#cors) footgun 守卫）

---

## 容量规划

`src/config.py` 的代码默认值已按单 backend 32 路执行做了一组稳妥起点：`MAX_CONCURRENT_TASKS=32`、`DATABASE_POOL_SIZE=10`、`DATABASE_MAX_OVERFLOW=20`、`REDIS_MAX_CONNECTIONS=64`。这能吃满中等模型 API 配额，同时不会像 64 路默认那样把小型 DB / 内网宿主的资源承诺拉得过猛。

`deploy/.env.intranet.example` 和 `deploy/.env.prod.example` 也显式写入这组 32 路默认，下面是这组值的依据，便于按你自己的模型 API 配额线性缩放。

### 三个互相绑定的旋钮

```
模型 API 并发预算 (默认 32)
        │
        ▼
ARTIFACTFLOW_MAX_CONCURRENT_TASKS  ← 单 backend 引擎执行 Semaphore (src/api/services/execution_runner.py)
        │
        ├─► ARTIFACTFLOW_DATABASE_POOL_SIZE + MAX_OVERFLOW
        │     单 backend DB 连接上限，每个执行 post-process 期间短暂占 1–2 连接
        │     建议 ≈ MAX_CONCURRENT_TASKS（含 overflow），余量留给后台任务/调试
        │
        └─► ARTIFACTFLOW_REDIS_MAX_CONNECTIONS
              建议 ≈ 2× MAX_CONCURRENT_TASKS（runtime store + stream consumer + pub/sub）
```

如果模型 API 并发不是 32，把以上三个值按比例缩放即可；例如 64 路可用 `MAX_CONCURRENT_TASKS=64`、`REDIS_MAX_CONNECTIONS=128`，并在 DB 容量允许时把池上限抬到 60 左右。

### Mode A vs Mode B：DB 池的安全边界

模板里 DB 池两行（`POOL_SIZE` / `MAX_OVERFLOW`）**默认是注释掉的**，这是有意的：

| 部署形态 | DB 来源 | `max_connections` 上限 | DB 池处理 |
|---|---|---|---|
| Mode 2A / 3A（`--profile infra`） | 捆绑的 `postgres` 容器 | `200`（compose `command:` 显式设置） | 默认 10+20；64 路时可提高到 20+40 |
| Mode 2B（云托管 DB） | 外部 RDS / Aurora 等 | 由托管层级决定（小规格 ~85–150） | 按单 backend 池上限 × backend 副本数核算 |
| Mode 3B（内部企业 DB，无 `--profile infra`） | 公司内部 DB | 由 DBA 配置 | 同上 —— 由内部 DB 容量决定 |

详见 [`deploy/docker-compose.intranet.yml`](https://github.com/Neutrino1998/artifact-flow/blob/main/deploy/docker-compose.intranet.yml) 和 [`docker-compose.prod.yml`](https://github.com/Neutrino1998/artifact-flow/blob/main/docker-compose.prod.yml) 中 `postgres` 服务的 `command: postgres -c max_connections=200 -c shared_buffers=256MB`，**这条 patch 仅在 `--profile infra` 启动时生效**。

### Redis 内存预算

Mode A 的 compose 把 Redis `--maxmemory` 从默认 `256mb` 提到 **`512mb`**，并保留 `--maxmemory-policy noeviction`。

**容量估算**（基于实测，单 message 平均 ~500 KB、重负载 ~1 MB）：

| 并发 | 典型峰值 | 重负载峰值 |
|---|---|---|
| 32（代码默认） | 15–30 MB | ~65 MB |
| 64（高并发配置） | 30–60 MB | ~130 MB |

512 MB 对 32 路默认非常宽松，也覆盖 64 路配置的既有估算。如果你的模型并发 > 64 或自定义工具单条输出可能很大（>100 KB），按比例抬 `maxmemory`。

**为什么是 `noeviction` 而不是 `volatile-lru`**：所有 Redis key（lease / interrupt / cancel / queue / stream / stream_meta）都带 TTL 但承载在飞任务的关键控制状态。Redis 驱逐策略**以 key 为粒度**，LRU 类策略会随机删整个 lease/interrupt key，让 `consume_events` 误判 producer 掉线、cancel/interrupt 信号无声丢失 —— 表现为"任务随机被杀"。`noeviction` 在内存满时**显式写失败**，让运维拿到清晰信号去扩容。Stream 内的 entry 修剪由 `XADD MAXLEN ~ 1000` 在生产端处理，与 maxmemory 策略无关。

### 容器内存上限

两个 compose 文件都加了 `mem_limit`：

| 服务 | `mem_limit` | 说明 |
|---|---|---|
| `backend` | `2g` | FastAPI + LiteLLM + ML SDK 进程，避免单 backend OOM 拖整机 |
| `frontend` | `1g` | Next.js standalone，防内存爬升的 tripwire（稳态远低于上限） |
| `postgres` | `2g` | `shared_buffers=256MB` + `max_connections=200` × per-connection `work_mem` + 内核 buffer 余量 |
| `redis` | `768m` | Redis maxmemory 512m + AOF rewrite/RDB fork 余量 |

`mem_limit` 在这里**是 tripwire 不是分配额度**（loud-failure 原则，与上一节 Redis `noeviction` 同一思路）：触上限 → OOM kill → 容器 restart → 运维收到告警，而不是悄无声息吃满 host 把同机服务一起拖死。Postgres `2g` 的稳态保守估算 `256MB shared_buffers + 200 connection × 典型 work_mem + 内核 buffer ≈ 1.2–1.5g`，留 ~30% safety margin。若 p99 RSS 长期超过 1.5g，先排查异常（慢查询/连接泄漏）再谈调参，不要直接放宽。

反向代理（Caddy）不设 `mem_limit`：长期 < 50 MB，加了纯属冗余。

### 沙盒并发预算

`MAX_CONCURRENT_TASKS=32` 并不等于 32 个沙盒常驻；只有实际调用 `bash` / `mount` / `persist` 的 turn 才会启动沙盒容器。但如果 workload 里大量任务都会跑沙盒，需要单独按最坏情况做容量预算：

| 项 | 默认 | 32 路全跑沙盒的含义 |
|---|---|---|
| `SANDBOX_MEM_LIMIT_MB` | `1024` | 最坏约 32GB 容器内存上限承诺；32GB 宿主应降低并发、降低沙盒内存或避免全量沙盒 workload |
| `SANDBOX_WORKSPACE_QUOTA_MB` | `2048` | 最坏约 64GB scratch 数据；8G pool 只是 starter，32 路建议 80G 级别 |
| `SANDBOX_CPU_LIMIT` | `1.0` | 最坏 32 vCPU 配额；16 核机器会靠调度分时，适合 I/O/LLM 等待型任务，不适合 32 路纯 CPU 文档转换 |

结论：默认 1GB 沙盒内存不建议再加，真正要跟着并发调的是 `AF_SANDBOX_POOL_SIZE` 和是否允许这么多任务同时使用沙盒。

---

## 运维参考

### 数据库迁移

**两条路径，`docker-compose.prod.yml` / `deploy/docker-compose.intranet.yml` 默认走前者：**

- **Release-vs-serve 拆分（Mode 2/3 默认）：** 一次性 `release` 服务（`entrypoint.sh release`）单独跑迁移 + reconcile 后退出；backend 带 `AF_SKIP_RELEASE=1`，靠 compose `depends_on: { release: { condition: service_completed_successfully } }` 等它退出，**不再自己抢锁**——多副本时所有 backend 走同一条"等 release 退出"的依赖关系，下面 Leader/Follower 抢锁的分支在这条路径下根本不会进入（backend 直接跳到"继续启动"）。详见 [`deploy/MULTI-REPLICA.md`](https://github.com/Neutrino1998/artifact-flow/blob/main/deploy/MULTI-REPLICA.md)。
- **Inline 自迁移（Mode 1 / 向后兼容 fallback）：** 不设 `AF_SKIP_RELEASE` 时的默认行为——容器启动时自己判断数据库类型、按下面这套逻辑决出 leader/follower。SQLite 单副本走这条（`release` 拆分只对 PG/MySQL 有意义）。

```mermaid
flowchart TD
    Start[容器启动] --> Check{数据库类型?}
    Check -->|SQLite / 未配置| Skip[跳过迁移]
    Check -->|PostgreSQL / MySQL| Lock{获取 PG advisory lock}

    Lock -->|获取成功 Leader| Migrate[执行 alembic upgrade head]
    Lock -->|获取失败 Follower| Wait[等待 lock 释放]

    Migrate -->|成功| Release[释放 lock]
    Migrate -->|失败| Exit1[退出, 不释放 lock]

    Wait --> Verify{验证 schema at head?}
    Verify -->|是| Continue[继续启动]
    Verify -->|否| Exit2[退出 Leader 迁移失败]

    Release --> Continue
    Skip --> Continue
    Continue --> Server[启动服务]
```

> 上图是 `run_release()` 的迁移执行逻辑本身（`entrypoint.sh` 内的共享函数）——Release-vs-serve 路径下，一次性 `release` 服务只会走到 Leader 分支（没有其他副本在抢同一把锁）；Follower 分支只在 inline fallback 下、多个 backend 同时自迁移时才会触发。

- **失败处理：** Leader 迁移失败后不释放 lock（连接关闭自动释放），Follower 检测到 schema 未到 head 后退出，容器 restart policy 会重试
- **Fallback：** 如果 advisory lock 不可用（如 MySQL），直接执行 `alembic upgrade head`

### 反向代理配置

两种部署统一用 Caddy。共性机制（SSE 不缓冲 `flush_interval -1`、挡 Swagger `/docs|/redoc|/openapi.json`→404、维护页 `file` matcher 每请求 stat `MAINTENANCE_ON`、真实 IP `header_up X-Real-IP {remote_host}`、上传总量闸 `request_body max_size 210MiB`、内部健康监听 `:2021`）全部住在共享片段 `deploy/caddy/common.caddy`，只写一遍；入口文件只差 TLS 姿态：

| 维度 | **Mode 2（公网）** | **Mode 3（内网）** |
|---|---|---|
| 入口文件 | `deploy/caddy/Caddyfile` | `deploy/caddy/Caddyfile.intranet` |
| TLS | **自动 HTTPS**（Let's Encrypt / ACME，端口 80+443；证书状态在 `caddy_data` 卷） | **静态证书**（`tls /etc/caddy/certs/server.crt server.key`，全局 `auto_https off` 防 ACME 拨号；证书在 bind-mount 目录，无状态卷）。证书缺文件时 Caddy 起不来——真证书没到位可用 `deploy/scripts/ensure-cert.sh` 生成自签占位证书先顶着（幂等、不覆盖真证书） |
| HTTP :80 | ACME 验证 + 自动跳 https（Caddy 内建） | 显式 `redir` 到 `https://{host}:{$AF_HTTPS_PORT}`（`auto_https off` 关掉了内建跳转） |
| 端口 | 80/443 固定（协议要求） | `AF_HTTP_PORT`（默认 80）/ `AF_HTTPS_PORT`（默认 443）可改 |

- **上传上限（代理层是总量权威闸）**：`POST /api/v1/chat` 把整批附件放进**一个** multipart 请求，body 是整批之和。三轴**独立**：单文件 ≤200MB（`MAX_UPLOAD_SIZE`，后端 422）、数量 ≤30（`MAX_CHAT_ATTACHMENTS`，后端 422）、**总量约 200MiB 内容 + multipart 开销（代理层 413）**。总量**刻意小于** per-file×count（200MB×30=6GB）——设计意图是"1 个大文件 or 多个小文件，但控总量"，故大批量时代理层**会按设计抢先** 413（单个超大文件仍由后端给干净 422）。`210MiB` = 200MiB 内容 + 10MiB multipart 开销，所以单个满额文件可以穿过代理交给后端判定。**单位注意**：Caddy 的 `MB` 是 decimal 10⁶、`MiB` 才是二进制 2²⁰——写 `210MB` 会把 200MiB 批次的开销预算从 10MiB 压到 ~285KB，必须写 `MiB`。另：文本转换路径有更低的独立闸 `MAX_TEXT_CONVERT_BYTES`（20MB，防解码+词表物化的内存放大），blob 路径（图片/PDF/docx/其它二进制）不受此限；**超文本闸不 422**，文件落为二进制 blob artifact（可下载、可 mount 进沙盒处理）。
- **`X-Real-IP`（登录频控依赖）**：后端 per-IP 登录频控**只读这个头**（刻意不信可被客户端伪造的 `X-Forwarded-For`）。安全前提是 backend 仅 `expose`、不发布主机端口，只经反向代理可达，故这个头不可伪造。删掉它 / 换不写该头的代理 → per-IP 限流静默退化成"所有请求共用代理容器一个 IP 桶"（per-username 主防线仍在）。
  - **Mode 2 灰云（CF DNS only）直连**：`{remote_host}` 就是真实客户端 IP，直接写进 `X-Real-IP`，不退化。
  - **Mode 2 若改用 CF 橙云（proxied）**：真实客户端 IP 移到 `X-Forwarded-For`、`{remote_host}` 变成 CF 边缘 IP —— 需在 Caddyfile 加 `trusted_proxies`（CF IP 段）并改用 `{client_ip}`，否则 per-IP 限流退化。`deploy/caddy/Caddyfile` 头部注释了这一点。
- **`--scale` 支持**：backend 走 `dynamic a` 动态 upstream（按 DNS 刷新把每个副本 IP 当独立 upstream + `round_robin`）——直接写 `reverse_proxy backend:8000` 会经 keepalive 连接池钉死单副本，实测 12/12 同实例。wedge 副本摘除是**被动**的（主动健康检查不作用于 dynamic upstream）：分路径 `response_header_timeout` + 失败重试 + `fail_duration` 记忆，caddy 容器自身的 healthcheck 打 `:2021` 兼作探针。机制详注在 `deploy/caddy/common.caddy`。frontend 单副本，保持静态 upstream。

### 维护模式（无停机更新窗口）

适用于 **Mode 2（公网）** 和 **Mode 3（内网）**（都是 Caddy 入口）。Mode 1 无反向代理，不适用。

**脚本分两套，机制完全相同（共享 `deploy/scripts/_maint_lib.sh`，含同一个 resume 健康探针），只差 compose 文件：**

| | Mode 2（公网） | Mode 3（内网） |
|---|---|---|
| 进维护窗口 | `pause-prod.sh` | `pause.sh` |
| 退维护窗口 | `resume-prod.sh` | `resume.sh` |
| compose 文件 | `docker-compose.prod.yml` | `deploy/docker-compose.intranet.yml` |
| resume 健康探针 | 共用：caddy 容器内经 Caddy 内部端口 `wget localhost:2021/health/ready`（真正过 Caddy 反代 → 验证配置已加载 + Caddy→backend 通；该端口不发布到宿主机，避开 TLS-on-localhost 域名不匹配） | 同左（`_maint_lib.sh` 默认实现） |

**两层接口：** 镜像升级（典型场景）用 `pause*.sh` / `resume*.sh`，封装"维护页 + 停服务 → 起新版本 + 关维护页"全套；config-only 改动不需要停服务，直接用底层的 `maintenance.sh on|off`（公网内网共用同一个）。

**机制：**

- `deploy/scripts/maintenance.sh on|off|status` 在宿主机 `deploy/maintenance/` 下写入 / 删除 `MAINTENANCE_ON` flag 文件（两套部署共用同一目录、同一脚本）
- Caddy 每请求检查该 flag（`file` matcher stat `MAINTENANCE_ON`）→ 命中由 `file_server { status 503 }` 直接服务 `maintenance.html`（带睡猫的静态页）
- 检查是 per-request 的，**无需 reload**，切换秒级生效
- `/health/` 故意不挡——容器 healthcheck 和外部监控仍要看到真实状态
- **上游真实 503 原样穿透**：维护页 503 只在 gated 路径内产生，`/health/ready` 在 DB/Redis 异常时返回的 JSON 503 不会被改写为 HTML（`/health` 的 handle 在链里排在维护 gate 之前）

**首次启用（仅一次）：** compose 已声明 `deploy/maintenance` 卷挂载，但既有代理容器需要 force-recreate 一次才会挂上：

```bash
# Mode 3（内网）
docker compose -f deploy/docker-compose.intranet.yml up -d --force-recreate --no-deps caddy
# Mode 2（公网 / Caddy）
docker compose -f docker-compose.prod.yml --profile infra up -d --force-recreate caddy
```

之后所有切换不需要碰 docker。

**镜像升级（典型场景）：** 内网用 `pause.sh` / `resume.sh`，公网用 `pause-prod.sh` / `resume-prod.sh`。下面以内网气隙升级为例（公网无 docker load / tar 步骤，把脚本名换成 `-prod` 版即可）：

```bash
# 1. 加载新镜像（不影响在跑容器）—— 仅内网气隙需要
docker load -i tmp/artifactflow-v2.3.0.tar.gz
tar xzf tmp/artifactflow-deploy-v2.3.0.tar.gz   # 如果 deploy 也变了
tar xzf tmp/artifactflow-config-v2.3.0.tar.gz   # 如果 config 也变了

# 2. 进维护窗口（写 flag → 等 2s → stop backend frontend）
./deploy/scripts/pause.sh "正在更新到 v2.3.0，预计 5 分钟"

# 3. 退维护窗口（up -d backend frontend → 等 healthy → 关 flag）
#    backend 或 frontend 60s 内不 healthy → 维护页保持开启，运维有时间排查
./deploy/scripts/resume.sh v2.3.0
```

> **公网（Mode 2）升级：镜像本地构建，没有 docker load / tar / 版本 tag。** prod compose
> 的 backend/frontend 固定是 `:latest`，所以**升级 = 切代码再重建**，`resume-prod.sh`
> **不接版本号**（传了也无效——没有版本化镜像可切）：
> ```bash
> # 低流量：直接重建拉起（几秒停机，无维护页）
> git pull --ff-only                                  # 或 git checkout <ref>
> ./deploy/scripts/deploy-prod.sh --pull --build      # 2B 外部 DB 追加 --no-infra
>
> # 要维护页包住整个窗口：
> ./deploy/scripts/pause-prod.sh "升级中，约 5 分钟"   # 起维护页 + 停 backend/frontend
> ./deploy/scripts/deploy-prod.sh --build --no-cert-watch  # 重建并拉起（2B 加 --no-infra）
> #   ↑ 维护窗口里必须 --no-cert-watch：否则脚本结尾会 tail caddy 日志阻塞，
> #     后面的 resume 不会自动执行（证书已签发过，无需再盯）
> ./deploy/scripts/resume-prod.sh                     # 等 healthy + 经 Caddy(:2021) 探针 → 关维护页
> ```
> `deploy-prod.sh` 默认带 `--profile infra` 拉起捆绑 PG/Redis（Mode 2A）；**Mode 2B
> 用外部 DB/Redis，必须加 `--no-infra`**，否则会多起一对没用的本地 PG/Redis（空密码
> PG 还会起不来）。`pause-prod.sh` / `resume-prod.sh` 本身只包"维护页 + 停/起当前镜像"，
> 不改版本、不接参数——用于改 `.env` 等不换镜像的维护窗口。

`resume*.sh` 兼容 V1（`docker-compose`）和 V2（`docker compose`），自动探测，CentOS 7 老服务器和 Docker Desktop 都能用。

> **慢盘机器调长超时：** 默认每个服务等 60s healthy；若机器磁盘慢、Next.js / FastAPI 冷启动会超过 60s，超时后看日志没发现真错误，直接重跑并加大超时：
> ```bash
> RESUME_HEALTHY_TIMEOUT=120 ./deploy/scripts/resume.sh v2.3.0
> ```
> 最小允许值 10s（再小就会比容器 healthcheck 的 `start_period=15s` 还短，必假阴性）。

**Config-only 热修 —— 直接 `maintenance.sh`：**

只调 `models.yaml` 这种 restart backend 就能生效的场景，不需要 `pause.sh`
那种"停服务"操作，开关 flag 的同时 `restart backend` 就够：

```bash
./deploy/scripts/maintenance.sh on "调整模型配置，约 1 分钟"
vim config/models/models.yaml
docker compose -f deploy/docker-compose.intranet.yml restart backend
./deploy/scripts/maintenance.sh off
```

> 调 prompt / 工具 / skills 时不要只 restart backend；这些配置会 reconcile 进 DB，
> 需要跑 release/reconcile gate，或者重新打 config release 后走 `fleet deploy`。
>
> **注意：** `.env` 变更不能走 `restart`——`docker compose restart` 不会重读 `.env` interpolation，容器还在用旧值。需要改环境变量时，请走 `pause.sh → 改 .env → resume.sh`（resume 内部 `up -d` 会重建容器并注入新环境变量），或者短维护窗口下手动 `maintenance.sh on → AF_VERSION="$VERSION" docker compose ... up -d backend → maintenance.sh off`。

### 健康检查

| 端点 | 用途 | 检查内容 |
|------|------|----------|
| `GET /health/live` | 存活探测（Mode 1 / K8s liveness） | 进程存活，始终返回 200 |
| `GET /health/ready` | 就绪探测（Mode 2/3 / K8s readiness） | 进程 + DB + Redis 连通性，失败返回 503 |

### 舰队可观测与自愈（Phase C）

多副本下「哪个实例活着 / 有没有出错 / watchdog 抓没抓到过异常」在管理端一眼可见，
wedge 副本自动重启。三件套，都复用已有件、不引外部监控栈：

- **心跳注册表**：每个 backend 的 `RuntimeSampler`（30s 周期）把快照子集多写一份到
  Redis `{prefix:instance:<id>}`（单 SET+EX，每实例独占 slot）。字段含版本 / 上线时间 /
  loop_lag / RSS / 在途 turn 数 / **ERROR 计数** / **watchdog 最近 wedge 摘要** /
  **autoheal 重启轨迹**。单机（无 Redis）无注册表，面板退化成本机一行。
- **实例面板**：管理端菜单 →「舰队实例」（admin-only 独立 tab）。红黄绿：
  - 🟢 绿 = 心跳新鲜（`ts` < `OBS_HEARTBEAT_STALE_SEC`，默认 60s）且无异常
  - 🟡 黄 = 活着但 loop_lag 近分钟峰值超阈 / 窗口内出过 ERROR / watchdog 抓到过 wedge /
    近期被 autoheal 重启
  - 🔴 红 = 心跳 `ts` 陈旧（停更）但 key 仍在 TTL 窗口内 —— wedge「在册可见」，给自愈留窗口；
    key 真过期（> `OBS_HEARTBEAT_TTL_SEC`，默认 300s）才从列表消失（死透已收殓）
  - **双时间轴**是刻意的:TTL 若只有 ~ STALE，wedge 实例的 key 直接过期蒸发、面板根本
    没机会显红。颜色由读侧按 `ts` 新鲜度算(阈值可调不需回填)。
- **autoheal**：宿主 systemd timer 每 60s 把 unhealthy 容器 `docker restart`。装法与约束
  见 `deploy/autoheal/README.md`。两个要点：(1) 与维护窗口互斥（`MAINTENANCE_ON` 旗标在
  即整轮 no-op，不拉活 pause.sh 有意停掉的服务）；(2) 归因经 marker 文件中转、backend
  只读挂载代报（脚本不直连 Redis，保十行可审计），`docker restart` 保容器身份 → 面板
  同一行连续、`started_at` 变新即重启轨迹。

验收（真机随发版窗口）：`--scale backend=2` 面板见两实例；`kill -STOP` 一个副本模拟
wedge → ≤90s 变红 → autoheal 重启 → 恢复绿；制造一条 ERROR 日志 → 对应实例变黄且计数可见。

### 数据卷

| 卷名 | 用途 |
|------|------|
| `artifactflow_data` | SQLite 数据库 / 上传文件 |
| `postgres_data` | PostgreSQL 数据（Mode 2A/3A） |
| `redis_data` | Redis AOF 持久化（Mode 2A/3A） |
| `caddy_data` | Caddy 证书 + ACME 账户密钥（Mode 2）。**务必持久化** —— 丢卷触发证书重新签发，频繁重建可能撞 Let's Encrypt 每域每周 50 张频控 |
| `caddy_config` | Caddy 运行时自管配置（Mode 2） |

### 停止与清理

> **`--profile infra` 必须与启动时一致**，否则 PG/Redis 容器不在 Compose 作用域内，`down` 会跳过它们。

```bash
# Mode 1
docker compose down

# Mode 2A（启动时带了 --profile infra，停止也必须带）
docker compose -f docker-compose.prod.yml --profile infra down

# Mode 2B（无 --profile）
docker compose -f docker-compose.prod.yml down

# Mode 3A
docker compose -f deploy/docker-compose.intranet.yml --profile infra down

# Mode 3B
docker compose -f deploy/docker-compose.intranet.yml down
```

如需同时删除数据卷（**不可逆，会丢失数据库和 Redis 数据**）：

```bash
docker compose -f docker-compose.prod.yml --profile infra down -v
```

### 日志

```bash
# 查看所有服务日志
docker compose -f <compose-file> logs -f

# 单服务日志
docker compose -f <compose-file> logs -f backend

# 开启 debug 日志：.env 中设置 ARTIFACTFLOW_DEBUG=true
```

**Docker 日志磁盘上限**：两个 compose 文件给全部 5 个服务都配了 `logging: { driver: json-file, options: { max-size: "100m", max-file: "3" } }`，单服务最多 300 MB（旧切片滚动丢弃）。默认 json-file driver **无上限**，长跑容器能把 `/var/lib/docker/containers/*/<id>-json.log` 撑到几十 GB 填满宿主机盘 —— 这套配置是兜底。注意这只覆盖 stdout/stderr 那一层；backend 自己写到 `/app/data/observability/*.jsonl` 的文件由 Python `RotatingFileHandler` 单独管（见 `OBS_JSONL_MAX_MB` × `OBS_JSONL_BACKUP_COUNT`），两条路径独立。
