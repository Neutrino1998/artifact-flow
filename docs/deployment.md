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
| **3A: 内网 自建** | 离线/内网部署 | nginx + backend + frontend + PG + Redis | 容器化 | `deploy/docker-compose.intranet.yml --profile infra` |
| **3B: 内网 托管DB** | 离线 + 内部DB服务 | nginx + backend + frontend | 内部托管 | `deploy/docker-compose.intranet.yml` |

**关键区别：**

- **Mode 2 vs 1：** Caddy 反向代理（自动 HTTPS / Let's Encrypt，端口 80+443）、PG + Redis 持久化、Alembic 自动迁移
- **Mode 3 vs 2：** 反向代理用 **nginx**（内网无公网域名 / 不签证书，HTTP 单端口 80 即可）；`image:` 替代 `build:`，通过 `docker save/load` 离线部署，无需访问外部镜像仓库
- **代理分叉的原因：** 公网（Mode 2）需要自动签发 / 续期 TLS 证书 → Caddy 一行配置搞定；内网（Mode 3）是气隙环境，Let's Encrypt 不可达、也不需要域名证书 → 保留零依赖的 nginx。两套反向代理共用同一套维护页 flag 机制（`deploy/maintenance/`）。
- **2A/3A vs 2B/3B：** `--profile infra` 控制是否启动 PG/Redis 容器

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

# 注意：首次启动多副本时，Alembic 迁移通过 PG advisory lock 串行化
# 只有一个副本执行迁移，其他副本等待并验证后再启动
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

# 首次部署 / nginx-pg-redis 版本升级 —— 显式加 infra
./scripts/release.sh 1.0.0 --with-infra
# 额外产出：
#   artifactflow-infra-nginx1.30.1-pg16-redis7.tar.gz  (~130MB)
# 文件名按 base image 版本内容寻址 —— 目标机已有同名 tar 就跳过 scp
```

> **拆 4 tar 按变更频率分层：**
> - `config` / `deploy` 是 bind-mount 进容器的,改 prompt / nginx / scripts 重传对应 tar 即可,不动镜像。
> - `app` 是 backend + frontend,几乎每次发版都改。
> - `infra` 是 nginx / postgres / redis 三个 base image,版本动得极少（半年一次量级）,默认**不打**,显式 `--with-infra` 才生成。命名带 base image 版本号方便目标机一眼看出"我已经有这个 infra tar 了"。

> **目标平台默认 `linux/amd64`。** Apple Silicon 上跑 `release.sh` 会自动通过 buildx + QEMU 交叉编译，省得装到 x86_64 服务器后撞 `exec format error`。要构建别的平台传 `PLATFORM=linux/arm64 ./scripts/release.sh ...`。

### 首次部署（在目标内网机器上）

```bash
# 1. 传 4 个 tar + sha256 + manifest 到目标机
scp dist/artifactflow-{app,config,deploy}-1.0.0.tar.gz{,.sha256} \
    dist/artifactflow-infra-nginx1.30.1-pg16-redis7.tar.gz{,.sha256} \
    dist/artifactflow-1.0.0.manifest.txt \
    target:/opt/artifactflow/

# 2. 校验 + 解包
cd /opt/artifactflow
# 首次部署 deploy/ 还没解出来,verify-bundle.sh 用不了 —— 直接 sha256sum -c。
# CWD 跟 tar 在同一层、不会撞路径坑;glob 在全新目录里也只会匹配本次的 tar。
sha256sum -c artifactflow-*.tar.gz.sha256
tar xzf artifactflow-deploy-1.0.0.tar.gz       # 解出 ./deploy/(下次升级起就能用 verify-bundle.sh)
tar xzf artifactflow-config-1.0.0.tar.gz       # 解出 ./config/
docker load -i artifactflow-infra-nginx1.30.1-pg16-redis7.tar.gz
docker load -i artifactflow-app-1.0.0.tar.gz

# 3. 配置 .env
cp deploy/.env.intranet.example deploy/.env
# 编辑 deploy/.env，填写密码和 API Keys

