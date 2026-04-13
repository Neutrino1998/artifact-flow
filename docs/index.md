# ArtifactFlow

> Pi-style 可配置 Agent 引擎 — 面向中小规模团队的多 Agent SaaS 服务

ArtifactFlow 是一个基于扁平 while loop 执行引擎的多 Agent 协作系统。它采用双 Artifact 架构（Task Plan + Result），通过配置化的 Agent/Tool/Model 体系，让团队无需编写代码即可扩展 AI 能力。

## 核心特性

- **扁平 while loop 引擎** — 无框架依赖的 Pi-style 执行循环，call_llm → parse_tool_calls → execute → route，完全透明可调试
- **Agent/Tool/Model 全配置化** — Agent 是 Markdown 文件（YAML frontmatter + role prompt），Model 是 YAML 配置，无需写 Python 即可扩展
- **双 Artifact 架构** — Task Plan Artifact + Result Artifact，write-back cache 机制确保原子性持久化
- **对话树 + Compaction** — 保留分支结构的上下文压缩，支持分支回溯
- **SSE 实时流式 + Permission Interrupt** — fetch + ReadableStream 传输，CONFIRM 级工具触发用户授权中断
- **多数据库 + 可选 Redis 分布式** — SQLite（开发）/ PostgreSQL / MySQL + InMemory / Redis RuntimeStore

## 快速开始

### 前置要求

- Docker & Docker Compose
- 至少一个 LLM API Key（DashScope / OpenAI / DeepSeek）

### 三步启动

```bash
# 1. 克隆项目
git clone <repo-url> && cd artifact-flow

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入：
#   - ARTIFACTFLOW_JWT_SECRET（必填）
#   - LLM API Key（至少一个）
#   - BOCHA_API_KEY（Web 搜索工具）

# 3. 启动服务
docker compose up -d

# 4. 创建管理员账号
docker compose exec backend python scripts/create_admin.py admin --password <your-password>
```

启动后访问：

- 前端：http://localhost:3000
- API 文档：http://localhost:8000/docs

> 这是 Quick Trial 模式（SQLite + InMemory），适合本地试用。生产部署请参考 [部署指南](deployment.md)。

## 环境变量速查

所有环境变量使用 `ARTIFACTFLOW_` 前缀，完整列表见 `src/config.py`。

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `JWT_SECRET` | **是** | — | JWT 签名密钥，`python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成 |
| `DATABASE_URL` | **是** | — | 数据库连接串，如 `sqlite+aiosqlite:///data/artifactflow.db` |
| `REDIS_URL` | 否 | `""` (InMemory) | Redis 连接串，生产环境建议配置 |
| `REDIS_KEY_PREFIX` | 启用 Redis 时必填 | `""` | Redis key 命名空间前缀 |
| `DEBUG` | 否 | `false` | 开启调试日志和详细错误信息 |
| `EXECUTION_TIMEOUT` | 否 | `1800` | 总执行超时（秒） |
| `PERMISSION_TIMEOUT` | 否 | `300` | 单次权限等待超时（秒） |
| `COMPACTION_TOKEN_THRESHOLD` | 否 | `60000` | 触发上下文压缩的 token 阈值 |
| `MAX_CONCURRENT_TASKS` | 否 | `10` | 最大并发引擎执行数 |

LLM 和工具的 API Key 不使用 `ARTIFACTFLOW_` 前缀，直接设置：

| 变量 | 说明 |
|------|------|
| `DASHSCOPE_API_KEY` | 通义千问 API |
| `OPENAI_API_KEY` | OpenAI API |
| `DEEPSEEK_API_KEY` | DeepSeek API |
| `BOCHA_API_KEY` | Bocha Web 搜索 |
| `JINA_API_KEY` | Jina Reader（网页抓取） |

## 文档导航

### 架构

- [架构概览](architecture/overview.md) — 三层模型、请求生命周期、设计决策
- [执行引擎](architecture/engine.md) — Pi-style while loop、Agent 完成路由、Compaction
- [Agent 系统](architecture/agents.md) — Agent-as-Config、协作模型
- [工具系统](architecture/tools.md) — XML 工具调用、权限模型、执行流水线
- [Artifact 架构](architecture/artifacts.md) — 双 Artifact、write-back cache
- [数据层](architecture/data-layer.md) — ORM 模型、对话树、Event Sourcing
- [流式传输](architecture/streaming.md) — SSE 事件体系、双实现
- [并发与运行时](architecture/concurrency.md) — RuntimeStore、租约、中断
- [可观测性](architecture/observability.md) — 事件持久化、Admin API、监控 UI

### 指南

- [添加 Agent](guides/add-agent.md) — 创建自定义 Agent 配置
- [添加 Tool](guides/add-tool.md) — 实现自定义工具
- [添加 Model](guides/add-model.md) — 接入新 LLM Provider
- [API 参考](guides/api-reference.md) — REST API 完整文档

### 运维

- [部署指南](deployment.md) — 五种部署模式、环境变量完整参考

### 前端

- [前端架构](frontend.md) — Next.js 15 + Zustand + SSE 集成
