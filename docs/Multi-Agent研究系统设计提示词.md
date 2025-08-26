# 🤖 基于Artifact的Multi-Agent研究系统设计方案（ArtifactFlow）

## 系统架构概要

- **双Artifact机制**：Task Plan (进度跟踪) + Result (研究成果)
- **Lead Agent**：任务协调，唯一可读写Artifact
- **Subagents**：只读Task Plan，返回结构化结果给Lead
- **信息流**：Subagent → Lead Agent → Artifact (单向避免冲突)

```
 ┌────────────────────────────────────────────────────────────┐
 │                       ARTIFACT LAYER                       │
 │                                                            │
 │  ┌───────────────────────────────┐  ┌────────────────────┐ │
 │  │       Task Plan Artifact      │  │    Result Artifact │ │
 │  │  - 任务分解 & 进度跟踪          │  │  - 最终产出文档     │ │
 │  │  - 共享给所有Agent 有权限控制   │  │   - 共享给用户      │ │
 │  └───────────────────────────────┘  └────────────────────┘ │
 │                                                            │
 └────────────────────────────────────────────────────────────┘
           ↑                     ↑                    ↑
           │                     │                    │
           │ (读写)              │ (只读)              │ (读写)
           │                     │                    │
 ┌───────────────┐      ┌───────────────┐      ┌──────────────┐
 │   Lead Agent  │      │   Subagents   │      │    User      │
 │  - 管理任务    │      │ - Search Agent│      │ - 可浏览编辑  │
 │  - 协调执行    │      │ - Web Crawl   │      │ - 可给反馈    │
 │  - 整合结果    │      │ - Others...   │      │              │
 └───────────────┘      └───────────────┘      └──────────────┘
```

**Lead Agent**

- **职责**: 任务规划、信息整合、用户交互
- **工具:**
  - Artifact操作 (create/update/rewrite)
  - Subagent调用接口
- **特点**: 保持上下文简洁，专注于高层决策

**Subagents**

- **Search Agent**: 信息检索，返回结构化搜索结果
- **Web Crawl Agent**: 深度内容抓取，提取关键信息
- **独立性**: 每个agent独立完成分配任务，自主判断完成标准

**双Artifact机制**

- **Task Plan Artifact:**
  - **Lead Agent权限**: 读写 (更新任务状态、添加新任务、修改优先级)
  - **Subagent权限**: 只读 (了解任务需求、查看上下文信息)
  - **用户权限**: 只读 (查看研究进度和任务状态)
- **Result Artifact:**
  - **Lead Agent权限**: 读写 (整合信息、更新内容、调整结构)
  - **Subagent权限**: 无访问权限 (保持职责清晰，避免直接修改最终结果)
  - **用户权限**: 读写 (编辑内容、调整格式、添加批注)

## 🔄 工作流程

```
   用户提出需求
        │
        │ (对话交互)
        ↓
   Lead Agent创建Task Plan
        │
        │ (分析并派发任务)
        ↓
   ┌─── Search Agent ←── (读取Task Plan获取完整上下文)
   │         │
   │         └──→ 返回搜索结果
   │
   ├─── Crawl Agent ←── (读取Task Plan获取完整上下文)  
   │         │
   │         └──→ 返回抓取结果
   │
   └─── Other Agents...
        │
        ↓
Lead Agent整合信息
        │
        │ (更新Task Plan状态)
        │ (更新Result Artifact内容)
        ↓
   用户查看结果
        │
        │ (编辑Artifact或对话反馈)
        ↓
   Lead Agent根据反馈调整
        │
        │ (循环执行直到满意)
        ↓
      任务完成
```

### 阶段1: 研究规划

1. 用户提出研究需求
2. Lead Agent分析需求，创建Task Plan Artifact和初始Result Artifact框架
3. 系统进入执行阶段

### 阶段2: 信息收集

1. Lead Agent根据Task Plan派发搜索任务
2. **Search Agent读取Task Plan**，了解具体搜索需求和上下文
3. Search Agent独立执行搜索，**返回结果给Lead Agent**（不直接修改任何Artifact）
4. Lead Agent接收结果后：
   - 更新Task Plan中的任务状态
   - 将信息整合到Result Artifact
   - 评估是否需要进一步抓取
5. 必要时派发URL给Web Crawl Agent，重复上述流程

### 阶段3: 协作优化

1. **任务完成后的交互**：Lead Agent完成一轮任务执行，向用户展示更新后的Result Artifact
2. **用户反馈方式：**
   - 直接编辑Result Artifact（修改内容、调整结构、添加要求）
   - 通过对话向Lead Agent提出改进要求（"需要更多临床数据"、"增加市场分析章节"）
