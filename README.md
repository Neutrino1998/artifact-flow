# ArtifactFlow

> Pi-style 可配置 Agent 引擎 + 双 Artifact 架构的多智能体 SaaS

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![SQLite/PostgreSQL](https://img.shields.io/badge/SQLite%20%7C%20PostgreSQL-Persistent-blue.svg)]()
[![Development Status](https://img.shields.io/badge/Status-Alpha%20Development-orange.svg)]()

ArtifactFlow 是一个基于扁平 while loop 执行引擎的多 Agent 协作系统。采用双 Artifact 架构（Task Plan + Result），通过配置化的 Agent / Tool / Model 体系，让团队无需编写代码即可扩展 AI 能力。执行引擎参考 [Pi-mono](https://github.com/badlogic/pi-mono) 设计。

## 预览

**Web UI** — 三栏布局：侧边栏对话列表、聊天面板（流式渲染 + 分支导航）、Artifact 面板（Markdown / Source / Diff）

![Screenshot](docs/assets/screenshot.png)

**CLI** — 终端交互模式，实时展示 Agent 协作过程和工具调用

![CLI](docs/assets/cli_screenshot.png)

## 核心特性

- **扁平 while loop 引擎** — 无框架依赖的 Pi-style 执行循环，call_llm → parse_tool_calls → execute → route，完全透明可调试
- **Agent / Tool / Model 全配置化** — Agent 是 Markdown 文件（YAML frontmatter + role prompt），Model 是 YAML 配置，无需写 Python 即可扩展
- **双 Artifact 架构** — Task Plan Artifact + Result Artifact，write-back cache 机制确保原子性持久化
- **对话树 + Compaction** — 保留分支结构的上下文压缩，支持分支回溯
- **SSE 实时流式 + Permission Interrupt** — fetch + ReadableStream 传输，CONFIRM 级工具触发用户授权中断
- **多数据库 + 可选 Redis 分布式** — SQLite（开发）/ PostgreSQL / MySQL + InMemory / Redis RuntimeStore

## 快速开始

### 前置要求

- Docker & Docker Compose（推荐方式）或 Python 3.11+
- 至少一个 LLM API Key（默认 Agent 配置使用 DashScope / 通义千问，可在 `config/agents/*.md` 中改用 `gpt-4o`、`deepseek-chat` 等内置 alias）
- `BOCHA_API_KEY` — Web 搜索工具所需

### 方式一：Docker 部署（推荐）

SQLite + InMemory，适合本地试用。

```bash
git clone https://github.com/Neutrino1998/artifact-flow.git
cd artifact-flow

cp .env.example .env
# 编辑 .env，至少填入：
#   ARTIFACTFLOW_JWT_SECRET  (python -c "import secrets; print(secrets.token_urlsafe(32))" 生成)
#   DASHSCOPE_API_KEY        (或改用其他 provider 对应的 key)
#   BOCHA_API_KEY

docker compose up -d
docker compose exec backend python scripts/create_admin.py admin --password <your-password>
```

访问：前端 http://localhost:3000 / API 文档 http://localhost:8000/docs （需 `ARTIFACTFLOW_DEBUG=true`）

> **生产部署**（PostgreSQL + Redis + 多副本）详见 [部署指南](docs/deployment.md)。

### 方式二：本地安装

适合需要修改代码或进行开发的场景。

```bash
git clone https://github.com/Neutrino1998/artifact-flow.git
cd artifact-flow

# 创建虚拟环境
conda create -n artifact-flow python=3.11 && conda activate artifact-flow
# 或 python3 -m venv .venv && source .venv/bin/activate

# 系统依赖（用于 doc_converter）
brew install pandoc          # macOS
# sudo apt-get install -y pandoc   # Ubuntu/Debian

pip install -e .

cp .env.example .env
echo "ARTIFACTFLOW_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env
# 编辑 .env 填入其余 API Keys

python scripts/create_admin.py admin --password admin
python run_server.py         # 加 --reload 开启热重载
```

启动后 CLI 交互：

```bash
python run_cli.py login
python run_cli.py chat                # 交互模式
python run_cli.py chat "帮我调研一下 LLM Agent 框架"
```

## 环境变量

核心配置：

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `ARTIFACTFLOW_JWT_SECRET` | **是** | — | JWT 签名密钥 |
| `ARTIFACTFLOW_DATABASE_URL` | **是** | — | DB 连接串（SQLite / PostgreSQL / MySQL） |
| `ARTIFACTFLOW_REDIS_URL` | 否 | `""` (InMemory) | 生产建议配置 |
| `ARTIFACTFLOW_DEBUG` | 否 | `false` | 调试日志 + Swagger |
| `DASHSCOPE_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` | 至少一个 | — | LLM provider |
| `BOCHA_API_KEY` | Web 搜索时必填 | — | 博查 AI |
| `JINA_API_KEY` | 否 | — | Jina Reader（网页抓取限额提升） |

完整列表见 [部署指南 - 环境变量完整参考](docs/deployment.md#环境变量完整参考)。

## 自定义配置

所有运行时配置集中在 `config/` 目录，文件自带注释和示例：

| 配置 | 文件 | 说明 |
|------|------|------|
| **模型** | `config/models/models.yaml` | 基于 [LiteLLM](https://github.com/BerriAI/litellm) 支持 100+ provider，含 Ollama/vLLM 自部署示例 |
| **Agent** | `config/agents/*.md` | YAML frontmatter（模型、工具权限）+ 角色提示词 |
| **自定义工具** | `config/tools/*.md` | YAML frontmatter（HTTP 端点、参数）+ 使用说明，参考 `_example.md` |

扩展方法详见 [添加 Agent](docs/guides/add-agent.md) / [添加 Tool](docs/guides/add-tool.md) / [添加 Model](docs/guides/add-model.md)。

## 项目结构

```
artifact-flow/
├── src/
│   ├── core/          # Pi-style 引擎、Controller、Compaction、Context Manager
│   ├── agents/        # Agent 加载器（MD + YAML frontmatter）
│   ├── tools/         # 工具基类、XML 解析、builtin 工具、自定义 HTTP 工具
│   ├── db/            # SQLAlchemy ORM + Alembic 迁移
│   ├── repositories/  # 数据访问层（Conversation / Artifact / User / MessageEvent）
│   ├── models/        # LiteLLM 统一 LLM 接口
│   └── api/           # FastAPI routers / schemas / services（SSE、RuntimeStore、JWT）
├── cli/               # Typer + Rich CLI
├── frontend/          # Next.js 15 + Zustand + Tailwind
├── config/            # agents/ models/ tools/（运行时只读）
├── scripts/           # export_openapi / create_admin
├── tests/             # repositories / api / concurrent / manual
└── docs/              # MkDocs 文档站源码
```

## 测试

```bash
pytest                           # 全部
pytest tests/repositories/       # Repository 合约测试
pytest tests/api/                # API 集成测试
pytest tests/test_concurrent.py  # 并发测试
```

手动 / 交互式测试（需 LLM 后端）：

```bash
python -m tests.manual.engine               # 多轮对话、Artifact、权限、分支
python -m tests.manual.litellm_providers    # LLM provider 兼容性
```

## 文档

完整文档见 **[Wiki](https://neutrino1998.github.io/artifact-flow/)**：

- [架构概览](https://neutrino1998.github.io/artifact-flow/architecture/overview/) · [执行引擎](https://neutrino1998.github.io/artifact-flow/architecture/engine/) · [Agent 系统](https://neutrino1998.github.io/artifact-flow/architecture/agents/) · [工具系统](https://neutrino1998.github.io/artifact-flow/architecture/tools/)
- [Artifact](https://neutrino1998.github.io/artifact-flow/architecture/artifacts/) · [数据层](https://neutrino1998.github.io/artifact-flow/architecture/data-layer/) · [流式传输](https://neutrino1998.github.io/artifact-flow/architecture/streaming/) · [并发](https://neutrino1998.github.io/artifact-flow/architecture/concurrency/) · [可观测性](https://neutrino1998.github.io/artifact-flow/architecture/observability/)
- [添加 Agent](https://neutrino1998.github.io/artifact-flow/guides/add-agent/) · [添加 Tool](https://neutrino1998.github.io/artifact-flow/guides/add-tool/) · [添加 Model](https://neutrino1998.github.io/artifact-flow/guides/add-model/) · [API Reference](https://neutrino1998.github.io/artifact-flow/guides/api-reference/)
- [部署指南](https://neutrino1998.github.io/artifact-flow/deployment/) · [前端架构](https://neutrino1998.github.io/artifact-flow/frontend/)

## 支持与反馈

- [问题反馈](https://github.com/Neutrino1998/artifact-flow/issues)
- [讨论交流](https://github.com/Neutrino1998/artifact-flow/discussions)
- [联系作者](mailto:1998neutrino@gmail.com)