# 4. 内网 LLM（如需）：编辑 config/models/models.yaml，把 base_url 指向内部推理端点

# 5. 启动（3A: 自建基础设施）
AF_VERSION=1.0.0 docker compose -f deploy/docker-compose.intranet.yml --profile infra up -d

# 6. 创建管理员
docker compose -f deploy/docker-compose.intranet.yml exec backend \
  python scripts/create_admin.py admin --password <your-password>
```

### 滚动更新已有部署

新版本到位后，`pause.sh` / `resume.sh` 把维护窗口包成两个动作：起维护页 + 停服务 → 加载新镜像 → 起新版本 + 关维护页。

```bash
# 在内网机（假设新 tar 已 scp 到 ./tmp/ 下，typically 不含 infra）
cd /opt/artifactflow

# 1. 校验 + 解包（不影响在跑容器，可在维护开始前做）
./deploy/scripts/verify-bundle.sh tmp    # 一次性校验 tmp/ 下所有 tar
tar xzf tmp/artifactflow-deploy-1.0.1.tar.gz
tar xzf tmp/artifactflow-config-1.0.1.tar.gz
docker load -i tmp/artifactflow-app-1.0.1.tar.gz

# 2. 进维护窗口
./deploy/scripts/pause.sh "升级到 v1.0.1"

# 3. 退维护窗口（resume 失败 → 维护页保持开启，运维有时间排查）
./deploy/scripts/resume.sh 1.0.1
```

> **首次启用维护页（一次性 bootstrap）：** 如果现有 nginx 容器是用旧 compose 起的（没有 maintenance 卷挂载），先 force-recreate 一次让它读新 nginx.conf：
> ```bash
> docker compose -f deploy/docker-compose.intranet.yml up -d --force-recreate nginx
> ```
> 之后 `pause.sh` 写的 flag 文件才能被 nginx 看到。后续升级直接 pause/resume 即可，不再需要 force-recreate。

> **涉及 compose infra config 变更的升级（罕见）：** 多数升级只改 backend / frontend 镜像和它们用到的 `ARTIFACTFLOW_*` env，`pause/resume` 已覆盖（resume.sh `up backend frontend` → compose 自动 diff config-hash → 改了就 recreate）。但若本版本动了 `postgres` / `redis` / `nginx` 服务块的 HostConfig 字段（`image` / `logging` / `mem_limit` / `volumes` / `ports` / `cap_add` / `command`，典型例：commit `d7f26f8`），或 `.env` 里 `AF_HTTP_PORT`（nginx `ports:` interpolation），resume.sh 不触碰 infra 容器，新配置永远不生效。**前提**：先按上面常规流程完成 `verify-bundle.sh` + `docker load` + `tar xzf deploy/config`，让新 compose + 新 nginx.conf 就位，再进入下面两个时机（否则 recreate 用的是旧 compose / 旧 nginx.conf）：
>
> **(a) nginx 块或 `AF_HTTP_PORT` 变了 → 在 `pause.sh` 之前 recreate**：
> ```bash
> docker compose -f deploy/docker-compose.intranet.yml --profile infra \
>     up -d --force-recreate --no-deps nginx
> ```
> 接受 1–2 秒 nginx 重启的连接 RST（量级跟任意一次 nginx restart 一致，可由维护窗口公告吸收）。**为什么不能放到 pause 之后**：`deploy/nginx.conf:1-7` 用静态 `upstream backend { server backend:8000; }`，OSS nginx 启动时**一次性**解析 upstream 名称（`resolver 127.0.0.11` 只对 `proxy_pass http://$variable` 这种变量写法生效；静态 upstream 块的运行时重解析需要 nginx-plus 的 `resolve` flag，OSS 没有）。pause 后 backend/frontend 已 stopped，从 Docker DNS 消失 → nginx 启动时 upstream 解析失败 → nginx 进程退出 → 维护页连同 nginx 一起掉。
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
> 两条 recreate 都必须 `--no-deps`：不加 compose 会顺手把 backend/frontend 也起来（nginx `depends_on` 它俩），违反前提，且会用 `${AF_VERSION:-latest}` 拉镜像（内网通常没有 `latest` tag）。数据安全：named volume 在 recreate 中不动；PG 走 crash recovery 启动（5–15s），Redis 控制状态全是 TTL key 应用层自愈，nginx 无状态。
>
> **`.env` 变量归属(`.env.{intranet,prod}.example` 头注释也复述了同样规则)**：
>
> | 变量 | 走哪条路径 |
> |---|---|
> | `ARTIFACTFLOW_*`（JWT / DATABASE_URL / REDIS_URL / MAX_CONCURRENT_TASKS / API keys 等） | 常规 pause/resume（resume.sh up backend → compose 检测 env_file 变化 → recreate backend） |
> | `AF_HTTP_PORT`（仅 prod 模板有；nginx `ports:` interpolation） | 上面 (a) —— nginx pre-pause force-recreate |
> | `POSTGRES_*` | 见上方 ⚠ 块 —— **不能**走 recreate，必须 SQL `ALTER USER` |
> | `AF_VERSION` | `resume.sh <VERSION>` 显式传入即可 |
>
> **验证 HostConfig 已生效**（recreate 完毕后、resume 之前；容器名按当前 compose project 实际命名，默认 `artifactflow-<svc>-1`）：
>
> ```bash
> for s in nginx backend frontend postgres redis; do
>   echo "--- $s ---"
>   docker inspect artifactflow-${s}-1 --format \
>     '{{.HostConfig.LogConfig.Type}} {{.HostConfig.LogConfig.Config}} mem={{.HostConfig.Memory}}'
> done
> # 期望 LogConfig.Type=json-file，Config 含 max-size:100m / max-file:3；
> # Memory（字节）：nginx=0 / backend=2147483648 / frontend=1073741824 /
> # postgres=2147483648 / redis=805306368
> ```