3. **Lead Agent响应**：根据用户反馈调整Task Plan，派发新的任务或优化现有内容
4. **迭代循环**：重复执行直到用户满意

## 技术栈

- **框架**: LangGraph (工作流) + LangChain (LLM接口)
- **模型**: 支持OpenAI接口兼容模型(Qwen/DeepSeek等)
- **存储**: 内存Checkpoint (LangGraph MemorySaver)



## 关键实现模块

### 1. 执行控制器 (Human in the Loop)

```python
class ExecutionController:
    async def pause(thread_id): # 暂停执行
    async def resume(thread_id): # 恢复+带入用户context
    async def rollback(thread_id, checkpoint_id): # 回滚
    def add_context(user_query): # 暂停时添加补充要求
```

### 2. LangGraph工作流

```python
from langgraph.graph import StateGraph
from langgraph.checkpoint.memory import MemorySaver

workflow = StateGraph(AgentState)
workflow.add_node("lead_agent", lead_agent_node)
workflow.add_node("search_agent", search_agent_node)
workflow.add_conditional_edges("lead_agent", route_to_subagent, {...})
app = workflow.compile(checkpointer=MemorySaver())
```

### 3. XML工具调用 (不用tool_call接口)

```xml
<tool_call>
  <name>web_search</name>
  <params>
    <query>AI medical FDA approval</query>
  </params>
</tool_call>
```

### 4. Robust XML解析器

```python
class RobustXMLParser:
    def parse(text):
        # 1.标准解析 → 2.修复常见错误 → 3.正则提取
```

### 5. Context压缩 (20k字符限制)

- 保留最近N轮对话 + 关键决策点
- 历史对话压缩为摘要
- 基于任务相关性筛选



## 核心特性

### 1. 流式响应机制

- **Lead Agent**: 使用流式输出，实时展示思考过程和结果生成，通过WebSocket推送给前端
- **Subagents**: 等待完整响应后批量返回，避免碎片化信息干扰主流程

### 2. 工具权限控制

- 针对每个工具设置权限级别：PUBLIC(直接执行如搜索)、NOTIFY(执行后通知如保存文件)、CONFIRM(需用户确认如发邮件)、RESTRICTED(需特殊授权如执行代码)
- 敏感操作需要用户审批，普通查询类工具自动执行
- 代码细节：

1.  **Setup Phase:**
    
    *   创建 `ToolRegistry` 实例。
    *   注册所有可用工具到 `registry.tool_library`。
    *   定义 agent 的默认权限 `default_perms = {"lead_agent": {...}, "search_agent": {...}}`。
    *   创建 `PermissionManager` 实例，传入配置：`permission_manager = PermissionManager(default_perms)`。
    *   为每个 agent 创建工具包：`registry.create_agent_toolkit("search_agent", tool_names=["search_web", "send_email"])`。
    
2.  **Execution Phase (Agent wants to use "send_email"):**
    
    *   **Step 1 (Availability):** 从 Registry 获取工具。
        ```python
        toolkit = registry.get_agent_toolkit("search_agent")
        tool_to_use = toolkit.get_tool("send_email")
        if not tool_to_use:
            # 失败：Agent 甚至没有这个工具
            return "Error: Tool not available."
        ```
    *   **Step 2 (Authorization):** 询问 Permission Manager。
        ```python
        if not permission_manager.check_permission("search_agent", tool_to_use, auto_request=True):
            # 失败：Agent 有这个工具，但当前没有权限使用它。
            # (一个权限请求可能已被自动创建)
            return "Waiting for permission to use send_email."
        ```
    *   **Step 3 (User Confirmation, if needed):**
        ```python
        if tool_to_use.permission == ToolPermission.CONFIRM:
            # 暂停并向用户请求执行许可
            user_approval = ask_user_for_confirmation(...)
            if not user_approval:
                return "Execution cancelled by user."
        ```
    *   **Step 4 (Execution):** 调用工具。
        ```python
        result = await toolkit.execute_tool("send_email", params={...})
        ```

### 3. 分级日志系统

- 支持配置是否记录完整prompt和response（调试时开启，生产环境关闭以节省存储）
- 默认只记录关键信息：模型名称、token数、耗时、成本、响应预览(前200字符)
- 提供debug模式一键开启详细日志

### 4. 错误处理策略

- **模型调用错误**: 限流错误用指数退避重试；超时错误立即重试或降级到小模型；认证错误直接失败并通知用户
- **工具调用错误**: 网络错误自动重试；解析错误降级到正则提取；权限错误请求用户授权
- **XML解析错误**: 自动修复未闭合标签、转义特殊字符，实在无法解析则提取可用部分

