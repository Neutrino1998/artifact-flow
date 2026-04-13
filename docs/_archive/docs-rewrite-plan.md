# Docs 重写计划

> 产品定位：扁平 Pi-style 可配置 Agent 引擎 + 高可用设计的中小规模 SaaS 服务

## 目标结构

```
docs/
├── index.md                     # 项目概述 + 快速开始
├── deployment.md                # 部署指南
├── architecture/
│   ├── overview.md              # 整体架构 + 三层责任模型
│   ├── engine.md                # Pi-style 引擎
│   ├── agents.md                # Agent 配置化系统
│   ├── tools.md                 # 工具系统
│   ├── artifacts.md             # Artifact 双层架构
│   ├── data-layer.md            # 数据层
│   ├── streaming.md             # SSE 流式传输
│   └── concurrency.md           # 并发与运行时
├── guides/
│   ├── add-agent.md             # 添加新 Agent
│   ├── add-tool.md              # 添加新 Tool
│   ├── add-model.md             # 添加新模型
│   └── api-reference.md         # API 参考
├── frontend.md                  # 前端架构
└── _archive/                    # 历史文档（保留）
```

---

## PR 分批计划

### PR 1: 骨架搭建 + 清理旧文件 + 核心概述

**范围：** index.md, architecture/overview.md, deployment.md

**删除旧文件：**
- docs/api.md, extension-guide.md, faq.md, streaming.md, request-lifecycle.md, frontend.md
- docs/architecture/ 下全部旧文件（core.md, agents.md, tools.md, data-layer.md, concurrency.md）
- docs/assets/ 下旧截图（如需要后续重新截取）
- 将 _archive/deployment-sop.md 内容整合到新 deployment.md

**index.md 内容要点：**
- 产品定位：Pi-style 可配置 Agent 引擎 SaaS
- 核心特性一句话列表：
  - 扁平 while loop 引擎（无框架依赖）
  - Agent/Tool/Model 全配置化（YAML + Markdown）
  - 双 Artifact 架构（Task Plan + Result）
  - 对话树 + Compaction（保留分支结构）
  - SSE 实时流式 + Permission Interrupt
  - SQLite/MySQL/PostgreSQL + 可选 Redis 分布式
- 快速开始（Docker Compose 一键启动）
- 环境变量速查表（核心项）

