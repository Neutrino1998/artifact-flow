# ArtifactFlow 分阶段优化计划

基于 code review 反馈和 `docs/architecture/concurrency.md` 演进路线整合。

---

## Phase 1: 核心流程 Bug 修复（ID 一致性 + Permission Resume） ✅ DONE

> **完成于**: commit `c24fdb8` — 统一 ID 生成源（Router 层为权威源，Controller 接受外部传入）、前端 PermissionModal 改从 streamStore 读取 ID、前端处理 metadata 事件做一致性校验、resume 归属校验（thread_id ↔ conversation_id 绑定）。

---

## Phase 2: 安全加固 ✅ DONE

> **完成于**: commit `0e0eb23` — web_fetch SSRF 防护（协议校验 + 私网 IP 拒绝 + permission AUTO→CONFIRM）、持久化 fail fast（移除静默吞异常）、Docker healthcheck（/docs→/health）、错误信息脱敏（`_sanitize_error_event`）。附带修复 permission 前端确认流程。补丁：`65a78f1`、`b1fe826`。

---

## Phase 3: 数据质量改善 (3.1 ✅, 3.2 ✅, 3.3 ⏸️)

> **3.1 & 3.2 完成于**: commit `14f44e6` — 分页 total 真实计数（Repository 层 count 查询）、Artifact created_at 返回 DB 真实时间。
> **补丁**: commit `925ce9f` — list_conversations 去掉 asyncio.gather 避免共享 AsyncSession；create_artifact 缓存构造传入 db_artifact.created_at。

### 3.3 Graph 编译缓存（来自 concurrency.md Phase 3） ⏸️ Deferred

**当前状态**: 暂不实施，先保持“每请求编译 graph”现状，后续在有明确性能瓶颈数据后再推进。

**背景问题**: 当前每个请求都会创建 Agent/Tool/Registry 并编译 StateGraph，存在固定 CPU 开销，影响吞吐。

**现有实现特征（2026-02）**:
- `src/api/dependencies.py` 和 `src/api/routers/chat.py` 的后台任务路径中，均会按请求调用 `create_multi_agent_graph()`
- `artifact_manager`（请求级对象，绑定请求级 DB session）当前通过闭包/实例字段参与 graph 构建
- 该设计在“每请求编译”前提下并发正确性可接受（不会跨请求复用 session），但吞吐较差

**候选改造方案（已调研，未落地）**:
- 目标：启动时编译一次 graph 并缓存为全局单例；请求级 `artifact_manager` 通过 runtime context 注入
- 对齐 LangGraph 推荐模式：`context_schema` + `runtime.context`（context 不进入 checkpoint，resume 时需重新传入）

1. 新增 `GraphContext`
- 新建 `src/core/graph_context.py`
- 定义 dataclass：`GraphContext(artifact_manager: Optional[ArtifactManager])`
- 作用：承载请求级依赖，避免 graph 闭包捕获 request-scoped 对象

2. 改造 `core/graph.py`
- `ExtendableGraph` 移除 `artifact_manager` 构造参数
- `StateGraph` 增加 `context_schema=GraphContext`
- 节点执行时通过 runtime 读取 `artifact_manager`，传入 `ContextManager.build_agent_messages(...)`
- `create_multi_agent_graph()` 改为不接收 `artifact_manager`

3. 改造 `tools/implementations/artifact_ops.py`
- Artifact 工具不再在 `__init__` 持有 manager
- 执行时从 runtime context 获取 manager
- `create_artifact_tools()` 改为无参工厂

4. 改造 `api/dependencies.py`
- 增加 `_compiled_graph` 全局变量
- 在 `init_globals()` 中编译并缓存 graph
- 增加 `get_compiled_graph()` 访问器
- `get_controller()` 改为复用缓存 graph，仅注入请求级 manager

5. 改造 `core/controller.py`
- 所有 graph 调用点（`ainvoke`/`astream`、new/resume）统一传入 `context=GraphContext(...)`

6. 改造 `api/routers/chat.py`
- 两个后台任务（`execute_and_push` / `execute_resume`）改为复用 `get_compiled_graph()`
- 删除任务内重复编译 graph 的逻辑