### 5. 监控指标

- 实时统计各模型的调用次数、token消耗、成本累计
- 跟踪工具使用频率、错误率、平均响应时间
- 支持导出分析报告用于优化系统性能



## 项目结构

```
artifact-flow/
├── 📄 README.md
├── 📄 requirements.txt
├── 📄 .env.example
├── 📄 .gitignore
├── 📄 Dockerfile                     # Docker镜像定义
├── 📄 docker-compose.yml             # Docker Compose配置
├── 📄 config.yaml                    # ⭐ 全局配置文件
│
├── 📁 src/
│   ├── 📄 __init__.py
│   │
│   ├── 📁 core/                      # ⭐ 核心模块
│   │   ├── 📄 __init__.py
│   │   ├── 📄 graph.py               # LangGraph工作流定义
│   │   ├── 📄 state.py               # 状态管理和定义
│   │   ├── 📄 controller.py          # 执行控制器(pause/resume/rollback)
│   │   └── 📄 context_manager.py     # Context压缩和管理
│   │
│   ├── 📁 agents/                    # ⭐ Agent实现
│   │   ├── 📄 __init__.py
│   │   ├── 📄 base.py                # BaseAgent抽象类
│   │   ├── 📄 lead_agent.py          # Lead Agent实现
│   │   ├── 📄 search_agent.py        # Search Agent实现
│   │   └── 📄 crawl_agent.py         # Web Crawl Agent实现
│   │
│   ├── 📁 tools/                     # 工具系统
│   │   ├── 📄 __init__.py
│   │   ├── 📄 base.py                # BaseTool抽象类
│   │   ├── 📄 registry.py            # 工具注册和管理
│   │   ├── 📄 prompt_generator.py    # XML提示词生成器
│   │   ├── 📄 permissions.py         # 权限控制
│   │   └── 📁 implementations/       # 具体工具实现
│   │       ├── 📄 __init__.py
│   │       ├── 📄 web_search.py
│   │       ├── 📄 web_fetch.py
│   │       └── 📄 artifact_ops.py    # Artifact操作工具
│   │
│   ├── 📁 models/                    # 模型接口
│   │   ├── 📄 __init__.py
│   │   └── 📄 llm.py                 # ⭐ 统一的LLM接口(基于LangChain)
│   │
│   ├── 📁 utils/                     # 工具函数
│   │   ├── 📄 __init__.py
│   │   ├── 📄 xml_parser.py          # ⭐ Robust XML解析
│   │   ├── 📄 logger.py              # 日志系统
│   │   └── 📄 retry.py               # 重试机制
│   │
│   └── 📁 api/                       # API接口层
│       ├── 📄 __init__.py
│       ├── 📄 server.py              # FastAPI/Flask服务器
│       ├── 📄 websocket.py          # WebSocket处理
│       ├── 📄 routes.py              # API路由定义
│       └── 📄 schemas.py             # Pydantic模型定义
│
├── 📁 prompts/                       # 提示词模板
│   ├── 📄 lead_agent.xml
│   ├── 📄 search_agent.xml
│   └── 📄 tools_instruction.xml
│
├── 📁 tests/                         # 测试文件
├── 📁 logs/                          # 日志目录
├── 📁 examples/                      # 示例代码
└── 📁 docs/                          # 文档
```



### 关键配置 (config.yaml)

```yaml
models:
  lead_agent:
    provider: "anthropic"
    model: "claude-3-opus"
    supports_thinking: true
  search_agent:
    provider: "openai"
    model: "gpt-4-turbo"
```



### 实施建议

1. **避免过度工程化**：先跑通MVP（Lead Agent + 一个工具），再逐步增强，不要实现"可能会用到"的功能
2. **实现顺序**：核心流程跑通→ XML解析 → 错误处理 → 权限/监控等高级特性
3. **技术债务**：可暂时跳过权限系统、完善错误处理、性能优化；必须实现XML解析、Agent通信、Artifact操作
4. **使用成熟方案**：直接用LangChain/LangGraph现成功能，不重造轮子
5. **简化状态管理**：用LangGraph内置state机制，不自己管理复杂状态
6. **合理默认值**：让系统开箱即用，避免过多配置
7. **调试友好**：关键节点加日志，包括Agent决策、工具调用、状态变化，错误信息要明确
8. **代码风格**：清晰胜过聪明，**代码即注释**——用清晰的命名和结构让代码自解释。函数
9. **不要过早优化**：先让它工作，再让它正确，最后让它快
10. **保持简单依赖**：只用必要的库，避免依赖地狱