**architecture/overview.md 内容要点：**
- 整体架构图（文字版，标注三层：Router → Manager → Repository）
- 请求生命周期：POST /chat → Controller → Engine Loop → SSE → Flush
- 配置化扩展点总览：models.yaml, config/agents/*.md, tools/builtin/
- 信号流图：用户视角的完整交互流程
- **Design Decisions：**
  - 为什么选 Pi-style flat loop（vs LangGraph/middleware）
  - 三层模型的边界划分原则
  - 404 not 403 安全策略

**deployment.md 内容要点：**
- 开发模式：docker-compose up
- 生产部署：MySQL/PG + Redis 配置
- 环境变量完整参考（分组：核心/数据库/Redis/JWT/SSE/Compaction/上传）
- 数据库迁移：entrypoint.sh 自动 Alembic（PG/MySQL），SQLite 跳过
- 健康检查：GET /health/live
- 部署变体：docker-compose.intranet.yml

---

### PR 2: 引擎 + Agent + 工具（架构核心三件套）

**范围：** architecture/engine.md, architecture/agents.md, architecture/tools.md

**architecture/engine.md 内容要点：**
- Pi-style while loop 详解：
  - 核心状态：current_agent, completed, error, cancelled
  - 每次循环：build context → LLM call → parse tool calls → serial execute → emit events
  - 无跨迭代状态持久化，每轮 ContextManager.build() 重新生成
- Agent 完成路由（不对称设计）：
  - Lead 无工具调用 → completed = True（退出）
  - Subagent 无工具调用 → 打包为 call_subagent tool_result，切回 Lead
  - Lead 有 pending messages → 继续循环
- 上下文加载策略：
  - 只加载 Conversation + Message（不加载 MessageEvent）
  - 保证上下文干净，避免 event 膨胀
- Compaction 机制：
  - 触发条件：COMPACTION_TOKEN_THRESHOLD（默认 60k）
  - 保留策略：最近 N 对保留（COMPACTION_PRESERVE_PAIRS=2）
  - 保留对话树结构（parent_id 不变）
  - compact_agent 内部 Agent 生成摘要
- EngineHooks：check_cancelled, wait_for_interrupt, drain_messages
- 可观测性：per-turn token 统计、LLM/Tool 耗时
- **Design Decisions：**
  - 为什么 flat loop 而非 graph/DAG（可调试性、透明性）
  - 为什么上下文只加载 conversation 不加载 events（上下文窗口效率）
  - 为什么 compaction 保留树结构（分支回溯能力）

**architecture/agents.md 内容要点：**
- Agent-as-Config 理念：无需写 Python，一个 MD 文件 = 一个 Agent
- YAML Frontmatter Schema：
  - name, description, model, max_tool_rounds, internal
  - tools: {tool_name: auto/confirm}（per-agent 权限覆盖）
- Role Prompt 设计：MD body 作为 system prompt
- 现有 Agent 概览：
  - lead_agent：协调者，任务规划，Artifact 管理，call_subagent 路由
  - search_agent：Web 搜索专家（web_search, AUTO, max 3 rounds）
  - crawl_agent：网页内容提取（web_fetch, CONFIRM, max 3 rounds）
  - compact_agent：内部 Agent，生成对话摘要（无工具，internal=true）
- Agent 协作模型：Lead 分发 → Sub 执行 → 结果回传 Lead
- Agent 注册与加载：load_all_agents() 扫描 config/agents/
- **Design Decisions：**
  - 为什么 Agent 是数据不是类（降低扩展门槛，热加载）
  - 完成路由不对称性的意图（Lead 是唯一出口）

**architecture/tools.md 内容要点：**
- XML 工具调用格式：
  - LLM 输出：`<tool_call><name>...</name><params>...</params></tool_call>`
  - 参数值 CDATA 包裹：`<param><![CDATA[value]]></param>`
  - 容错解析：缺失闭合标签、畸形 XML 的 regex fallback
- ToolPermission 二级模型：AUTO / CONFIRM
  - Agent 级权限覆盖
  - CONFIRM 触发 RuntimeStore.create_interrupt()
  - 超时和客户端断开 = deny
- 工具执行流水线：
  - 参数类型强转（XML string → int/bool/float）
  - 默认值填充、必填/未知参数校验、enum 约束
  - 串行执行（非并行），call_subagent 排最后
- 内置工具清单：
  - web_search（Bocha AI API）
  - web_fetch（Jina Reader）
  - create/update/rewrite/read_artifact
  - call_subagent（Lead 专用）
- Tool Metadata：tool_result.metadata 可携带 artifact_snapshot（实时前端更新）
- 工具指令生成：to_xml_example() → generate_tool_instruction() → 注入 system prompt
- **Design Decisions：**
  - 为什么选 XML 而非 JSON（文本编辑场景鲁棒性，CDATA 避免转义地狱）
  - 为什么串行执行工具（Permission Interrupt 天然插入点）
  - 为什么 call_subagent 排最后（确保当前轮工具先完成）

---

### PR 3: Artifact + 数据层 + 并发

**范围：** architecture/artifacts.md, architecture/data-layer.md, architecture/concurrency.md

**architecture/artifacts.md 内容要点：**
- 双 Artifact 架构：Task Plan Artifact + Result Artifact
- ArtifactSession：绑定 conversation_id，Artifact 隔离
- Write-Back Cache 机制：
  - 引擎执行期间：create/update/rewrite 只改内存 + mark dirty
  - flush_all() 在 Controller 后处理中一次性持久化
  - 结果：ArtifactVersion 号稀疏（中间编辑折叠）
- list_artifacts() 合并 DB + 内存缓存
- Artifact Operations：create, update, rewrite, read
- 内容类型：text/markdown, text/x-python, text/javascript 等
- ToolResult.metadata 携带 artifact_snapshot（实时 SSE 推送）
- 模糊匹配：Unicode 归一化、智能引号、CJK-Latin 空格处理
- Upload 走 create_from_upload，绕过 write-back 直接提交
- **Design Decisions：**
  - 为什么 write-back 而非即时写入（原子性 + 减少 DB 写入）
  - 为什么版本号稀疏（同一轮多次编辑只保留最终快照）

**architecture/data-layer.md 内容要点：**
- ORM 模型一览（含字段说明）：
  - User, Conversation, Message, ArtifactSession, Artifact, ArtifactVersion, MessageEvent
- 对话树结构：Message.parent_id → 分支，Conversation.active_branch → 当前叶
- Event Sourcing：
  - MessageEvent 表：append-only，event_type + agent_name + data(JSON)
  - llm_chunk 仅 SSE 传输不持久化，llm_complete 持久化完整内容
- Repository 模式：
  - BaseRepository[T] 泛型 CRUD
  - ConversationRepository：对话/消息树/Artifact Session
  - 异常：NotFoundError, DuplicateError
- 事务所有权：
  - DatabaseManager.session() 只管生命周期
  - Repository 内 flush() + commit() 控制写锁
  - 批量 UPDATE 用于 DB-side 计算值（func.now()）
- ORM 使用规范：
  - server_default 创建时间戳，onupdate 更新时间戳
  - ORM 实例短生命周期，过期后不可访问属性（MissingGreenlet）
  - 不将 SQL 表达式赋值给 ORM 属性
- **Design Decisions：**
  - 为什么 404 not 403（不泄露资源存在性）
  - 为什么事务控制在 Repository 而非 session context manager（缩短写锁）

**architecture/concurrency.md 内容要点：**
- DatabaseManager：
  - AsyncSession + async_sessionmaker
  - SQLite(WAL) / MySQL / PostgreSQL
  - 连接池：pool_size, max_overflow, pool_recycle, pool_pre_ping
  - with_retry() 分布式重试
- RuntimeStore（对话租约 + 中断管理）：
  - try_acquire_lease() / release_lease()（阻止并发 POST /chat）
  - mark_engine_interactive() / clear_engine_interactive()
  - Interrupt：wait_for_interrupt() ↔ resolve_interrupt()
  - 取消：request_cancel() / is_cancelled()
  - 消息队列：inject_message() / drain_messages()
- 两种实现：
  - InMemoryRuntimeStore（单进程，dict + asyncio.Event）
  - RedisRuntimeStore（分布式，Redis key + TTL 续期，支持 Cluster）
- **Design Decisions：**
  - 为什么 Permission Interrupt 用 asyncio.Event 阻塞（简单可靠，超时即拒绝）
  - 为什么租约而非锁（允许超时自动释放，避免死锁）

---

### PR 4: Streaming + API Reference + 前端

**范围：** architecture/streaming.md, guides/api-reference.md, frontend.md

**architecture/streaming.md 内容要点：**
- StreamEventType 枚举（完整列表，按层分组）：
  - Controller 层：METADATA, COMPLETE, CANCELLED, ERROR
  - Agent 层：AGENT_START, LLM_CHUNK, LLM_COMPLETE, AGENT_COMPLETE
  - Tool 层：TOOL_START, TOOL_COMPLETE, PERMISSION_REQUEST, PERMISSION_RESULT, COMPACTION_WAIT
  - Input 层：USER_INPUT, QUEUED_MESSAGE, SUBAGENT_INSTRUCTION
- ExecutionEvent 数据结构：event_type, agent_name, data, event_id, created_at
- SSE Transport 双实现：
  - InMemoryStreamTransport（开发）
  - RedisStreamTransport（生产，Redis Streams）
  - Protocol-based 接口：create_stream → push_event → consume_events
- Stream 生命周期：创建 → 推送 → 消费 → 清理（TTL 或终端事件）
- 心跳：SSE_PING_INTERVAL（15s）ping comment
- Last-Event-ID 断线续传
- **Design Decisions：**
  - 为什么 fetch+ReadableStream 而非 EventSource（EventSource 不支持自定义 Auth header）
  - 为什么 llm_chunk 不持久化（高频低价值，只做 SSE 传输）

**guides/api-reference.md 内容要点：**
- 认证：POST /auth/register, /auth/login, /auth/refresh（JWT）
- 对话：
  - POST /chat（发送消息，返回 stream_url）
  - GET /chat（列表）, GET /chat/{id}（详情）, DELETE /chat/{id}
  - POST /chat/{id}/inject（注入消息）
  - POST /chat/{id}/resume（Permission Interrupt 应答）
  - POST /chat/{id}/cancel（取消执行）
- Artifact：
  - GET /artifacts, GET /artifacts/{id}, GET /artifacts/{id}/versions
  - POST /artifacts/{id}/upload, DELETE /artifacts/{id}
- SSE：GET /stream/{stream_id}
- Admin：用户 CRUD（require_admin）
- 通用模式：
  - 认证：Authorization: Bearer {token}
  - 错误码：401/403/404/409
  - 409 = 并发 chat（lease conflict）

**frontend.md 内容要点：**
（基本复用现有 frontend.md，更新以下部分）
- Tech Stack：Next.js 15 + TypeScript + Tailwind + Zustand
- 目录结构：app/, components/, stores/, hooks/, lib/, types/
- 三栏布局：Sidebar / Chat / Artifacts
- SSE 集成：fetch + ReadableStream + Bearer token
- 状态管理：Zustand stores（conversation, artifact, stream, ui, auth）
- 性能优化：
  - requestAnimationFrame 节流 llm_chunk
  - React.memo 消息组件
  - @tanstack/react-virtual 长对话虚拟列表
  - Zustand selector 精细订阅
- API 类型同步：export_openapi.py → generate-types
- 暗色模式：dark: variant 全组件覆盖

---

### PR 5: Guides（扩展指南）

**范围：** guides/add-agent.md, guides/add-tool.md, guides/add-model.md

**guides/add-agent.md 内容要点：**
- 步骤：
  1. 在 config/agents/ 创建 .md 文件
  2. 编写 YAML frontmatter（name, description, model, tools, max_tool_rounds）
  3. 编写 Role Prompt（MD body）
  4. 重启服务（自动扫描加载）
- Frontmatter 完整字段参考 + 默认值
- Role Prompt 编写建议：
  - 明确职责边界
  - 定义输出格式
  - 设置停止条件
- 示例：完整的自定义 Agent 配置文件
- 注意事项：
  - internal=true 的 Agent 不出现在 call_subagent 候选列表
  - 工具权限覆盖只能收紧（auto→confirm），不能放松

**guides/add-tool.md 内容要点：**
- 步骤：
  1. 继承 BaseTool
  2. 定义 name, description, parameters（ToolParameter list）
  3. 实现 async execute() → ToolResult
  4. 注册到 tools/builtin/__init__.py
- ToolParameter 字段：name, type, description, required, default, enum
- ToolResult 返回：success, output, error, metadata
- XML 格式自动生成（to_xml_example）
- 权限级别选择指南：AUTO vs CONFIRM
- 示例：完整的自定义 Tool 实现
- 测试建议

**guides/add-model.md 内容要点：**
- 步骤：
  1. 在 config/models/models.yaml 添加配置
  2. 设置对应 API Key 环境变量
  3. 在 Agent 配置中引用 model alias
- models.yaml 结构：defaults + models 列表
- 字段参考：alias, litellm_format, base_url, api_key, temperature, max_tokens, enable_thinking 等
- 支持的 Provider：OpenAI, DashScope(Qwen), DeepSeek, Ollama, vLLM, OpenAI-compatible
- 自部署模型配置（base_url 指定）
- 示例：添加 Ollama 本地模型 / 添加 OpenAI-compatible API
- 测试：tests/manual/litellm_providers.py

---

### PR 6: README 更新

**范围：** README.md

**定位：** 项目第一印象 + 快速上手入口，详细内容全部指向 docs/

**README.md 结构：**
- **项目一句话介绍**（Pi-style 可配置 Agent 引擎 SaaS）
- **核心特性**（bullet list，每条一句话）
- **Quick Start**（docker-compose up 三步走：clone → 配置 .env → docker-compose up）
- **截图**（可选，复用 docs/assets/）
- **Documentation**（指向 docs/ 各模块的链接表）
  - 架构：overview, engine, agents, tools, artifacts, data-layer, streaming, concurrency
  - 指南：add-agent, add-tool, add-model, api-reference
  - 运维：deployment
  - 前端：frontend
- **License**

**注意：** 等 PR 1-5 全部合并后再写，确保所有文档链接有效。

---

## 编写约定

- 语言：中文
- 每个架构文档包含 Design Decisions 小节（解释"为什么"，不单独建文件）
- 代码示例取自当前实现（读源码后写，不编造）
- 避免引用已移除的 LangGraph / LangChain
- 配置示例使用实际的 config/ 文件内容
- 保持 CLAUDE.md 中的架构决策与文档一致（CLAUDE.md 作为 AI 精简版，docs 作为人类详细版）