### 运行时配置变更（无需 rebuild / 重新传镜像）

| 变更类型 | 操作 | 生效命令 |
|---------|------|---------|
| `config/agents/*.md`（agent prompt） | 直接编辑宿主机文件 | `docker compose -f deploy/docker-compose.intranet.yml restart backend` |
| `config/models/models.yaml`（模型 / base_url） | 直接编辑宿主机文件 | 同上 — `restart backend` |
| `config/tools/*.md`（自定义工具） | 直接编辑宿主机文件 | 同上 — `restart backend` |
| `config/site/notifications.json`（左栏通知） | 直接编辑宿主机文件，schema 见 `config/site/README.md` | **无需 restart** — 挂载在 frontend 容器，前端 60s 轮询自动重拉（标签回前台时立即重拉） |
| `config/site/welcome_tips.json` / `branding.json`（欢迎页提示 / 版权页脚） | 直接编辑宿主机文件；`branding.json` 首次启用需 `cp branding.example.json branding.json` 再填值（仓库 `.gitignore` 排除真实文件） | **无需 restart**，但**只在挂载时拉一次、不轮询**——改完需用户刷新页面才看到。文件缺失 / schema 错位 → 对应 UI 自动隐藏或回落默认（fail-closed）。 |
| `deploy/.env`（任何 `ARTIFACTFLOW_*` 变量） | 直接编辑 | **`up -d backend`**（restart 不会重读 .env，up 会检测 env 变化重建容器） |
| `deploy/nginx.conf` | 直接编辑 | `docker compose -f deploy/docker-compose.intranet.yml restart nginx` |
| `deploy/docker-compose.intranet.yml`（端口、profile 等） | 直接编辑 | `up -d` |

> **关键区别：** 改 `config/*` 用 `restart backend`（让进程重读文件），改 `.env` 用 `up -d`（让 compose 重建容器注入环境变量）。`config/site/` 是例外：挂在 frontend 容器，无需重启；其中 `notifications.json` 前端轮询自动重拉，`welcome_tips.json` / `branding.json` 只在页面加载时读取，运维改完需用户刷新。

### 仅推送 config 更新（不动镜像）

