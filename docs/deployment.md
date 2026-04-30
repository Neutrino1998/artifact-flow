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

### 构建发布包（在有网络的构建机上）

```bash
./scripts/release.sh 1.0.0
# 产出:
#   dist/artifactflow-1.0.0.tar.gz               (~500MB, 含全部 5 个镜像)
#   dist/artifactflow-1.0.0.tar.gz.sha256
#   dist/artifactflow-config-1.0.0.tar.gz        (config/ — agent prompts + models.yaml)
#   dist/artifactflow-config-1.0.0.tar.gz.sha256
```

> **为什么 config 单独打包：** intranet compose 通过 `../config:/app/config:ro` bind mount 让运维直接在宿主机编辑 prompt / 模型配置，重启容器即可生效（不用 rebuild 镜像）。配置变更频率远高于代码变更，单独打包后日常迭代只需传输几十 KB 的 config tar，不必重新拉 ~500MB 镜像 tar。

### 部署（在目标内网机器上）

```bash
# 1. 传输文件到目标机器（镜像 tar + config tar + deploy/ 目录）
scp dist/artifactflow-1.0.0.tar.gz \
    dist/artifactflow-config-1.0.0.tar.gz \
    deploy/ \
    target:/opt/artifactflow/

# 2. 加载镜像 + 解压 config
cd /opt/artifactflow
docker load < artifactflow-1.0.0.tar.gz
tar xzf artifactflow-config-1.0.0.tar.gz   # 解出 ./config/

# 3. 配置 .env
cp deploy/.env.intranet.example deploy/.env
# 编辑 deploy/.env，填写密码和 API Keys

# 4. 配置内网 LLM（如需）
# 编辑 config/models/models.yaml，设置 base_url 为内部推理端点

# 5. 启动（3A: 自建基础设施）
AF_VERSION=1.0.0 docker compose -f deploy/docker-compose.intranet.yml --profile infra up -d

# 6. 创建管理员
docker compose -f deploy/docker-compose.intranet.yml exec backend \
  python scripts/create_admin.py admin --password <your-password>
```

### 运行时配置变更（无需 rebuild / 重新传镜像）

| 变更类型 | 操作 | 生效命令 |
|---------|------|---------|
| `config/agents/*.md`（agent prompt） | 直接编辑宿主机文件 | `docker compose -f deploy/docker-compose.intranet.yml restart backend` |
| `config/models/models.yaml`（模型 / base_url） | 直接编辑宿主机文件 | 同上 — `restart backend` |
| `config/tools/*.md`（自定义工具） | 直接编辑宿主机文件 | 同上 — `restart backend` |
| `deploy/.env`（任何 `ARTIFACTFLOW_*` 变量） | 直接编辑 | **`up -d backend`**（restart 不会重读 .env，up 会检测 env 变化重建容器） |
| `deploy/nginx.conf` | 直接编辑 | `docker compose -f deploy/docker-compose.intranet.yml restart nginx` |
| `deploy/docker-compose.intranet.yml`（端口、profile 等） | 直接编辑 | `up -d` |

> **关键区别：** 改 `config/*` 用 `restart`，改 `.env` 用 `up -d`。前者只是把进程重新拉起来读文件，后者要让 compose 重新组装容器才能注入新的环境变量。

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