7. 测试适配
- `tests/test_core_graph.py` / `tests/test_core_graph_stream.py`：测试环境改为“graph 编译一次 + 请求级 manager 复用 graph”

**关键注意事项（必须满足）**:
- `compiled_graph` 绝不能持有 request-scoped 对象（尤其 `ArtifactManager` / `AsyncSession`）
- 开启 graph 缓存后，graph 内绑定的 tool/agent 实例会跨请求复用；所有工具需保证无状态或并发安全
- `web_fetch` 等工具若持有可变实例状态（配置/运行对象），应改为执行期局部创建，避免跨请求状态污染
- 需锁定支持 runtime context 的 LangGraph 版本，避免环境解析到旧版本导致运行时错误
- context 不会持久化到 checkpoint，resume 路径必须每次重新传入 context

**为何先 defer**:
- 当前更关注并发正确性与稳定性，优先避免一次性引入“缓存 + 依赖注入模型切换 + tool 生命周期变化”的复合改动风险
- Phase 5/6（Redis + PostgreSQL 迁移）完成后再结合压测数据评估，收益/风险比更清晰

**后续触发条件（再开启本项）**:
- 有明确数据表明 graph 编译耗时显著影响 p95/p99 或吞吐
- 完成工具无状态化审计（至少 `artifact_ops`、`web_fetch`）
- 有可自动化回归覆盖 new/resume/streaming/并发双会话

---

## Phase 4: 认证框架 ✅ DONE

> **完成于**: commit `8d367ae` — JWT 认证框架（签发/验证、User 模型、get_current_user 依赖注入、所有路由 user_id 过滤、SSE 认证、resume 归属校验、admin 用户管理 API）。前端：登录页、authStore、AuthGuard、401 拦截。CLI：login/logout + token 持久化。补丁：`821f20a`、`b573be1`、`0a06643`。

---

## Phase 5 & 6: 持久化改造 (Redis + 数据库) ✅ DONE

> **已迁移至独立计划**: 详见 [persistence-refactor-plan.md](./persistence-refactor-plan.md)。
>
> 原 P5/P6 的内容在 LangGraph 移除、Pi-style engine 重写后已大幅过时（checkpointer 不再存在、TaskManager 拆为 ExecutionRunner + RuntimeStore、StreamManager 重命名为 StreamTransport Protocol）。独立计划重新整理了三个 Phase：
>
> - **Phase 1**: Redis RuntimeStore — lease/interrupt/cancel/inject 跨 Worker 共享 ✅
> - **Phase 2**: Redis StreamTransport — 跨 Worker 事件推送 + SSE 断线重连 ✅
> - **Phase 3**: 数据库通用适配 — Alembic + 去 SQLite + datetime.now→func.now() ✅
>
> **本文件中以下旧条目的最终处置**:
>
> | 旧条目 | 处置 |
> |--------|------|
> | 5.1 RETURNING 移除 + Alembic | ✅ 已在 persistence Phase 3 完成 |
> | 5.2 Redis Checkpointer | ❌ 作废（LangGraph 已移除，无 checkpointer） |
> | 5.3 StreamManager → Redis Streams | ✅ 已在 persistence Phase 2 完成（RedisStreamTransport） |
> | 5.4 Manager 缓存决策 | 不变（request-local 内存缓存，不迁移 Redis） |
> | 5.5 TaskManager 多 Worker | ✅ 已在 persistence Phase 1 完成（RedisRuntimeStore lease） |
> | 6.1-6.3 PostgreSQL | ✅ 已在 persistence Phase 3 完成（通用适配，支持 TDSQL/MySQL/PostgreSQL） |

---

## Phase 7: 文件上传 → Artifact

### Phase 7A: 文档上传 + content_type 统一 + 前端渲染修正 ✅ DONE