```bash
# 在构建机上重新打包（或手工 tar）
tar czf artifactflow-config-1.0.1.tar.gz config/

# 推到内网
scp artifactflow-config-1.0.1.tar.gz target:/opt/artifactflow/
ssh target 'cd /opt/artifactflow && \
            tar xzf artifactflow-config-1.0.1.tar.gz && \
            docker compose -f deploy/docker-compose.intranet.yml restart backend'
```

> 上面的 `restart backend` 是给 `config/agents/`、`config/models/`、`config/tools/` 用的。如果**只**改了 `config/site/*.json`，不需要任何 docker 命令；其中 `notifications.json` 前端 60s 轮询自己生效，`welcome_tips.json` / `branding.json` 只在挂载时拉一次，需要用户刷新页面才看到。

---

## 沙盒执行环境（可选 overlay）

`bash` / `mount` / `persist` 三个沙盒工具需要宿主侧前置 + 一个 compose overlay。**没有沙盒需求的部署跳过本节**——基础 compose 不挂 `docker.sock`，没有这个暴露面。架构与全部 `SANDBOX_*` 旋钮见 [架构 · 沙盒执行](architecture/sandbox.md)。

随发布包额外携带三个传输单元（与应用包同一构建机产出）：**沙盒镜像** tar（`scripts/build-sandbox-image.sh`，注意按目标机选 `PLATFORM`）、**verify 探针** tar（同脚本产出，arch 无关）、**gVisor 包** tar（`sandbox/gvisor-pkg/fetch-and-package.sh`）。

### 宿主前置（一次性，按顺序）

1. **gVisor（runsc）**：解开 gVisor 包，`sudo ./install.sh && sudo systemctl reload docker && sudo ./smoke-test.sh`（内含 `unshare -U` 预检）。arm / 鲲鹏注意：Kylin V10 arm 默认 64K 页内核，gVisor 拒启——先用 `sandbox/kernel-4k-pkg/` 换 4K 内核再装（x86 跳过）。
2. **沙盒镜像**：`gunzip -c artifactflow-sandbox-<ver>-<arch>.tar.gz | docker load`。
3. **scratch 根 = 定容 loop 文件系统**（磁盘配额的硬墙层：watchdog race 窗口内写穿也只是池子满，宿主盘无恙；独立 inode 表顺带兜住海量小文件）：

   ```bash
   POOL=/var/lib/artifactflow/sandbox-pool.img
   ROOT=/var/lib/artifactflow/sandbox-scratch
   sudo mkdir -p "$(dirname "$POOL")" "$ROOT"
   sudo fallocate -l 20G "$POOL"          # 容量 ≈ 并发 turn 数 × SANDBOX_WORKSPACE_QUOTA_MB(默认2G) + 余量
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
docker compose -f deploy/docker-compose.intranet.yml \
               -f deploy/docker-compose.sandbox.yml [--profile infra] up -d
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
| `ARTIFACTFLOW_DATABASE_POOL_SIZE` | `5` | 连接池大小 |
| `ARTIFACTFLOW_DATABASE_MAX_OVERFLOW` | `10` | 连接池溢出上限 |
| `ARTIFACTFLOW_DATABASE_POOL_TIMEOUT` | `30` | 获取连接超时（秒） |
| `ARTIFACTFLOW_DATABASE_POOL_RECYCLE` | `300` | 连接回收周期（秒） |

### Redis

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARTIFACTFLOW_REDIS_URL` | `""` | 空 = InMemory 回退；非空 = Redis 模式 |
| `ARTIFACTFLOW_REDIS_CLUSTER` | `false` | Redis Cluster 模式 |
| `ARTIFACTFLOW_REDIS_KEY_PREFIX` | `""` | Key 命名空间前缀（启用 Redis 时**必填**） |
| `ARTIFACTFLOW_REDIS_MAX_CONNECTIONS` | `50` | 连接池上限 |
| `ARTIFACTFLOW_LEASE_TTL` | `90` | 对话租约 TTL（秒），心跳每 TTL/3 续租 |

