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
| **2A: Prod 自建** | 生产 + 自建基础设施 | nginx + backend + frontend + PG + Redis | 容器化 | `docker-compose.prod.yml --profile infra` |
| **2B: Prod 云数据库** | 生产 + RDS/ElastiCache | nginx + backend + frontend | 外部托管 | `docker-compose.prod.yml` |
| **3A: 内网 自建** | 离线/内网部署 | 同 2A | 容器化 | `deploy/docker-compose.intranet.yml --profile infra` |
| **3B: 内网 托管DB** | 离线 + 内部DB服务 | nginx + backend + frontend | 内部托管 | `deploy/docker-compose.intranet.yml` |

**关键区别：**

- **Mode 2 vs 1：** Nginx 反向代理（单端口 80）、PG + Redis 持久化、Alembic 自动迁移
- **Mode 3 vs 2：** `image:` 替代 `build:`，通过 `docker save/load` 离线部署，无需访问外部镜像仓库
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

完整生产部署，PG + Redis 容器化，Nginx 反向代理。

### 前置准备

```bash
# 1. 从模板创建 .env
cp deploy/.env.prod.example .env

# 2. 编辑 .env，必须填写：
#    - ARTIFACTFLOW_JWT_SECRET（生成: python -c "import secrets; print(secrets.token_urlsafe(32))"）
#    - POSTGRES_PASSWORD（强密码）
#    - DASHSCOPE_API_KEY（默认模型必填）
```

### 启动

```bash
docker compose -f docker-compose.prod.yml --profile infra up -d
```

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
# 健康检查（通过 Nginx）
curl http://localhost/health/ready
# 预期: {"status":"ok","db":"ok","redis":"ok"}

# 前端
open http://localhost
```

### 扩缩容

```bash
# 水平扩展 backend（Nginx 自动负载均衡）
docker compose -f docker-compose.prod.yml --profile infra up -d --scale backend=2

# 注意：首次启动多副本时，Alembic 迁移通过 PG advisory lock 串行化
# 只有一个副本执行迁移，其他副本等待并验证后再启动
```

---

## Mode 2B: Production（云数据库）

使用外部 RDS + ElastiCache/Redis，不启动数据库容器。

### 配置

```bash
cp deploy/.env.prod.example .env
# 编辑 .env，修改连接地址：
# ARTIFACTFLOW_DATABASE_URL=postgresql+asyncpg://user:pass@your-rds-endpoint:5432/artifactflow
# ARTIFACTFLOW_REDIS_URL=redis://your-redis-endpoint:6379
# 删除或注释掉 POSTGRES_* 相关变量
```

### 启动

```bash
# 不加 --profile infra，不启动 PG/Redis 容器
docker compose -f docker-compose.prod.yml up -d
```

---

## Mode 3: 内网离线部署

适用于无法访问外部网络的环境。使用预构建镜像，通过 `docker save/load` 传输。

> **`docker compose` vs `docker-compose`：** 下面的命令用 V2 写法（`docker compose`，带空格）。CentOS 7 等老环境如果只有 V1（`docker-compose`，带横线），把所有 compose 调用替换成 V1 即可，compose 文件本身两版都解析。`pause.sh` / `resume.sh` 自动探测，无需替换。

### 构建发布包（在有网络的构建机上）

```bash
./scripts/release.sh 1.0.0
# 产出 3 个 tar + sha256（存到 dist/）：
#   artifactflow-1.0.0.tar.gz             (~370MB, 5 个 docker 镜像)
#   artifactflow-config-1.0.0.tar.gz      (~10KB, config/ — agent prompts + models.yaml)
#   artifactflow-deploy-1.0.0.tar.gz      (~15KB, deploy/ — compose + nginx.conf + scripts)
```

> **三 tar 按变更频率拆分：** 调 prompt 只需重传 config tar，调 nginx 配置只需重传 deploy tar，不必每次都拖 ~370MB 镜像。intranet compose 把 `../config:/app/config:ro` 和 `./maintenance:/etc/nginx/maintenance:ro` 都用 bind mount 挂进容器，所以宿主机解出来的文件直接生效（详见后文运行时变更表）。

> **目标平台默认 `linux/amd64`。** Apple Silicon 上跑 `release.sh` 会自动通过 buildx + QEMU 交叉编译，省得装到 x86_64 服务器后撞 `exec format error`。要构建别的平台传 `PLATFORM=linux/arm64 ./scripts/release.sh ...`。

### 首次部署（在目标内网机器上）

```bash
# 1. 传 3 个 tar + sha256 到目标机
scp dist/artifactflow-1.0.0.tar.gz dist/artifactflow-1.0.0.tar.gz.sha256 \
    dist/artifactflow-config-1.0.0.tar.gz dist/artifactflow-config-1.0.0.tar.gz.sha256 \
    dist/artifactflow-deploy-1.0.0.tar.gz dist/artifactflow-deploy-1.0.0.tar.gz.sha256 \
    target:/opt/artifactflow/