> **完成于**: commit `78df7c4` — content_type 统一为 MIME type、DocConverter 文档转换层（pandoc + pymupdf，双向导入导出）、上传 API（`POST /artifacts/{session_id}/upload`）+ Artifact.source 字段（`"agent"` / `"user_upload"`）、前端渲染策略修正（Preview tab 仅 `text/markdown`）、前端上传 UI（按钮 + 拖拽，无 session 时禁用）、Prompt 设计 Review（artifact inventory 注入 source 属性 + 行为指引）。补丁：`937bb66`、`6fb8e70`、`e243dbf`、`694ba88`。

---

### Phase 7B: 结构化数据 + 原始文件存储（建议 Phase 5/6 之后）

**依赖**: Phase 7A + Phase 5/6（PostgreSQL 大字段支持 + 可能需要对象存储）

**目标**: 支持 csv / json 等结构化数据上传，保留原始内容供代码沙盒处理，不强制转 markdown。

#### 7B.1 结构化数据上传

**涉及文件**:
- `src/api/routers/artifacts.py` — 上传白名单扩展
- `src/tools/utils/doc_converter.py` — 新增 csv / json 处理
- `frontend/src/components/artifact/` — 可能需要表格预览组件

**改动**:
- 新增支持：`.csv`, `.json`（后续可扩展 `.xlsx` 等）
- csv / json **不强制转 markdown**：`content_type` 保持 `"text/csv"` / `"application/json"`，`content` 存原始文本
- 可选生成 markdown 摘要预览（如 csv 前 20 行转 markdown 表格），存到 `metadata.preview_markdown`
- 前端对 `text/csv` 可后续实现表格渲染组件（不在 7B 范围内，可作为独立增强）

#### 7B.2 原始文件存储（待定）

**说明**: 7A 阶段 docx / pdf 转换后只存 markdown 文本，原始文件不保留。7B 评估是否需要：
- 原始文件 BLOB 存储（数据库）或对象存储（S3 / MinIO）
- 原始文件下载功能
- 取决于是否有"重新转换"或"下载原始文件"的需求

#### 7B.3 代码沙盒联动（前置调研）

**说明**: Agent 使用 Python 工具处理 csv / json 的能力依赖独立的代码沙盒功能（sandbox execution），不在 Phase 7 范围内。7B 需确保 artifact 数据格式兼容未来沙盒读取：
- csv artifact 保持原始文本，沙盒可直接 `pd.read_csv(StringIO(content))`
- json artifact 保持原始文本，沙盒可直接 `json.loads(content)`

---

## Phase 8: 用户直接编辑 Artifact

**目标**: 允许用户通过前端直接编辑 Artifact 内容，与 Agent 协作修订。

### 8.1 后端 Artifact 写接口

**涉及文件**:
- `src/api/routers/artifacts.py` — 新增 PUT/PATCH 端点
- `src/api/schemas/` — 新增更新请求 schema（含 `lock_version` 乐观锁）
- `src/tools/implementations/artifact_ops.py` — 新增 `update_by_user()` 方法
- `src/db/models.py` — Artifact 模型已有 `lock_version` 字段

**改动**:
- `PUT /api/v1/artifacts/{session_id}/{artifact_id}` — 全量更新内容
- 请求体包含 `content` + `lock_version`，乐观锁防止并发冲突
- 更新时创建新 version 记录（`update_type = "rewrite"` 或 `"update"`，取决于编辑范围）
- 返回新的 `lock_version` 供前端下次提交使用

### 8.2 前端编辑 UI

**涉及文件**:
- `frontend/src/components/artifact/` — ArtifactPanel 中增加编辑模式
- `frontend/src/lib/api.ts` — 新增更新 API 调用
- `frontend/src/stores/artifactStore.ts` — 编辑状态管理

**改动**:
- Artifact 预览面板增加"编辑"按钮，切换到编辑模式
- 编辑模式：代码类型用 code editor（monaco-editor 或 CodeMirror），文本类型用 textarea
- 保存时带 `lock_version`，冲突时提示用户（409 Conflict → 显示 diff 让用户选择）
- 乐观更新：保存后立即更新本地状态，失败时回滚

---

## Phase 9: Skill 系统

**目标**: 允许用户管理可复用的知识/技能片段（user-scoped，跨所有会话），Agent 在会话中自动或按需加载。

**状态**: 调研完成，方案已确定（轻量独立表），待排入开发计划。

