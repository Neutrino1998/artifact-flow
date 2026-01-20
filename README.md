# 🤖 ArtifactFlow

> Multi-Agent Research System based on LangGraph and Artifacts

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![LangGraph](https://img.shields.io/badge/LangGraph-Latest-green.svg)](https://github.com/langchain-ai/langgraph)
[![SQLite](https://img.shields.io/badge/SQLite-Persistent-blue.svg)]()
[![Development Status](https://img.shields.io/badge/Status-Alpha%20Development-orange.svg)]()

ArtifactFlow 是一个智能多智能体研究系统，通过协调专门的AI智能体来执行综合性研究任务。基于 LangGraph 构建，采用独特的双 Artifact 架构，实现 AI 协作研究和人工监督的迭代优化。

## ✨ 核心特性

- **🗂️ 双Artifact架构**: 分离任务计划和结果工件，实现清晰的工作流管理
- **🤝 多智能体协作**: 专门的智能体（主控、搜索、网页抓取）协调工作
- **🤖 统一Agent框架**: 基于BaseAgent的一致性执行模式，支持流式响应和工具调用
- **🎯 智能任务分解**: Lead Agent根据任务复杂度自动选择执行策略
- **🔍 专业化智能体**: Search和Crawl智能体各司其职，提供专业化服务
- **🔄 无缝协作**: Agent间通过统一接口协作，支持复杂工作流编排
- **⚡ 流式响应**: 实时查看AI思考过程、LLM输出和工具调用状态，支持类ChatGPT的打字机效果
- **🎯 人机协作**: 在任意阶段暂停、恢复并提供反馈
- **🔧 灵活工具系统**: 可扩展的工具框架，支持权限控制
- **🕷️ 智能网页抓取**: 基于crawl4ai的深度内容提取和分析（支持PDF解析）
- **📊 进度跟踪**: 可视化任务进度和完成状态
- **🔄 迭代优化**: 基于用户反馈的持续改进
- **🌳 分支对话**: 支持从任意历史节点创建新的对话分支
- **💾 SQLite持久化**: 数据持久化存储，服务重启后数据不丢失，支持乐观锁并发控制

## 🛠️ 系统架构

### 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                     Application Layer                       │
│           (ExecutionController, Agents, Tools)              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       Manager Layer                         │
│   ┌─────────────────────┐   ┌─────────────────────────┐     │
│   │ ConversationManager │   │    ArtifactManager      │     │
│   │  - In-memory cache  │   │  - In-memory cache      │     │
│   │  - Call Repository  │   │  - Call Repository      │     │
│   └─────────────────────┘   └─────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Repository Layer                        │
│   ┌──────────────────────┐   ┌─────────────────────────┐    │
│   │ConversationRepository│   │   ArtifactRepository    │    │
│   │  - CRUD operations   │   │  - CRUD operations      │    │
│   │  - Tree queries      │   │  - Version & Opt. Lock  │    │
│   └──────────────────────┘   └─────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Database Layer                         │
│              ┌────────────┴────────────┐                    │
│              ▼                         ▼                    │
│     App DB (SQLite)          LangGraph Checkpointer         │
│     (conversations,          (AsyncSqliteSaver)             │
│      messages, artifacts)    (thread states)                │
└─────────────────────────────────────────────────────────────┘
```

### Artifact 层

```
┌────────────────────────────────────────────────────────────┐
│                       ARTIFACT LAYER                       │
│                                                            │
│  ┌───────────────────────────────┐  ┌────────────────────┐ │
│  │       Task Plan Artifact      │  │    Result Artifact │ │
│  │  - Task breakdown & tracking  │  │  - Final outputs   │ │
│  │  - Shared context for agents  │  │  - User editable   │ │
│  └───────────────────────────────┘  └────────────────────┘ │
└────────────────────────────────────────────────────────────┘
           ↑                     ↑                    ↑
    Lead Agent              Subagents                User
  (Read/Write)             (Read Only)           (Read/Edit)
```

### 智能体角色

- **🎯 主控智能体 (Lead Agent)**: 任务协调、信息整合、用户交互
- **🔍 搜索智能体 (Search Agent)**: 信息检索和结构化搜索结果
- **🕷️ 网页抓取智能体 (Crawl Agent)**: 深度内容提取和分析（支持HTML和PDF）

## 🚀 快速开始

### 环境要求

- **Python 3.11+** （⚠️必需！LangGraph的异步interrupt功能需要Python 3.11+才能正确工作，详见： [Asynchronous Graph with interrupts in Python 3.10 seems to be broken](https://github.com/langchain-ai/langgraph/discussions/3200)）
- API Keys（OpenAI、通义千问、DeepSeek、博查AI 等）
- 推荐系统内存 ≥ 4GB（网页抓取需要启动浏览器）
- **⚠️ 依赖版本注意**: `aiosqlite` 必须使用 **0.21.0** 版本。0.22.0 版本移除了 `Connection` 对 `threading.Thread` 的继承，导致 `is_alive()` 方法丢失，与 `langgraph-checkpoint-sqlite` 不兼容。requirements.txt 已锁定正确版本。

### 安装步骤

1. **克隆项目**
   ```bash
   git clone https://github.com/yourusername/artifact-flow.git
   cd artifact-flow
   ```

2. **创建虚拟环境**
   ```bash
   # 使用 conda（推荐）
   conda create -n artifact-flow python=3.11
   conda activate artifact-flow
   
   # 或使用 venv
   python3.11 -m venv artifact-flow
   # Windows: artifact-flow\Scripts\activate
   # macOS/Linux: source artifact-flow/bin/activate
   ```

3. **安装依赖**
   ```bash
   pip install -r requirements.txt
   pip install -e .
   ```

4. **⚠️ 重要：初始化crawl4ai**
   ```bash
   # crawl4ai 需要额外的初始化步骤
   crawl4ai-setup
   ```
   
   这个命令会：
   - 下载必要的浏览器驱动程序
   - 配置Playwright环境

5. **配置环境变量**
   ```bash
   cp .env.example .env
   # 编辑 .env 文件，添加你的 API Keys
   ```

## 🔑 配置指南

创建 `.env` 文件并配置以下 API Keys：

```env
# ========================================
# 模型 API 配置
# ========================================

# ------ OpenAI (GPT系列) ------
# 获取地址: https://platform.openai.com/api-keys
OPENAI_API_KEY=sk-xxx

# ------ 通义千问 (Qwen) ------
# 获取地址: https://dashscope.console.aliyun.com/apiKey
DASHSCOPE_API_KEY=sk-xxx

# ------ DeepSeek ------
# 获取地址: https://platform.deepseek.com/api_keys
DEEPSEEK_API_KEY=sk-xxx

# ========================================
# 工具 API 配置
# ========================================

# ------ 博查AI (Web搜索) ------
# 获取地址: https://open.bochaai.com
BOCHA_API_KEY=sk-xxx
```

## 💡 支持的模型

### OpenAI
- `gpt-4o` - 最新的 GPT-4 模型
- `gpt-4o-mini` - 轻量级版本

### 通义千问 (Qwen)
- `qwen-flash` - 快速响应版本
- `qwen-plus` - 增强版本
- `qwen3-30b-thinking` - 支持深度推理的思考模型 ⭐
- `qwen3-30b-instruct` - 快速指令响应模型
- `qwen3-next-80b-thinking` - 更大规模的思考模型
- `qwen3-next-80b-instruct` - 更大规模的指令模型

### DeepSeek
- `deepseek-chat` - 对话模型
- `deepseek-reasoner` - 推理模型 ⭐

## 💾 数据持久化

ArtifactFlow 使用 SQLite 数据库进行数据持久化，采用双层存储架构：

### 存储位置

```
data/
└── artifactflow.db    # SQLite 数据库文件（自动创建）
```

### 数据库表结构

| 表名 | 说明 |
|------|------|
| `conversations` | 对话元信息（ID、标题、活跃分支、时间戳） |
| `messages` | 消息记录（树结构，支持分支对话） |
| `artifact_sessions` | Artifact 会话（与对话 1:1 关联） |
| `artifacts` | Artifact 内容（含乐观锁版本控制） |
| `artifact_versions` | Artifact 历史版本（支持版本回溯） |

### 特性

- **WAL 模式**: 启用 Write-Ahead Logging，支持并发读写
- **乐观锁**: Artifact 更新使用乐观锁机制，防止并发冲突
- **热数据缓存**: Manager 层实现 LRU 缓存，减少数据库访问
- **PostgreSQL 兼容**: 使用 SQLAlchemy ORM，可平滑迁移到 PostgreSQL

### 初始化

数据库在首次运行时自动创建，无需手动初始化。如需重置数据库：

```bash
# 删除数据库文件（谨慎操作，将丢失所有数据）
rm data/artifactflow.db
```

## 📁 项目结构

```
artifact-flow/
├── src/
│   ├── core/ ✅        # 核心工作流和状态管理 (已完成)
│   │   ├── state.py              # 状态管理和定义
│   │   ├── graph.py              # LangGraph工作流定义
│   │   ├── controller.py         # 执行控制器 (支持流式和批量模式)
│   │   ├── events.py             # 统一事件类型和ExecutionMetrics定义
│   │   ├── context_manager.py    # Context压缩和管理
│   │   └── conversation_manager.py  # 对话管理器（缓存+持久化）
│   ├── agents/ ✅      # 智能体实现 (已完成)
│   │   ├── base.py               # Agent基类和流式执行框架
│   │   ├── lead_agent.py         # 主控智能体实现
│   │   ├── search_agent.py       # 搜索智能体实现
│   │   └── crawl_agent.py        # 网页抓取智能体实现
│   ├── tools/ ✅       # 工具系统和实现 (已完成)
│   │   ├── base.py               # 工具基类和权限定义
│   │   ├── registry.py           # 工具注册和管理
│   │   ├── permissions.py        # 权限控制系统
│   │   ├── prompt_generator.py   # XML提示词生成
│   │   └── implementations/      # 具体工具实现
│   │       ├── artifact_ops.py   # Artifact操作工具 (ArtifactManager)
│   │       ├── web_search.py     # 博查AI搜索
│   │       ├── web_fetch.py      # crawl4ai网页抓取(支持PDF)
│   │       └── call_subagent.py  # Subagent调用工具
│   ├── db/ ✅          # 数据库层 (已完成)
│   │   ├── database.py           # DatabaseManager：连接池、WAL模式
│   │   ├── models.py             # SQLAlchemy ORM 模型定义
│   │   └── migrations/           # 数据库迁移脚本
│   │       └── versions/
│   │           └── 001_initial_schema.py
│   ├── repositories/ ✅ # 数据访问层 (已完成)
│   │   ├── base.py               # BaseRepository 抽象类
│   │   ├── conversation_repo.py  # ConversationRepository
│   │   └── artifact_repo.py      # ArtifactRepository (含乐观锁)
│   ├── models/ ✅      # LLM 接口封装 (已完成)
│   │   └── llm.py                # 统一的多模型接口
│   ├── utils/ ✅       # 工具函数和帮助类 (已完成)
│   │   ├── logger.py             # 分级日志系统
│   │   ├── retry.py              # 指数退避重试
│   │   └── xml_parser.py         # 鲁棒XML解析
│   └── api/            # 🚧 API 接口层 (计划中)
├── data/               # 数据目录 (SQLite数据库文件)
├── tests/              # 测试用例
│   ├── core_graph_test.py             # 核心模块批量测试
│   └── core_graph_test_with_stream.py # 核心模块流式测试
├── prompts/            # 智能体提示词模板
├── examples/           # 使用示例
├── logs/               # 日志目录
└── docs/               # 文档
```

## 🧪 使用示例

### 1. 基础LLM调用

```python
from src.models.llm import create_llm

# 创建思考模型
llm = create_llm("qwen3-30b-thinking", temperature=0.3)
response = llm.invoke("解释量子计算的基本原理")

# 获取思考过程
if 'reasoning_content' in response.additional_kwargs:
    print("💭 思考过程:", response.additional_kwargs['reasoning_content'])
print("💬 最终回答:", response.content)
```

### 2. 工具系统使用

```python
import asyncio
from src.tools.implementations.web_search import WebSearchTool
from src.tools.implementations.web_fetch import WebFetchTool
from src.tools.implementations.artifact_ops import CreateArtifactTool

async def demo_tools():
    # 1. 网页搜索
    search_tool = WebSearchTool()
    search_result = await search_tool(
        query="AI多智能体系统最新研究",
        count=5,
        freshness="oneMonth"
    )
    
    if search_result.success:
        print("🔍 搜索完成:", search_result.metadata['results_count'], "条结果")
    
    # 2. 深度网页抓取（支持PDF）
    fetch_tool = WebFetchTool()
    urls = ["https://github.com/langchain-ai/langgraph", "https://arxiv.org/pdf/1706.03762.pdf"]
    fetch_result = await fetch_tool(
        urls=urls,
        max_content_length=3000,
        max_concurrent=2
    )
    
    if fetch_result.success:
        print("🕷️ 抓取完成:", fetch_result.metadata['success_count'], "个页面/文档")
    
    # 3. 创建研究工件
    artifact_tool = CreateArtifactTool()
    create_result = await artifact_tool(
        id="research_plan",
        type="task_plan",
        title="Multi-Agent系统研究计划",
        content="# 研究目标\n\n1. 分析当前技术现状\n2. 设计系统架构"
    )
    
    if create_result.success:
        print("📄 工件创建成功")

# 运行演示
asyncio.run(demo_tools())
```

### 3. 核心模块使用（批量模式）

```python
import asyncio
from src.core.graph import create_multi_agent_graph
from src.core.controller import ExecutionController
from src.utils.logger import set_global_debug

# 开启调试模式
set_global_debug(True)

async def demo_core_system():
    # 创建系统
    compiled_graph = create_multi_agent_graph()
    controller = ExecutionController(compiled_graph)
    
    # 第一轮对话
    result1 = await controller.execute(
        content="研究一下LangGraph的最新特性"
    )
    conv_id = result1["conversation_id"]
    print(f"回复: {result1['response']}")
    
    # 第二轮（自动继续对话历史）
    result2 = await controller.execute(
        content="帮我整理成一份技术文档",
        conversation_id=conv_id
    )
    print(f"回复: {result2['response']}")
    
    # 如果遇到权限请求
    if result2.get("interrupted"):
        print(f"⚠️ 需要权限: {result2['interrupt_data']['tool_name']}")
        
        # 批准权限
        result2 = await controller.execute(
            thread_id=result2["thread_id"],
            resume_data={"type": "permission", "approved": True}
        )
        print(f"✅ 完成: {result2['response']}")

asyncio.run(demo_core_system())
```

### 4. 流式执行（实时响应）🆕

```python
import asyncio
from src.core.graph import create_multi_agent_graph
from src.core.controller import ExecutionController
from src.core.events import StreamEventType

async def demo_streaming():
    """演示流式执行 - 实时查看AI的思考和输出过程"""
    compiled_graph = create_multi_agent_graph()
    controller = ExecutionController(compiled_graph)

    # 使用 stream_execute 进行流式执行
    async for event in controller.stream_execute(
        content="研究一下LangGraph的最新特性"
    ):
        event_type = event.get("type")
        data = event.get("data", {})
        agent = event.get("agent", "")

        if event_type == StreamEventType.METADATA.value:
            print(f"🚀 开始执行: {data['conversation_id']}")

        elif event_type == StreamEventType.LLM_CHUNK.value:
            # 实时输出LLM响应
            content = data.get("content", "")
            reasoning = data.get("reasoning_content", "")

            if reasoning:
                print(f"💭 [{agent}] 思考: {reasoning}", end="", flush=True)
            if content:
                print(f"💬 [{agent}] 回答: {content}", end="", flush=True)

        elif event_type == StreamEventType.TOOL_START.value:
            tool = event.get("tool", "")
            print(f"\n🔧 [{agent}] 调用工具: {tool}...")

        elif event_type == StreamEventType.TOOL_COMPLETE.value:
            tool = event.get("tool", "")
            success = data.get("success", False)
            duration = data.get("duration_ms", 0)
            print(f"🔧 [{agent}] 工具 {tool} 完成: {'OK' if success else 'FAIL'} ({duration}ms)")

        elif event_type == StreamEventType.PERMISSION_REQUEST.value:
            tool = event.get("tool", "")
            print(f"\n⚠️ [{agent}] 需要权限: {tool}")

        elif event_type == StreamEventType.COMPLETE.value:
            if data["success"]:
                print(f"\n✅ 执行完成")
                if not data.get("interrupted"):
                    print(f"回复: {data['response']}")
                # 显示执行指标
                metrics = data.get("execution_metrics", {})
                if metrics:
                    print(f"📊 总耗时: {metrics.get('total_duration_ms', 0)}ms")

asyncio.run(demo_streaming())
```

#### 流式执行的优势

- **实时反馈**: 立即看到AI的思考过程和输出，无需等待完整响应
- **用户体验**: 类似ChatGPT的打字机效果，提升交互感
- **进度透明**: 清晰了解当前执行状态（思考中、调用工具、等待权限等）
- **调试友好**: 实时查看每个Agent的工作流程

### 5. 权限确认（批量模式）

```python
async def demo_permission():
    from src.tools.base import ToolPermission
    
    # 配置需要确认的工具
    tool_permissions = {
        "web_fetch": ToolPermission.CONFIRM
    }
    
    compiled_graph = create_multi_agent_graph(tool_permissions=tool_permissions)
    controller = ExecutionController(compiled_graph)
    
    # 批量模式
    result = await controller.execute(
        content="抓取 https://github.com/langchain-ai/langgraph"
    )
    
    if result.get("interrupted"):
        print(f"⚠️ 需要权限: {result['interrupt_data']['tool_name']}")
        
        # 批准或拒绝
        result = await controller.execute(
            thread_id=result["thread_id"],
            resume_data={"type": "permission", "approved": True}
        )

asyncio.run(demo_permission())
```

### 6. 权限确认（流式模式）🆕

```python
async def demo_permission_stream():
    from src.tools.base import ToolPermission
    from src.core.events import StreamEventType

    tool_permissions = {
        "web_fetch": ToolPermission.CONFIRM
    }

    compiled_graph = create_multi_agent_graph(tool_permissions=tool_permissions)
    controller = ExecutionController(compiled_graph)

    # 流式模式 - 支持多次权限确认
    result = None
    max_retries = 3
    retry_count = 0

    # 第一次执行
    stream = controller.stream_execute(
        content="抓取 https://github.com/langchain-ai/langgraph"
    )

    async for event in stream:
        event_type = event.get("type")
        # 处理流式事件
        if event_type == StreamEventType.PERMISSION_REQUEST.value:
            tool = event.get("tool", "")
            print(f"⚠️ 需要权限确认: {tool}")

        elif event_type == StreamEventType.COMPLETE.value:
            result = event.get("data", {})

    # 处理多次权限确认
    while result.get("interrupted") and retry_count < max_retries:
        retry_count += 1
        user_input = input(f"是否批准工具 '{result['interrupt_data']['tool_name']}'? (y/n): ")
        approved = user_input.lower() == 'y'

        # 继续执行
        stream = controller.stream_execute(
            thread_id=result["thread_id"],
            conversation_id=result["conversation_id"],
            message_id=result["message_id"],
            resume_data={"type": "permission", "approved": approved}
        )

        async for event in stream:
            # 处理流式事件
            if event.get("type") == StreamEventType.COMPLETE.value:
                result = event.get("data", {})

asyncio.run(demo_permission_stream())
```

### 7. 分支对话

```python
async def demo_branch_conversation():
    compiled_graph = create_multi_agent_graph()
    controller = ExecutionController(compiled_graph)
    
    # 主线对话
    result1 = await controller.execute(content="计算 15 + 28")
    conv_id = result1["conversation_id"]
    msg1_id = result1["message_id"]
    
    # 继续主线
    result2 = await controller.execute(
        content="再乘以2",
        conversation_id=conv_id
    )
    
    # 从msg1创建分支
    result3 = await controller.execute(
        content="再减去10",
        conversation_id=conv_id,
        parent_message_id=msg1_id  # 从msg1分支
    )
    
    print(f"主线结果: {result2['response']}")
    print(f"分支结果: {result3['response']}")

asyncio.run(demo_branch_conversation())
```

### 8. 运行完整测试

```bash
# 运行核心模块集成测试（批量模式）
python -m test.core_graph_test

# 运行流式输出测试 🆕
python core_graph_test_with_stream.py

# 测试选项：
# 1. 多轮对话演示
# 2. 权限确认演示（支持多次确认）
# 3. 分支对话演示
# 4. 批量 vs 流式对比演示
```

## 📈 开发路线图

- ✅ **基础设施** (v0.1.0) - **已完成**
  - [x] 项目结构和配置
  - [x] 核心工具模块（日志、重试、XML解析）
  - [x] 多模型LLM接口统一封装

- ✅ **工具系统** (v0.1.5) - **已完成**
  - [x] 工具框架和权限控制
  - [x] Artifact操作工具
  - [x] Web搜索和抓取工具（支持PDF）
  - [x] XML提示词生成系统

- ✅ **智能体系统** (v0.2.0) - **已完成**
  - [x] BaseAgent抽象类和统一执行框架
  - [x] Lead Agent 实现 - 任务协调和信息整合
  - [x] Search Agent 实现 - 信息检索专家
  - [x] Crawl Agent 实现 - 网页内容抓取专家

- ✅ **工作流编排** (v0.3.0) - **已完成**
  - [x] Agent状态管理 (state.py)
  - [x] LangGraph工作流 (graph.py)
  - [x] 执行控制器 (controller.py)
  - [x] Context压缩和管理 (context_manager.py)
  - [x] 多轮对话支持
  - [x] 分支对话功能
  - [x] 权限确认流程

- ✅ **数据持久化** (v0.3.5) - **已完成**
  - [x] SQLite + WAL 模式持久化存储
  - [x] SQLAlchemy ORM 数据模型
  - [x] Repository 模式数据访问层
  - [x] 乐观锁并发控制
  - [x] LangGraph Checkpointer 状态持久化
  - [x] 依赖注入设计

- ✅ **高级特性** (v0.4.0) - **已完成**
  - [x] 流式执行支持（实时响应、思考内容、工具调用状态）
  - [x] 流式权限确认（支持多次中断处理）
  - [x] 可观测性指标系统（ExecutionMetrics：Token使用、工具调用、执行耗时）
  - [ ] 错误处理和自动恢复
  - [ ] 性能优化

- 🚀 **API接口** (v0.5.0) - **计划中**
  - [ ] FastAPI REST接口
  - [ ] WebSocket实时通信
  - [ ] 前端界面集成
  - [ ] API文档

- 🎉 **生产就绪** (v1.0.0) - **目标**
  - [ ] 完整的错误处理
  - [ ] 生产级性能优化
  - [ ] PostgreSQL 迁移支持
  - [ ] 安全增强
  - [ ] 完整文档和示例
  - [ ] Docker部署支持


## 📞 支持与反馈

- 🐛 [问题反馈](https://github.com/Neutrino1998/artifact-flow/issues)
- 💬 [讨论交流](https://github.com/Neutrino1998/artifact-flow/discussions)
- 📖 [开发文档](docs/)
- 📫 [联系作者](mailto:1998neutrino@gmail.com)