# 2. 校验 + 解包
cd /opt/artifactflow
sha256sum -c artifactflow-1.0.0.tar.gz.sha256
sha256sum -c artifactflow-config-1.0.0.tar.gz.sha256
sha256sum -c artifactflow-deploy-1.0.0.tar.gz.sha256
docker load -i artifactflow-1.0.0.tar.gz
tar xzf artifactflow-deploy-1.0.0.tar.gz   # 解出 ./deploy/
tar xzf artifactflow-config-1.0.0.tar.gz   # 解出 ./config/

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
# 在内网机（假设新 tar 已 scp 到 ./tmp/ 下）
cd /opt/artifactflow

# 1. 校验 + 解包（不影响在跑容器，可在维护开始前做）
./deploy/scripts/verify-bundle.sh tmp    # 一次性校验 tmp/ 下所有 tar
tar xzf tmp/artifactflow-deploy-1.0.1.tar.gz
tar xzf tmp/artifactflow-config-1.0.1.tar.gz
docker load -i tmp/artifactflow-1.0.1.tar.gz

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

### 运行时配置变更（无需 rebuild / 重新传镜像）

| 变更类型 | 操作 | 生效命令 |
|---------|------|---------|
| `config/agents/*.md`（agent prompt） | 直接编辑宿主机文件 | `docker compose -f deploy/docker-compose.intranet.yml restart backend` |
| `config/models/models.yaml`（模型 / base_url） | 直接编辑宿主机文件 | 同上 — `restart backend` |
| `config/tools/*.md`（自定义工具） | 直接编辑宿主机文件 | 同上 — `restart backend` |
| `config/site/*.json`（左栏通知 / 欢迎页提示） | 直接编辑宿主机文件，schema 见 `config/site/README.md` | **无需 restart** — 挂载在 frontend 容器，前端 60s 轮询自动重拉（标签回前台时立即重拉） |
| `deploy/.env`（任何 `ARTIFACTFLOW_*` 变量） | 直接编辑 | **`up -d backend`**（restart 不会重读 .env，up 会检测 env 变化重建容器） |
| `deploy/nginx.conf` | 直接编辑 | `docker compose -f deploy/docker-compose.intranet.yml restart nginx` |
| `deploy/docker-compose.intranet.yml`（端口、profile 等） | 直接编辑 | `up -d` |

> **关键区别：** 改 `config/*` 用 `restart backend`（让进程重读文件），改 `.env` 用 `up -d`（让 compose 重建容器注入环境变量）。`config/site/` 是例外：挂的是 frontend 容器，前端自己轮询，零运维动作。

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

> 上面的 `restart backend` 是给 `config/agents/`、`config/models/`、`config/tools/` 用的。如果**只**改了 `config/site/*.json`（通知 / 欢迎页提示），不需要任何 docker 命令 —— 前端轮询自己生效。

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
| `ARTIFACTFLOW_COMPACTION_TOKEN_THRESHOLD` | `80000` | 单次 LLM 调用 input+output 超此值时，引擎内立即触发 compaction |
| `ARTIFACTFLOW_COMPACTION_TIMEOUT` | `120` | 单次 compact LLM 调用的超时（秒） |
| `ARTIFACTFLOW_INVENTORY_PREVIEW_LENGTH` | `200` | Artifact 清单预览截断长度 |