### 9.1 业界调研结论

**Agent Skills 已形成跨平台开放标准**（[agentskills.io](https://agentskills.io/specification)），Claude Code / Copilot / Windsurf / OpenCode 均采用同一规范。Cursor 是唯一例外（自有 `.mdc` 格式）。

**标准 Skill 文件结构**:
```
.claude/skills/<name>/
├── SKILL.md          # 主指令（必须）— YAML frontmatter + markdown body
├── references/       # 引用资料（可选，按需加载）
├── scripts/          # 可执行脚本（可选）
└── assets/           # 模板/配置（可选）
```

**SKILL.md 格式**:
```yaml
---
name: fix-issue
description: Fix a GitHub issue by number.   # 用于自动匹配
disable-model-invocation: true               # 是否禁止模型自动调用
allowed-tools: Bash(gh *), Read, Write       # 执行期间允许的工具
context: fork                                # 是否在隔离子 agent 中运行
model: claude-opus-4                         # 可选模型覆盖
argument-hint: "[issue-number]"              # 自动补全提示
user-invocable: true                         # 是否显示在 / 菜单
---

Markdown body with instructions...
支持 $ARGUMENTS、$0、${CLAUDE_SESSION_ID}、!`command` 变量替换。
```

**核心架构模式——渐进式披露（Progressive Disclosure）**:

所有系统的共同设计：不全量注入所有 skill 到 context。

| 层级 | 加载内容 | 时机 |
|------|---------|------|
| L1 Metadata | name + description（~100 tokens/skill） | 始终加载，嵌入 tool description |
| L2 Body | SKILL.md 全文（~500-5000 tokens） | 用户 `/invoke` 或模型自动匹配时 |
| L3 References | references/ 下的文件 | 执行中按需读取 |

**Claude Code 内部实现**:
1. 注册 `Skill` 元工具，description 嵌入所有 skill 的 L1 metadata（~15K 字符预算）
2. 模型判断相关时调用 `Skill` tool
3. 系统注入一条**隐藏 user message**（含 SKILL.md 全文）到对话
4. 临时修改工具权限和模型覆盖
5. **核心洞察：Skill 本质是 prompt-based context modifier，不是可执行代码——改变模型怎么想，而不是能做什么**

**激活方式对比**:

| 触发方式 | 系统 |
|----------|------|
| 斜杠命令 `/skill-name` | Claude Code, Copilot |
| @-mention `@skill-name` | Windsurf, Cursor |
| 模型自动匹配（基于 description） | Claude Code, Copilot, Windsurf |
| 文件 glob 模式匹配 | Cursor 独有 |
| 始终激活 | CLAUDE.md, Cursor `alwaysApply: true` |

**Scope 层级**（Claude Code）:

| Scope | 路径 | 作用范围 |
|-------|------|---------|
| Enterprise | Managed settings | 全组织用户 |
| Personal | `~/.claude/skills/<name>/SKILL.md` | 个人所有项目 |
| Project | `.claude/skills/<name>/SKILL.md` | 当前项目 |

### 9.2 设计决策

**关键决策：独立 `skills` 表，不复用 Artifact 表。**

| 方案 | 优点 | 缺点 |
|------|------|------|
| 复用 Artifact 表 + 标记 | 零 schema 变更，版本管理免费 | **Scope 不匹配**：Artifact 是 session-scoped（绑 `conversation_id`），Skill 必须 user-scoped 跨所有会话；语义污染 |
| ✅ 独立 Skill 实体 | 语义清晰，天然 user-scoped，独立生命周期 | 新表 + 新 API（但工作量很小） |

**Skill 本质定位**：静态知识注入（system prompt context modifier），不含可执行脚本。ArtifactFlow 的 tool 能力由已有 ToolRegistry 管理，Skill 只负责"指导模型行为"。

### 9.3 数据模型

```python
class Skill(Base):
    __tablename__ = "skills"

    id = Column(String(64), primary_key=True)        # slug, e.g. "coding-standards"
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    name = Column(String(128), nullable=False)        # 显示名
    description = Column(String(1024), nullable=False) # L1 metadata，用于自动匹配
    content = Column(Text, nullable=False)             # L2 markdown body
    is_active = Column(Boolean, default=True)          # 用户可启用/禁用
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
```

### 9.4 API 端点

```
POST   /api/v1/skills           # 创建 skill
GET    /api/v1/skills           # 列出当前用户的 skills
GET    /api/v1/skills/{id}      # 获取 skill 详情
PUT    /api/v1/skills/{id}      # 更新 skill
DELETE /api/v1/skills/{id}      # 删除 skill
PATCH  /api/v1/skills/{id}      # 切换 is_active
```

所有端点 `Depends(get_current_user)`，按 `user_id` 隔离。

### 9.5 Context 注入

**注入链路**:

```
ContextManager.prepare_agent_context()
  → skill_repo.list_active_skills(user_id)
  → context["skills_metadata"]  (L1: name + description)
  → LeadAgent.build_system_prompt(context)
    → <available_skills> section in system prompt
```

**加载策略**（根据 skill 数量选择）:

| 场景 | 策略 |
|------|------|
| 少量 skill（<10） | 全量注入 body 到 system prompt（简单直接） |
| 大量 skill | 仅注入 L1 metadata + 提供 `read_skill` 工具，模型按需调用获取 L2 body |

初始实现走"少量 skill 全量注入"路径，后续按需切到工具模式。

**Subagent 可见性**：Search/Crawl Agent 不加载 skill，仅 Lead Agent 可见。

### 9.6 前端

设置/个人页面中的 Skill 管理面板（独立于 conversation 流程）：
- Skill 列表（名称 + 描述 + 启用/禁用开关）
- 创建/编辑表单（name, description, content markdown 编辑器）
- 删除确认

### 9.7 涉及文件

**新增**:
- `src/db/models.py` — `Skill` 模型
- `src/repositories/skill_repo.py` — Skill CRUD
- `src/api/routers/skills.py` — API 端点
- `src/api/schemas/skill.py` — 请求/响应 schema
- `frontend/src/` — Skill 管理 UI 组件

**修改**:
- `src/core/context_manager.py` — `prepare_agent_context()` 增加 skill 查询和注入
- `src/agents/lead_agent.py` — `build_system_prompt()` 增加 `<available_skills>` section
- `src/api/main.py` — 注册 skills router
- `src/api/dependencies.py` — 注入 skill_repo

**依赖**: 仅依赖 Phase 4（认证，已完成）。独立于 Phase 7/8，可随时实施。

---

## Phase 10: 内网离线部署

**目标**: 实现"外网构建 + 内网运行"的标准化发布流程，支持无外网环境下的镜像交付和离线部署。适用场景：企业私有知识库、对接内网数据库做数据分析/报表等。

**前置依赖**: Phase 5/6（4-service 栈定型后统一改造，避免 compose 反复修改）。

**背景**: 当前 `docker-compose.yml` 是"源码构建部署"模式（service 定义包含 `build`，前端 API 地址通过构建参数写入，默认 Agent 模型指向公网服务）。外网环境可直接构建运行，但在无外网、无内网镜像仓的环境中，`docker compose up` 会遇到：构建依赖无法下载、前端回源地址不匹配、模型调用出网失败。需要把"构建时联网"与"运行时离线"彻底解耦。

**开发者模式**: Phase 5/6 完成后砍掉 SQLite 支持，只保留 PostgreSQL。本地开发采用"基础设施 Docker + 应用本地跑"模式：`docker-compose.dev.yml` 仅包含 PostgreSQL + Redis 两个 service，后端和前端本地直接运行（保留热重载和调试体验）。

### 10.1 应用配置改造 — 模型配置外部化

**目标**: Agent 使用的模型名、推理服务地址、API Key 全部通过环境变量注入，支持运行时从前端切换 Lead Agent 模型。

**设计决策**:
- **单 Provider**：所有模型共享一个推理服务地址（内网典型场景），不做多 Provider
- **手动声明可用模型**（不做自动发现）
- **env var default + per-message override**：管理员通过 env var 设默认模型，用户发消息时可从前端切换 Lead Agent 模型
- **公网模式不受影响**：`LLM_BASE_URL` 不设时走 litellm 默认路由（当前行为）

**新增配置项**（`config.py`，`ARTIFACTFLOW_` 前缀）:

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `LLM_BASE_URL` | 推理服务地址（空 = litellm 默认路由） | `""` |
| `LLM_API_KEY` | 推理服务 API Key | `""` |
| `LLM_AVAILABLE_MODELS` | 逗号分隔的可用模型 ID（空 = 用 `MODEL_CONFIGS` 预设） | `""` |
| `LEAD_MODEL` | Lead Agent 默认模型 | `"qwen3.5-plus"` |
| `WORKER_MODEL` | Search/Crawl Agent 默认模型 | `"qwen3.5-flash-no-thinking"` |

**两种运行模式**（由 `LLM_BASE_URL` 是否设置自动切换）：

| | 公网模式（当前行为） | 内网模式 |
|---|---|---|
| 可用模型来源 | `MODEL_CONFIGS` 预设 keys | `LLM_AVAILABLE_MODELS` 配置 |
| 模型路由 | litellm 按 provider 前缀路由 | 全部走 `LLM_BASE_URL` |
| API Key | 各 provider 独立 env var | 统一 `LLM_API_KEY` |

**改造链路**（从底向上穿透）:

1. **`models/llm.py`** — `create_llm()` 自动注入全局 Provider（调用方未指定 `base_url` 时）；`get_available_models()` 按模式返回不同来源；保留 `MODEL_CONFIGS` 不动（公网模式的 extra_params 仍需要）
2. **Agent 工厂** — `create_lead_agent(model=)` 参数化，默认值改为 `config.LEAD_MODEL`；Search/Crawl 同理用 `config.WORKER_MODEL`
3. **`graph.py`** — `create_multi_agent_graph(lead_model=)` 新增参数，传入 lead agent 工厂。Search/Crawl 不暴露 override（执行层模型由管理员统一配置）
4. **API 层** — `ChatRequest` 增加可选 `model` 字段；router 校验 model 在可用列表中后传入 graph；新增 `GET /api/v1/models` 端点返回可用模型列表 + 当前默认值
5. **前端** — Chat 输入区域增加模型选择下拉，页面加载时从 `/models` 获取列表，发送消息时附带选中的 model

**涉及文件**:

- **修改**: `config.py`, `models/llm.py`, `lead_agent.py`, `search_agent.py`, `crawl_agent.py`, `graph.py`, `schemas/chat.py`, `routers/chat.py`, `main.py`, `frontend/src/lib/api.ts`, `frontend/src/components/chat/`, `.env.example`
- **新增**: `routers/models.py`, `schemas/model.py`

**退出标准**:
- 内网模式：设置 `LLM_BASE_URL` + `LLM_AVAILABLE_MODELS` → agent 调用走指定推理服务
- 公网模式：不设 `LLM_BASE_URL` → 行为与改造前完全一致
- 前端可选模型、per-message override 生效
- `validate_config()` 在内网模式下校验默认模型在可用列表中

---

### 10.2 外网构建发布流程

**目标**: `scripts/release.sh` 一键完成版本化构建 → 冒烟验证 → 镜像导出 → sha256 校验。

**流程**: 版本号传入 → 构建 backend/frontend/nginx 三镜像（带 version label）→ 启动全栈跑健康检查 → `docker save` 导出五个 tar（含 postgres + redis-stack 基础设施镜像）→ 生成 `checksums.sha256`。

**前端 `NEXT_PUBLIC_API_URL` 处理**: 推荐每个部署环境单独构建前端镜像（方案 C）——前端镜像轻量（~50MB），`NEXT_PUBLIC_*` 是 Next.js 编译时变量，运行时替换都是 workaround。

**退出标准**:
- `release.sh` 一键完成全流程
- 导出的 tar 可在全新机器上 `docker load` 成功

---

### 10.3 内网 Compose + 配置模板

**目标**: `deploy/docker-compose.intranet.yml`（纯 `image`，无 `build`）+ `deploy/.env.intranet.example`（配置模板）。

**关键设计**:
- 五个 service：nginx / backend / frontend / postgres / redis-stack，全部仅 `image` 引用
- Nginx 作为唯一对外入口，按路径分流：`/api/*` → backend:8000，`/*` → frontend:3000
- backend 和 frontend **不映射端口到宿主机**，仅通过 Docker 内部网络与 Nginx 通信
- 健康检查链：redis/postgres healthy → backend healthy → frontend 启动 → nginx 启动
- 三个 named volume 持久化（backend_data / postgres_data / redis_data）
- DB 连接串在 compose 内拼接，敏感值（JWT secret、PG password、LLM key）从 `.env` 读取
- 对外端口可配（`HTTP_PORT`，默认 80）

**Nginx 配置要点**:
- 路径分流：`/api/` 和 `/health` → backend，其余 → frontend
- SSE 支持：`/api/v1/stream/` 路径关闭 `proxy_buffering`，设长超时
- 安全：屏蔽 `/docs` 和 `/redoc`（Swagger UI），仅内部访问
- 可选限流：`limit_req_zone` 按 IP 限制 API 请求频率
- 后续加 HTTPS：只需在 Nginx 配置中增加 SSL 证书，后端零改动

**`.env.intranet.example` 必填项**: VERSION、JWT_SECRET、POSTGRES_PASSWORD、LLM_BASE_URL、LLM_API_KEY、LLM_AVAILABLE_MODELS、LEAD_MODEL、WORKER_MODEL。

**退出标准**:
- 纯离线环境（镜像已 load）`docker compose up -d` 五个 service 全部 healthy
- 通过 Nginx 端口访问前端可正常登录，API 请求正确转发
- 直接访问 backend/frontend 端口不可达（未映射到宿主机）
- `/docs` 路径被 Nginx 拦截，返回 403/404

---

### 10.4 内网部署 SOP

**目标**: `deploy/deployment-guide.md`，运维人员无需理解技术栈即可完成部署。

**大纲**: 前置要求（Docker 24+ / 4C8G / OpenAI 兼容推理服务已部署）→ 镜像导入（sha256 校验 + docker load）→ 配置（复制 .env 模板 + 修改必填项）→ 启动 → 创建管理员 → 验证。附日常运维（日志 / 备份 / 升级）。

**退出标准**: 按手册操作可在全新机器上完成完整部署。

---

### 10.5 交付物清单

```
artifactflow-release-${VERSION}/
├── images/          # 五个 tar + checksums.sha256（nginx / backend / frontend / postgres / redis-stack）
├── compose/         # docker-compose.intranet.yml + .env.intranet.example + nginx.conf
├── scripts/         # load-images.sh（docker load 一键脚本）
└── docs/            # deployment-guide.md
```

---

## Phase 11: 可观测性 — 结构化日志 + 运维界面

**目标**: 完整记录 Agent 执行链路（类似 LangSmith 的 trace 能力），支持按对话回溯完整的 prompt → response → tool call → tool result 链路。数据自托管，不依赖外部服务。

### 设计思路

**双通道日志架构**:

| 通道 | 内容 | 存储 | 用途 |
|------|------|------|------|
| stdout / 文件 | ERROR、启动/关闭、关键状态变更 | 本地文件（永远可用） | 兜底排障，数据库挂了也能看 |
| DB LogEntry | 完整执行链路（system prompt、LLM 原始输出、tool 入参/返回、token usage、耗时） | PostgreSQL | 可查询、可视化、按 conv_id 回溯 |

DB 写入失败时静默忽略，绝不影响主流程。现有 `utils/logger.py` 保留并输出到 stdout，DB handler 作为附加通道。

**数据模型**（一张表）:

```sql
CREATE TABLE execution_logs (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ DEFAULT now(),
    level VARCHAR(10),          -- INFO / WARN / ERROR
    conv_id UUID,
    request_id VARCHAR(64),     -- 请求链路追踪
    user_id UUID,
    agent VARCHAR(32),          -- lead / search / crawl
    tool VARCHAR(64),
    message TEXT,
    extra JSONB                 -- duration_ms, token_count, 原始 prompt/response 等
);
CREATE INDEX idx_logs_conv_id ON execution_logs(conv_id, ts);
```

**记录的关键节点**: agent_start（system prompt 全文）、llm_complete（模型原始输出、token usage）、tool_start（工具名 + 完整入参）、tool_complete（原始返回 + 耗时）、error（完整 traceback）。与现有 `ExecutionMetrics`（摘要统计）互补 — metrics 记"调了几次花了多久"，LogEntry 记"具体输入了什么输出了什么"。

**运维界面**: 复用现有前端技术栈和布局模式，不引入独立运维系统。核心思路 — 现有三栏布局的"管理员视图"：

- **左栏**: 对话列表（复用 Sidebar 组件，增加用户名/状态筛选）
- **中栏**: 点击对话后展示日志流而非聊天气泡 — `LogEntry` 组件替代 `MessageBubble`，每条日志显示时间戳 + level + agent/tool + message，level 颜色区分（INFO 灰、WARN 黄、ERROR 红）
- **右栏**: 点击某条日志展开详情（完整 prompt、LLM 原始输出、tool 入参/返回的 JSON 展开）
- **顶部**: level 过滤器 + 时间范围选择 + conv_id / user_id 搜索

API: `GET /api/v1/logs?conv_id=xxx&level=ERROR&user_id=xxx`，admin 权限。

**注意事项**: 日志写入用 fire-and-forget 或批量 insert，不阻塞主流程；设 retention 定期清理（如 30 天）；开发时 console 人类可读格式，生产时同时写表。

---

## 各 Phase 依赖关系

```
Phase 1 (核心 Bug)          ✅ 已完成
Phase 2 (安全加固)          ✅ 已完成
Phase 3 (数据质量)          ← 3.1/3.2 ✅, 3.3 ⏸️
Phase 4 (认证框架)          ✅ 已完成
Phase 5/6 (持久化改造)       ✅ 已完成 → 见 persistence-refactor-plan.md
Phase 7A (文档上传)          ✅ 已完成
Phase 7B (结构化数据)        ← 依赖 7A ✅ + Phase 5/6 ✅
Phase 8 (编辑 Artifact)      ← 依赖 7A ✅（上传和编辑共享写接口模式）
Phase 9 (Skill 系统)         ← 仅依赖 Phase 4（认证），独立于 Phase 7/8，可随时实施
Phase 10 (内网离线部署)      ← 依赖 Phase 5/6 ✅
  10.1 模型配置外部化          ← 无外部依赖
  10.2 外网构建发布流程        ← 依赖 10.1
  10.3 内网 Compose            ← 依赖 10.2
  10.4 内网部署 SOP            ← 依赖 10.3
  10.5 交付物清单              ← 依赖 10.2 + 10.3 + 10.4
Phase 11 (可观测性)            ← 依赖 Phase 5/6 ✅
```

建议执行顺序: **Phase 4 ✅ → 7A ✅ → 5/6 ✅ → 10 → 11 → 7B → 8**。Phase 9 可随时排入开发。

关键路径: **10.1 → 10.2 → 10.3**（Phase 5/6 已完成，10 的前置依赖已就绪）。

---

## 备注

- Phase 1-3 是纯修复，不引入新依赖，风险最低
- Phase 4 是第一个需要前端大改的阶段（登录页 + token 管理）
- Phase 5/6 持久化改造已全部完成，详见 `persistence-refactor-plan.md`
- Phase 7A ✅ 已完成（content_type 统一、文档转换、上传 API/UI、渲染策略修正、Prompt Review）
- Phase 7B 的前置依赖 Phase 5/6 已完成，可随时排入
- Phase 9 Skill 系统调研已完成，方案已确定（轻量独立 `skills` 表），仅依赖 Phase 4（已完成），可随时排入开发
- Phase 10 内网部署改造的前置依赖 Phase 5/6 已就绪，10.1 模型配置外部化是最关键的子项
- **数据迁移**: 系统处于开发阶段，不需要考虑旧数据迁移
- concurrency.md 中已标记 ✅ 的项目（短事务、日志上下文、SSE Heartbeat 等）不在此计划中