### SSE 与执行超时

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARTIFACTFLOW_SSE_PING_INTERVAL` | `15` | 心跳间隔（秒），保持连接活跃 |
| `ARTIFACTFLOW_EXECUTION_TIMEOUT` | `1800` | 总执行上限（秒），含 permission 等待 |
| `ARTIFACTFLOW_STREAM_CLEANUP_TTL` | `60` | 执行结束后 stream 清理窗口（秒） |
| `ARTIFACTFLOW_PERMISSION_TIMEOUT` | `300` | 单次权限等待超时（秒） |

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
| `ARTIFACTFLOW_MAX_CONCURRENT_TASKS` | `10` | 最大并发引擎执行数 |
| `ARTIFACTFLOW_MAX_UPLOAD_SIZE` | `104857600` | 单文件上传大小限制（字节，默认 100MB）；批量总字节由代理层独立封顶（200MB）。注：文本转换另有更低的独立闸 20MB——超闸**不 422、落为二进制 blob artifact**（可下载、可 mount 进沙盒处理） |
| `ARTIFACTFLOW_DEFAULT_PAGE_SIZE` | `20` | 分页默认每页条数 |
| `ARTIFACTFLOW_MAX_PAGE_SIZE` | `100` | 分页最大每页条数 |

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

`src/config.py` 的代码默认值（`MAX_CONCURRENT_TASKS=10`、`DATABASE_POOL_SIZE=5+10`、`REDIS_MAX_CONNECTIONS=50`）在没有特定模型 API 并发预算时是安全起点，但**对于 Mode 2/3 的实际生产部署偏保守** —— 单 backend 只允许 10 个引擎并发，模型 API 配额（如 64 路并行）会被严重浪费。

`deploy/.env.intranet.example` 和 `deploy/.env.prod.example` 已按 64 路模型并发预设了一组推荐值，下面是这组值的依据，便于按你自己的模型 API 配额线性缩放。

### 三个互相绑定的旋钮

```
模型 API 并发预算 (例 64)
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

如果模型 API 并发不是 64，把以上三个值按比例缩放即可。

### Mode A vs Mode B：DB 池的安全边界

模板里 DB 池两行（`POOL_SIZE` / `MAX_OVERFLOW`）**默认是注释掉的**，这是有意的：

| 部署形态 | DB 来源 | `max_connections` 上限 | DB 池处理 |
|---|---|---|---|
| Mode 2A / 3A（`--profile infra`） | 捆绑的 `postgres` 容器 | `200`（compose `command:` 显式设置） | **取消注释开启 20+40** —— 60 连接安全 |
| Mode 2B（云托管 DB） | 外部 RDS / Aurora 等 | 由托管层级决定（小规格 ~85–150） | **保持注释或缩小** —— 否则单 backend 60 连接易打满 |
| Mode 3B（内部企业 DB，无 `--profile infra`） | 公司内部 DB | 由 DBA 配置 | 同上 —— 由内部 DB 容量决定 |