> 旧版本的 `COMPACTION_PRESERVE_PAIRS` / `CONTEXT_MAX_TOKENS` / `TRUNCATION_PRESERVE_AI_MSGS` 已随异步后台 compaction 与 token-预算截断一起移除。现在引擎不做独立截断，压缩完全由上述阈值驱动（详见 [engine.md → Compaction 机制](architecture/engine.md#compaction-机制)）。

### CORS

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARTIFACTFLOW_CORS_ORIGINS` | `["http://localhost:3000"]` | 允许的跨域来源 |
| `ARTIFACTFLOW_CORS_ALLOW_CREDENTIALS` | `true` | 允许携带凭证 |
| `ARTIFACTFLOW_CORS_ALLOW_METHODS` | `["*"]` | 允许的 HTTP 方法 |
| `ARTIFACTFLOW_CORS_ALLOW_HEADERS` | `["*"]` | 允许的请求头 |

### 其他

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARTIFACTFLOW_MAX_CONCURRENT_TASKS` | `10` | 最大并发引擎执行数 |
| `ARTIFACTFLOW_MAX_UPLOAD_SIZE` | `20971520` | 上传大小限制（字节，默认 20MB） |
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
| `redis` | `768m` | Redis maxmemory 512m + AOF rewrite/RDB fork 余量 |

Postgres 没设 `mem_limit` 是有意的 —— PG 的 `shared_buffers=256MB` + per-connection `work_mem` 会占用浮动内存，硬上限容易触发 OOM kill；建议在主机层留够 PG 工作集的物理内存。

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

### Nginx 配置

生产模式使用 Nginx 反向代理（配置文件：`deploy/nginx.conf`）：

- SSE 流式连接：`/api/v1/stream/` 路径关闭 `proxy_buffering`，超时 1800s
- Swagger 文档：生产环境下 `/docs`、`/redoc`、`/openapi.json` 返回 404
- `--scale` 支持：使用 Docker 内部 DNS resolver `127.0.0.11`
- 维护开关：`deploy/maintenance/MAINTENANCE_ON` flag 文件控制（详见下方"维护模式"）

### 维护模式（无停机更新窗口）

适用于 **Mode 3**（内网，有 Nginx 反向代理）。Mode 1 无 Nginx，不适用；Mode 2（生产）暂未适配——`pause.sh` / `resume.sh` 硬编码 `docker-compose.intranet.yml`，硬套会驱动错文件。

**两层接口：** 镜像升级（典型场景）用 `pause.sh` / `resume.sh`，它们封装了"维护页 + 停服务 → 起新版本 + 关维护页"的全套动作；config-only 改动不需要停服务，直接用底层的 `maintenance.sh on|off`。

**机制：**

- `deploy/scripts/maintenance.sh on|off|status` 在宿主机 `deploy/maintenance/` 下写入 / 删除 `MAINTENANCE_ON` flag 文件
- Nginx 在每个 gated location 头部 `if (-f /etc/nginx/maintenance/MAINTENANCE_ON) { return 503; }`，触发后 `error_page 503 @maintenance` 渲染 `maintenance.html`（带睡猫的静态页）
- 检查是 per-request 的，**无需 reload**，切换秒级生效
- `/health/` 故意不挡——容器 healthcheck 和外部监控仍要看到真实状态
- `error_page 503 @maintenance` 只在 gated location 内生效（不放在 server 级），加上 `proxy_intercept_errors` 默认 `off`，**上游真实 503 原样穿透**（如 `/health/ready` 在 DB/Redis 异常时返回的 JSON 503，契约不会被改写为 HTML）

**首次启用（仅一次）：** intranet compose 已声明 `deploy/maintenance` 卷挂载，但既有 nginx 容器需要 force-recreate 一次才会挂上：

```bash
docker compose -f deploy/docker-compose.intranet.yml up -d --force-recreate nginx
```

之后所有切换不需要碰 docker。

**镜像升级（典型场景）—— `pause.sh` / `resume.sh`：**

```bash
# 1. 加载新镜像（不影响在跑容器）
docker load -i tmp/artifactflow-v2.3.0.tar.gz
tar xzf tmp/artifactflow-deploy-v2.3.0.tar.gz   # 如果 deploy 也变了
tar xzf tmp/artifactflow-config-v2.3.0.tar.gz   # 如果 config 也变了

# 2. 进维护窗口（写 flag → 等 2s → stop backend frontend）
./deploy/scripts/pause.sh "正在更新到 v2.3.0，预计 5 分钟"

# 3. 退维护窗口（up -d backend frontend → 等 healthy → 关 flag）
#    backend 或 frontend 60s 内不 healthy → 维护页保持开启，运维有时间排查
./deploy/scripts/resume.sh v2.3.0
```

`resume.sh` 兼容 V1（`docker-compose`）和 V2（`docker compose`），自动探测，CentOS 7 老服务器和 Docker Desktop 都能用。

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
