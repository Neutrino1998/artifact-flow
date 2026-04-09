# ArtifactFlow 部署 SOP

> 三种部署模式的操作手册。README 的「快速开始」覆盖 Mode 1，本文档覆盖全部模式。

## 部署模式总览

| Mode | 场景 | 服务 | 数据库 | Compose 文件 |
|------|------|------|--------|-------------|
| **1: Quick Trial** | 本地试用 | backend + frontend | SQLite + InMemory | `docker-compose.yml` |
| **2A: Prod 自建** | 生产 + 自建基础设施 | nginx + backend + frontend + PG + Redis | 容器化 | `docker-compose.prod.yml --profile infra` |
| **2B: Prod 云数据库** | 生产 + RDS/ElastiCache | nginx + backend + frontend | 外部托管 | `docker-compose.prod.yml` |
| **3A: 内网 自建** | 离线/内网部署 | 同 2A | 容器化 | `deploy/docker-compose.intranet.yml --profile infra` |
| **3B: 内网 托管DB** | 离线 + 内部DB服务 | nginx + backend + frontend | 内部托管 | `deploy/docker-compose.intranet.yml` |

**关键区别：**
- Mode 2 vs 1：Nginx 反向代理（单端口 80）、PG + Redis 持久化、Alembic 自动迁移
- Mode 3 vs 2：`image:` 替代 `build:`，通过 `docker save/load` 离线部署，无需访问外部镜像仓库
- 2A/3A vs 2B/3B：`--profile infra` 控制是否启动 PG/Redis 容器

---

## Mode 1: Quick Trial

最简部署，SQLite 存储 + InMemory 运行时，适合本地试用和开发。

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
# API:  http://localhost:8000/docs
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
#    - LLM API Keys（至少一个）
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
docker compose -f docker-compose.prod.yml exec backend python scripts/create_admin.py admin --password <your-password>
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
#   dist/artifactflow-1.0.0.tar.gz        (~500MB, 含全部 5 个镜像)
#   dist/artifactflow-1.0.0.tar.gz.sha256  (校验文件)
```

### 部署（在目标内网机器上）

```bash
# 1. 传输文件到目标机器
scp dist/artifactflow-1.0.0.tar.gz deploy/ target:/opt/artifactflow/

# 2. 加载镜像
docker load < artifactflow-1.0.0.tar.gz

# 3. 配置
cp deploy/.env.intranet.example deploy/.env
# 编辑 deploy/.env，填写密码和 API Keys
# 内网 LLM：编辑 config/models/models.yaml，设置 base_url 为内部推理端点

# 4. 启动（3A: 自建基础设施）
AF_VERSION=1.0.0 docker compose -f deploy/docker-compose.intranet.yml --profile infra up -d

# 5. 创建管理员
docker compose -f deploy/docker-compose.intranet.yml exec backend python scripts/create_admin.py admin --password <your-password>
```

---

## 运维参考

### 数据库迁移

- **自动迁移**：backend 容器启动时 entrypoint 自动执行 `alembic upgrade head`（仅 PG/MySQL，SQLite 跳过）
- **多副本安全**：通过 PG `pg_advisory_lock` 保证只有一个副本执行迁移，其他副本等待并验证 schema 到位后再启动
- **迁移失败**：leader 失败后 follower 检测到 schema 未到 head，拒绝启动；容器 restart policy 会重试

### Nginx 配置

- 配置文件：`deploy/nginx.conf`
- SSE 流式连接：`/api/v1/stream/` 路径关闭 `proxy_buffering`，超时 1800s
- Swagger 文档：生产环境下 `/docs`、`/redoc`、`/openapi.json` 返回 404
- `--scale` 支持：使用 Docker 内部 DNS resolver `127.0.0.11`

### 健康检查

| 端点 | 用途 | 检查内容 |
|------|------|----------|
| `/health/live` | 存活探测（Mode 1 / K8s liveness） | 进程存活 |
| `/health/ready` | 就绪探测（Mode 2/3 / K8s readiness） | 进程 + DB + Redis 连通性 |

### 数据卷

| 卷名 | 用途 |
|------|------|
| `artifactflow_data` | SQLite 数据库 / 上传文件 |
| `postgres_data` | PostgreSQL 数据（Mode 2A/3A） |
| `redis_data` | Redis AOF 持久化（Mode 2A/3A） |

### 日志

```bash
# 查看所有服务日志
docker compose -f <compose-file> logs -f

# 单服务日志
docker compose -f <compose-file> logs -f backend

# 开启 debug 日志：.env 中设置 ARTIFACTFLOW_DEBUG=true
```