详见 [`deploy/docker-compose.intranet.yml`](https://github.com/Neutrino1998/artifact-flow/blob/main/deploy/docker-compose.intranet.yml) 和 [`docker-compose.prod.yml`](https://github.com/Neutrino1998/artifact-flow/blob/main/docker-compose.prod.yml) 中 `postgres` 服务的 `command: postgres -c max_connections=200 -c shared_buffers=256MB`，**这条 patch 仅在 `--profile infra` 启动时生效**。

### Redis 内存预算

Mode A 的 compose 把 Redis `--maxmemory` 从默认 `256mb` 提到 **`512mb`**，并保留 `--maxmemory-policy noeviction`。

**容量估算**（基于实测，单 message 平均 ~500 KB、重负载 ~1 MB）：

| 并发 | 典型峰值 | 重负载峰值 |
|---|---|---|
| 10（代码默认） | 5–10 MB | ~10 MB |
| 64（推荐） | 30–60 MB | ~130 MB |

512 MB 在 64 并发下提供 ~4× 余量。如果你的模型并发 > 64 或自定义工具单条输出可能很大（>100 KB），按比例抬 `maxmemory`。

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

反向代理（nginx / Caddy）不设 `mem_limit`：长期 < 50 MB，加了纯属冗余。

---

## 运维参考

### 数据库迁移

容器启动时 `deploy/entrypoint.sh` 自动处理迁移：

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

- **多副本安全：** 通过 `pg_advisory_lock(hashtext('alembic_migrate'))` 保证只有一个副本执行迁移
- **失败处理：** Leader 迁移失败后不释放 lock（连接关闭自动释放），Follower 检测到 schema 未到 head 后退出，容器 restart policy 会重试
- **Fallback：** 如果 advisory lock 不可用（如 MySQL），直接执行 `alembic upgrade head`

### 反向代理配置

两种部署用不同的反向代理，但职责（SSE 不缓冲、挡 Swagger、维护页 flag、真实 IP 透传、上传上限）一一对应：

| 维度 | **Mode 2（公网）= Caddy** | **Mode 3（内网）= nginx** |
|---|---|---|
| 配置文件 | `deploy/Caddyfile` | `deploy/nginx.conf` |
| TLS | **自动 HTTPS**（Let's Encrypt，端口 80+443） | 无（HTTP 单端口 80，内网气隙环境） |
| SSE | `flush_interval -1`（关响应缓冲） | `proxy_buffering off`，超时 1800s |
| Swagger | `/docs`、`/redoc`、`/openapi.json` → 404 | 同左 |
| 上传上限 | `request_body max_size 210MiB` | `client_max_body_size 210M` |
| 维护开关 | `file` matcher 每请求 stat `MAINTENANCE_ON` | `if (-f ... MAINTENANCE_ON) return 503` |
| 真实 IP | `header_up X-Real-IP {remote_host}` | `proxy_set_header X-Real-IP $remote_addr` |

- **上传上限（代理层是总量权威闸）**：`POST /api/v1/chat` 把整批附件放进**一个** multipart 请求，body 是整批之和。三轴**独立**：单文件 ≤100MB（`MAX_UPLOAD_SIZE`，后端 422）、数量 ≤10（`MAX_CHAT_ATTACHMENTS`，后端 422）、**总量 ≤200MB（代理层 413）**。总量**刻意小于** per-file×count（100MB×10=1GB）——设计意图是"1 个大文件 or 多个小文件，但控总量"，故大批量时代理层**会按设计抢先** 413（单个超大文件仍由后端给干净 422）。`210M`/`210MiB` = 200MiB 内容 + 10MiB multipart 开销。**单位注意**：nginx `210M` 与 Caddy `210MiB` 都是二进制 2²⁰；Caddy 的 `MB` 会被当 decimal 10⁶（偏小），故必须写 `MiB`。另：文本转换路径有更低的独立闸 `MAX_TEXT_CONVERT_BYTES`（20MB，防解码+词表物化的内存放大），blob 路径（图片/PDF/docx/其它二进制）不受此限；**超文本闸不 422**，文件落为二进制 blob artifact（可下载、可 mount 进沙盒处理）。
- **`X-Real-IP`（登录频控依赖）**：后端 per-IP 登录频控**只读这个头**（刻意不信可被客户端伪造的 `X-Forwarded-For`）。安全前提是 backend 仅 `expose`、不发布主机端口，只经反向代理可达，故这个头不可伪造。删掉它 / 换不写该头的代理 → per-IP 限流静默退化成"所有请求共用代理容器一个 IP 桶"（per-username 主防线仍在）。
  - **Mode 2 灰云（CF DNS only）直连**：`{remote_host}` 就是真实客户端 IP，直接写进 `X-Real-IP`，不退化。
  - **Mode 2 若改用 CF 橙云（proxied）**：真实客户端 IP 移到 `X-Forwarded-For`、`{remote_host}` 变成 CF 边缘 IP —— 需在 Caddyfile 加 `trusted_proxies`（CF IP 段）并改用 `{client_ip}`，否则 per-IP 限流退化。`deploy/Caddyfile` 头部注释了这一点。
- **`--scale` 支持**：nginx 用 Docker 内部 DNS resolver `127.0.0.11`；Caddy 用服务名 `backend:8000` / `frontend:3000`，运行时按需解析，原生支持多副本。

### 维护模式（无停机更新窗口）

适用于 **Mode 2（公网 / Caddy）** 和 **Mode 3（内网 / nginx）**。Mode 1 无反向代理，不适用。

**脚本分两套，机制完全相同（共享 `deploy/scripts/_maint_lib.sh`），只差 compose 文件与健康探针：**

| | Mode 2（公网） | Mode 3（内网） |
|---|---|---|
| 进维护窗口 | `pause-prod.sh` | `pause.sh` |
| 退维护窗口 | `resume-prod.sh` | `resume.sh` |
| compose 文件 | `docker-compose.prod.yml` | `deploy/docker-compose.intranet.yml` |
| resume 健康探针 | caddy 容器内经 Caddy 内部端口 `wget localhost:2021/health`（真正过 Caddy 反代 → 验证配置已加载 + Caddy→backend 通；该端口不发布到宿主机，避开 TLS-on-localhost 域名不匹配） | host → nginx 发布端口 `localhost:${AF_HTTP_PORT}/health`（顺带验证 nginx 静态 upstream 解析未过期） |

**两层接口：** 镜像升级（典型场景）用 `pause*.sh` / `resume*.sh`，封装"维护页 + 停服务 → 起新版本 + 关维护页"全套；config-only 改动不需要停服务，直接用底层的 `maintenance.sh on|off`（公网内网共用同一个）。

**机制：**

- `deploy/scripts/maintenance.sh on|off|status` 在宿主机 `deploy/maintenance/` 下写入 / 删除 `MAINTENANCE_ON` flag 文件（两套部署共用同一目录、同一脚本）
- 反向代理每请求检查该 flag → 命中渲染 `maintenance.html`（带睡猫的静态页）：
  - **nginx**：每个 gated location 头部 `if (-f ... MAINTENANCE_ON) { return 503; }` → `error_page 503 @maintenance`
  - **Caddy**：`file` matcher stat `MAINTENANCE_ON` → `file_server { status 503 }` 直接服务维护页
- 检查是 per-request 的，**无需 reload**，切换秒级生效
- `/health/` 故意不挡——容器 healthcheck 和外部监控仍要看到真实状态
- **上游真实 503 原样穿透**：维护页 503 只在 gated 路径内产生，`/health/ready` 在 DB/Redis 异常时返回的 JSON 503 不会被改写为 HTML（nginx 靠 `error_page` 不放 server 级 + `proxy_intercept_errors off`；Caddy 靠 `/health` 在 handle 链里排在维护 gate 之前）

**首次启用（仅一次）：** compose 已声明 `deploy/maintenance` 卷挂载，但既有代理容器需要 force-recreate 一次才会挂上：

```bash
# Mode 3（内网 / nginx）
docker compose -f deploy/docker-compose.intranet.yml up -d --force-recreate nginx
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

**Config-only 变更 —— 直接 `maintenance.sh`：**

只调 prompt / `models.yaml` 这种 `restart backend` 就能生效的场景，不需要 `pause.sh` 那种"停服务"操作，开关 flag 的同时 `restart backend` 就够：

```bash
./deploy/scripts/maintenance.sh on "调整 agent 配置，约 1 分钟"
vim config/agents/lead_agent.md
docker compose -f deploy/docker-compose.intranet.yml restart backend
./deploy/scripts/maintenance.sh off
```

> **注意：** `.env` 变更不能走 `restart`——`docker compose restart` 不会重读 `.env` interpolation，容器还在用旧值。需要改环境变量时，请走 `pause.sh → 改 .env → resume.sh`（resume 内部 `up -d` 会重建容器并注入新环境变量），或者短维护窗口下手动 `maintenance.sh on → up -d backend → maintenance.sh off`。

### 健康检查

| 端点 | 用途 | 检查内容 |
|------|------|----------|
| `GET /health/live` | 存活探测（Mode 1 / K8s liveness） | 进程存活，始终返回 200 |
| `GET /health/ready` | 就绪探测（Mode 2/3 / K8s readiness） | 进程 + DB + Redis 连通性，失败返回 503 |

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
