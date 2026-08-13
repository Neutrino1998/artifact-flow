# 🤖 基于Artifact的Multi-Agent研究系统设计方案

## 💡 核心理念

采用**Collaborative Authoring**模式，通过Artifact作为共享记忆载体。摒弃传统黑盒式一次性生成，转向透明、可控、渐进式的研究过程。相比传统「一次性生成」或「长对话上下文」，Artifact机制能**持续积累和组织信息**，避免上下文混乱，让用户和AI真正协作构建研究成果。

## 🏗️ 系统架构

系统采用分层设计，以Artifact作为核心记忆载体，Lead Agent负责任务协调，多个Subagent专门处理具体任务。通过明确的权限控制和单向信息流，确保系统运行的稳定性和可控性。

### 🧩 核心组件

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

### 🔄 工作流程

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

#### 阶段1: 研究规划

1. 用户提出研究需求
2. Lead Agent分析需求，创建Task Plan Artifact和初始Result Artifact框架
3. 系统进入执行阶段

#### 阶段2: 信息收集

1. Lead Agent根据Task Plan派发搜索任务
2. **Search Agent读取Task Plan**，了解具体搜索需求和上下文
3. Search Agent独立执行搜索，**返回结果给Lead Agent**（不直接修改任何Artifact）
4. Lead Agent接收结果后：
   - 更新Task Plan中的任务状态
   - 将信息整合到Result Artifact
   - 评估是否需要进一步抓取
5. 必要时派发URL给Web Crawl Agent，重复上述流程

#### 阶段3: 协作优化

1. **任务完成后的交互**：Lead Agent完成一轮任务执行，向用户展示更新后的Result Artifact
2. **用户反馈方式：**
   - 直接编辑Result Artifact（修改内容、调整结构、添加要求）
   - 通过对话向Lead Agent提出改进要求（"需要更多临床数据"、"增加市场分析章节"）
3. **Lead Agent响应**：根据用户反馈调整Task Plan，派发新的任务或优化现有内容
4. **迭代循环**：重复执行直到用户满意

## 📚 详细示例

### 示例1: AI医疗应用研究报告

Task Plan Artifact

```markdown
# AI医疗应用研究进度
## 项目状态: 🔄 进行中
## 当前执行: Search Agent - 搜索FDA批准的AI医疗设备

### 研究大纲
#### 1. AI医疗应用概述
- 状态: ✅ 已完成 (信息充分度: 85%)
- 来源: 12篇权威文献, 3个官方报告
- 质量评估: 高质量，涵盖主要应用领域

#### 2. 临床试验现状
- 状态: 🔄 进行中 (60%)
- 当前任务: 搜索2024年FDA批准的AI医疗设备清单
- 已收集: 15个批准设备，8个临床试验案例
- 下一步: 深度抓取具体产品信息和临床数据
- [暂停此任务] [调整搜索策略]

#### 3. 市场分析
- 状态: ⏳ 等待中
- 依赖: 需要第2章完成后启动
- 预计信息需求: 市场规模、主要厂商、投资趋势

#### 4. 未来展望
- 状态: ⏳ 计划中
- 用户备注: 重点关注监管政策变化
```

对应的Result Artifact (部分)

```markdown
# AI医疗应用研究报告

## 1. AI医疗应用概述

人工智能在医疗领域的应用正在快速发展，主要集中在医学影像分析、药物发现、临床决策支持等领域。根据最新统计，截至2024年...

### 1.1 医学影像AI
- **应用领域**: 放射学、病理学、眼科等
- **技术成熟度**: 商业化程度较高
- **代表产品**: [具体产品列表]

### 1.2 临床决策支持
- **应用场景**: 疾病诊断、治疗方案推荐
- **技术挑战**: 数据质量、模型可解释性
- **监管现状**: FDA已批准多款产品

## 2. 临床试验现状 [正在更新中...]

### 2.1 FDA批准设备统计
[正在收集最新数据...]

### 2.2 临床试验分析
[等待Search Agent返回结果...]

---
*本报告由AI系统协助生成，用户可随时编辑修改*
```

### 示例2: 用户干预场景

#### 场景: 用户想要添加新的研究方向

**用户操作方式1**: 在Result Artifact中添加新章节框架

```markdown
## 5. 数据隐私与安全 [用户新增]
### 5.1 HIPAA合规要求
[请补充相关信息]
### 5.2 数据泄露案例分析
[需要收集近期案例]
```

**用户操作方式2**: 通过对话向Lead Agent提出要求

> "我发现数据隐私是AI医疗的重要挑战，需要增加一个专门章节来分析HIPAA合规和安全技术方案"

**系统响应**: Lead Agent识别用户需求，自动更新Task Plan添加相应搜索任务

#### 场景: 用户对信息质量不满意

**用户操作方式1**: 在Result Artifact中添加批注

```markdown
## 2. 临床试验现状
[当前内容多为新闻报道，缺少学术论文支撑，请补充更权威的研究数据]
```

**用户操作方式2**: 通过对话提出具体要求

> "第2章的信息质量不够好，多是新闻报道，能不能重新搜索一些学术论文和FDA的官方数据？"

**系统响应**: Lead Agent理解反馈，更新Task Plan重新执行搜索任务

## ⚙️ 功能设计要点

系统采用Lead Agent统一协调的方式，通过Task Plan展示进度，Subagent独立执行具体任务并返回结构化结果。Lead Agent负责质量评估和信息整合，用户通过编辑Result Artifact或对话来表达改进需求。整个过程实现多层质量控制，从Subagent的初步筛选到Lead Agent的综合评估，最终由用户进行质量把关。

## 🎯 系统优势

相比传统的多层嵌套架构，这种设计具有更好的透明度和可控性。Task Plan提供完整的进度可视化，用户能清楚了解系统工作状态。通过简化交互模式，用户只需要编辑文档和正常对话，避免了复杂的任务管理界面。系统易于扩展新的Agent类型，支持不同研究场景的复用。

## 🚀 实现路径

建议分四个阶段实施：首先实现Lead Agent和基础Artifact机制，开发核心的Search Agent和Web Crawl Agent；然后完善用户交互界面，支持实时进度跟踪和编辑反馈；接着增强信息质量评估和智能任务调度；最后扩展到更多研究场景，建立提示词模板库和最佳实践。

## ✨ 关键创新点

1. **Artifact作为记忆载体**: 解决了传统multi-agent系统的上下文管理问题
2. **分层权限控制**: Lead Agent全权限，Subagent只读Task Plan，用户只读Task Plan但可编辑Result
3. **简化交互模式**: 避免复杂的任务管理界面，用户通过编辑文档和对话来表达需求
4. **双Artifact分离**: Task Plan专注进度展示，Result Doc专注内容协作
5. **渐进式协作**: 从一次性生成转向迭代优化
6. **透明化执行**: 每个步骤都可追踪但不干扰执行
7. **信息流单向性**: Subagent → Lead Agent → Artifact，避免并发冲突

这种设计让研究过程变成了一个真正的人机协作过程，充分发挥了人类的创造力和AI的信息处理能力。



# Multi-Agent研究系统 - 后端实施细节

## 🎯 核心功能模块

### 1. Human in the Loop 机制

**原始需求**: 允许用户通过对话介入/打断agent执行的任务

**实施细节**:

- **基础控制接口**:

  ```python
  class ExecutionController:
      def __init__(self, graph, checkpointer):
          self.graph = graph
          self.checkpointer = checkpointer
          self.is_paused = False
          self.additional_context = []
      
      async def pause(self, thread_id):
          """暂停执行"""
          self.is_paused = True
          # LangGraph会在下个节点前自动保存checkpoint
          
      async def resume(self, thread_id):
          """恢复执行，带入用户补充的context"""
          config = {"configurable": {"thread_id": thread_id}}
          state = self.graph.get_state(config)
          
          # 将补充的context加入state
          if self.additional_context:
              state.values["user_context"] = "\n".join(self.additional_context)
              self.additional_context = []
          
          self.is_paused = False
          return await self.graph.invoke(None, config)
      
      async def rollback(self, thread_id, checkpoint_id=None):
          """回滚到指定checkpoint"""
          config = {"configurable": {
              "thread_id": thread_id,
              "checkpoint_id": checkpoint_id  # 如果None则回滚到上一个
          }}
          return self.graph.get_state(config)
      
      def add_context(self, user_query):
          """暂停状态下，用户输入会被添加为补充context"""
          if self.is_paused:
              self.additional_context.append(user_query)
  ```

- **Lead Agent处理补充context**:

  ```python
  def lead_agent_node(state):
      # 检查是否有用户补充的context
      user_context = state.get("user_context", "")
      
      # 构建prompt时包含用户补充信息
      prompt = f"""
      原始任务: {state["original_task"]}
      当前进度: {state["progress"]}
      
      {f"用户补充要求: {user_context}" if user_context else ""}
      
      请继续执行任务...
      """
      
      # 清空已使用的user_context
      state["user_context"] = ""
      return state
  ```

- **使用示例**:

  ```python
  # 用户发起任务
  controller = ExecutionController(graph, checkpointer)
  task = controller.start("研究AI医疗应用")
  
  # 用户暂停
  await controller.pause("thread_123")
  
  # 用户补充信息（在暂停状态下）
  controller.add_context("重点关注FDA批准的产品")
  controller.add_context("需要包含2024年的最新数据")
  
  # 恢复执行（自动带入补充的context）
  await controller.resume("thread_123")
  
  # 如需回滚
  await controller.rollback("thread_123")
  ```

### 2. Context Compression 策略

**原始需求**: 智能判断对context进行压缩（初期2w字符截断）

**实施细节**:

- **分级压缩策略**:

  ```python
  # 伪代码示例
  class ContextManager:
      LEVELS = {
          'full': 50000,      # 完整上下文
          'normal': 20000,    # 标准压缩
          'compact': 10000,   # 紧凑模式
          'minimal': 5000     # 最小化
      }
  ```

- **智能压缩算法**:

  - **优先级保留**: 保留最近N轮对话 + 关键决策点
  - **摘要替换**: 将历史对话压缩为摘要
  - **结构化压缩**: 保留JSON/XML结构，压缩描述性文本
  - **相关性筛选**: 基于当前任务筛选相关上下文

- **压缩触发条件**:

  - Token数超过阈值
  - 内存使用超限
  - 特定任务类型（如简单搜索）自动使用紧凑模式

### 3. 非阻塞式响应机制

**原始需求**: Lead Agent流式返回，Subagent批量返回

**实施细节**:

- **流式处理架构**:

  ```python
  # Lead Agent 流式返回
  async def lead_agent_stream():
      async for chunk in model.astream():
          yield chunk
          await update_artifact_realtime(chunk)
  
  # Subagent 批量返回
  async def subagent_execute():
      result = await model.ainvoke()
      return parse_complete_result(result)
  ```

- **并发执行管理**:

  - 使用asyncio管理多个Subagent并发
  - 实现任务队列和线程池
  - 支持动态调整并发数

- **结果缓冲策略**:

  - Lead Agent: 逐字符/逐词流式输出
  - Subagent: 结果完成后批量返回
  - 支持部分结果预览（如搜索结果实时显示前N条）

### 4. LangChain/LangGraph 集成

**原始需求**: 使用成熟框架避免造轮子

**实施细节**:

- **Graph设计**:

  ```python
  # 使用LangGraph构建工作流
  from langgraph.graph import StateGraph
  from langgraph.checkpoint.memory import MemorySaver
  
  # 配置checkpoint
  memory = MemorySaver()
  
  workflow = StateGraph(AgentState)
  workflow.add_node("lead_agent", lead_agent_node)
  workflow.add_node("search_agent", search_agent_node)
  workflow.add_node("crawl_agent", crawl_agent_node)
  
  # 配置条件边
  workflow.add_conditional_edges(
      "lead_agent",
      route_to_subagent,
      {
          "search": "search_agent",
          "crawl": "crawl_agent",
          "continue": "lead_agent"
      }
  )
  
  # 编译带checkpoint的graph
  app = workflow.compile(checkpointer=memory)
  ```

- **自定义组件**:

  - 继承BaseAgent实现自定义Agent逻辑
  - 自定义Tool包装器支持权限控制
  - 实现自定义Memory组件管理Artifact

- **状态管理**:

  - 使用LangGraph的State机制管理任务状态
  - 利用内置Checkpointer支持断点续传

### 5. 多模型支持与思考模型集成

**原始需求**: 支持不同agent调用不同模型，正确解析think部分

**实施细节**:

- **模型配置管理**:

  ```yaml
  models:
    lead_agent:
      provider: "anthropic"
      model: "claude-3-opus"
      temperature: 0.7
      supports_thinking: true
    
    search_agent:
      provider: "openai"
      model: "gpt-4-turbo"
      temperature: 0.3
      supports_thinking: false
  ```

- **思考模型处理**:

  ```python
  class ThinkingModelParser:
      def parse_response(self, response):
          thinking = extract_thinking_tags(response)
          answer = extract_answer_tags(response)
          return {
              'thinking': thinking,  # 内部推理过程
              'answer': answer,      # 实际响应
              'metadata': {...}      # 其他元数据
          }
  ```

- **模型切换策略**:

  - 基于任务类型自动选择模型
  - 支持fallback机制（主模型失败切换备用）
  - 成本优化（简单任务用小模型）

### 6. XML工具调用系统

**原始需求**: 不用tool call接口，自己设计XML形式的函数调用

**实施细节**:

- **统一的XML Schema**:

  ```xml
  <!-- 工具定义 -->
  <tool>
    <name>web_search</name>
    <description>Search the web for information</description>
    <parameters>
      <param name="query" type="string" required="true"/>
      <param name="max_results" type="int" default="10"/>
    </parameters>
    <permissions>
      <require_approval>false</require_approval>
    </permissions>
  </tool>
  
  <!-- 工具调用 -->
  <tool_call>
    <name>web_search</name>
    <params>
      <query>AI medical applications FDA approval</query>
      <max_results>20</max_results>
    </params>
  </tool_call>
  ```

- **提示词生成器**:

  ```python
  class ToolPromptGenerator:
      def generate_system_prompt(self, tools):
          # 自动生成工具使用说明
          return f"""
          Available tools:
          {self.format_tools_xml(tools)}
          
          To use a tool, wrap your call in <tool_call> tags...
          """
  ```

- **动态工具注册**:

  - 支持运行时注册新工具
  - 自动生成对应的XML schema和提示词
  - 工具版本管理

### 7. Robust XML解析

**原始需求**: 健壮的XML解析函数

**实施细节**:

- **多层解析策略**:

  ```python
  class RobustXMLParser:
      def parse(self, text):
          # 1. 尝试标准XML解析
          try:
              return self.standard_parse(text)
          except:
              pass
          
          # 2. 尝试修复常见错误
          fixed = self.fix_common_issues(text)
          try:
              return self.standard_parse(fixed)
          except:
              pass
          
          # 3. 使用正则提取
          return self.regex_fallback(text)
      
      def fix_common_issues(self, text):
          # 修复未闭合标签
          # 转义特殊字符
          # 处理嵌套错误
          pass
  ```

- **错误恢复机制**:

  - 部分解析成功时返回可用部分
  - 记录解析失败的详细信息
  - 提供修复建议

- **验证层**:

  - Schema验证
  - 业务逻辑验证
  - 安全性检查（防止注入）

### 8. 完善的Logging系统

**原始需求**: 跟踪所有行为：模型I/O、token count、工具调用

**实施细节**:

- **分层日志架构**:

  ```python
  class MultiAgentLogger:
      LEVELS = {
          'SYSTEM': logging.CRITICAL,     # 系统级事件
          'AGENT': logging.INFO,          # Agent决策
          'TOOL': logging.INFO,           # 工具调用
          'MODEL': logging.DEBUG,         # 模型交互
          'TOKEN': logging.DEBUG          # Token统计
      }
  ```

- **结构化日志格式**:

  ```json
  {
    "timestamp": "2024-01-20T10:30:00Z",
    "session_id": "sess_123",
    "agent": "lead_agent",
    "event_type": "model_call",
    "model": "claude-3-opus",
    "tokens": {
      "input": 1500,
      "output": 800,
      "total": 2300
    },
    "cost": 0.023,
    "duration_ms": 2500,
    "metadata": {...}
  }
  ```

- **监控指标**:

  - 实时Token消耗统计
  - API调用延迟监控
  - 错误率和重试统计
  - 任务完成时间分析

- **日志存储策略**:

  - 使用文件系统存储所有日志
  - 按日期分割日志文件（如：`logs/2024-01-20.json`）
  - 可选的日志轮转（如：保留最近30天）

### 9. 工具权限控制系统

**原始需求**: 工具包含可选的permission接口

**实施细节**:

- **权限级别定义**:

  ```python
  class PermissionLevel(Enum):
      PUBLIC = 0      # 无需审批
      NOTIFY = 1      # 执行后通知
      CONFIRM = 2     # 执行前确认
      RESTRICTED = 3  # 需要特殊授权
  ```

- **审批流程**:

  ```python
  class ToolPermissionManager:
      async def check_permission(self, tool, params, user):
          level = tool.permission_level
          
          if level == PermissionLevel.CONFIRM:
              approval = await self.request_approval(
                  user, tool, params
              )
              if not approval:
                  raise PermissionDeniedError()
          
          # 记录审批日志
          self.log_permission_check(...)
  ```

- **细粒度控制**:

  - 基于用户角色的权限
  - 基于参数的权限（如文件路径限制）
  - 时间窗口限制（如每小时最多N次）

### 10. Checkpoint机制

**原始需求**: 支持checkpoint断点续传

**实施细节**:

- **使用LangGraph内存Checkpoint**:

  ```python
  from langgraph.checkpoint.memory import MemorySaver
  
  # 使用内存存储checkpoint
  memory = MemorySaver()
  
  # 编译时指定checkpointer
  app = workflow.compile(checkpointer=memory)
  
  # 恢复checkpoint
  config = {"configurable": {"thread_id": "session_123"}}
  state = app.get_state(config)
  
  # 从checkpoint继续执行
  result = app.invoke(None, config)
  ```

- **简单的状态管理**:

  ```python
  class SimpleCheckpointManager:
      def __init__(self):
          self.checkpoints = {}  # 内存中的checkpoint存储
      
      def save(self, thread_id, state):
          """保存checkpoint到内存"""
          self.checkpoints[thread_id] = {
              'timestamp': datetime.now(),
              'state': state,
              'version': len(self.checkpoints.get(thread_id, []))
          }
      
      def load(self, thread_id):
          """从内存加载checkpoint"""
          return self.checkpoints.get(thread_id)
      
      def clear(self, thread_id=None):
          """清理checkpoint"""
          if thread_id:
              self.checkpoints.pop(thread_id, None)
          else:
              self.checkpoints.clear()
  ```

- **注意事项**:

  - 内存存储适合开发和测试环境
  - 重启服务会丢失所有checkpoint
  - 可根据需要后续升级到持久化存储

### 11. 错误处理与重试机制

- **智能重试策略**:

  ```python
  from tenacity import retry, stop_after_attempt, wait_exponential
  
  @retry(
      stop=stop_after_attempt(3),
      wait=wait_exponential(multiplier=1, min=4, max=10)
  )
  async def call_model_with_retry(prompt):
      return await model.ainvoke(prompt)
  ```

- **错误分类处理**:

  - API限流: 指数退避重试
  - 网络错误: 快速重试
  - 解析错误: 降级到备用解析器
  - 业务错误: 记录并跳过

- **降级方案**:

  - 主模型失败切换备用模型
  - 复杂工具失败降级到简单版本
  - 完整搜索失败降级到快速搜索



# Multi-Agent研究系统 - 项目文件结构

## 📁 完整目录树

```
multi-agent-research/
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
│   ├── 📄 __init__.py
│   ├── 📄 conftest.py               # Pytest配置
│   ├── 📁 unit/
│   │   ├── 📄 test_xml_parser.py
│   │   ├── 📄 test_context_manager.py
│   │   └── 📄 test_tools.py
│   ├── 📁 integration/
│   │   ├── 📄 test_workflow.py
│   │   └── 📄 test_agents.py
│   └── 📁 fixtures/                  # 测试数据
│       ├── 📄 sample_responses.json
│       └── 📄 mock_data.yaml
│
├── 📁 logs/                          # 日志目录
│   └── 📄 .gitkeep
│
├── 📁 examples/                      # 示例代码
│   ├── 📄 basic_research.py         # 基础研究任务示例
│   ├── 📄 with_interruption.py      # 带中断的示例
│   └── 📄 custom_agent.py            # 自定义Agent示例
│
└── 📁 docs/                          # 文档
    ├── 📄 architecture.md            # 架构说明
    ├── 📄 api.md                     # API文档
    └── 📄 deployment.md              # 部署指南
```

## 📝 模块说明

### 🎯 核心模块 (`src/core/`)

核心工作流和状态管理，是整个系统的骨架。

- **graph.py**: LangGraph工作流定义，包含节点、边、条件路由的配置
- **state.py**: 定义AgentState数据结构，管理全局状态
- **controller.py**: 实现pause/resume/rollback等控制功能
- **context_manager.py**: 负责context压缩、截断、智能筛选

### 🤖 Agent模块 (`src/agents/`)

所有Agent的实现，遵循统一的BaseAgent接口。

- **base.py**: 定义BaseAgent抽象类，规范Agent接口
- **lead_agent.py**: 协调者，负责任务分解、派发、结果整合
- **search_agent.py**: 信息搜索专家，返回结构化搜索结果
- **crawl_agent.py**: 深度内容抓取，提取网页详细信息

### 🔧 工具系统 (`src/tools/`)

可扩展的工具注册和调用系统。

- **registry.py**: 动态注册工具，管理工具生命周期
- **prompt_generator.py**: 根据工具定义自动生成XML格式的提示词
- **permissions.py**: 实现工具权限控制（PUBLIC/NOTIFY/CONFIRM/RESTRICTED）
- **implementations/**: 具体工具实现，每个工具都是独立模块

### 🧠 模型接口 (`src/models/`)

统一的模型调用接口，基于LangChain实现。

- llm.py

  : 使用LangChain封装，支持OpenAI通用接口、Qwen、DeepSeek等模型

  ```python
  # 示例代码结构
  from langchain_openai import ChatOpenAI
  
  def get_model(model_type="openai", **kwargs):
      if model_type == "openai":
          return ChatOpenAI(**kwargs)
      elif model_type == "qwen":
          return ChatOpenAI(
              base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
              **kwargs
          )
      elif model_type == "deepseek":
          return ChatOpenAI(
              base_url="https://api.deepseek.com/v1",
              **kwargs
          )
  ```

### 🛠 工具函数 (`src/utils/`)

通用工具函数，被其他模块复用。

- **xml_parser.py**: 三层解析策略（标准解析→修复常见错误→正则提取）
- **logger.py**: 结构化日志，支持不同级别和模块的日志记录
- **retry.py**: 智能重试机制，支持指数退避
- **streaming.py**: 处理流式响应，支持Lead Agent的实时输出

### 🌐 API层 (`src/api/`)

对外接口，支持REST API和WebSocket。

- **server.py**: 主服务器入口，可选FastAPI或Flask
- **websocket.py**: 实时推送任务状态、支持双向通信
- **routes.py**: 定义所有API端点
- **schemas.py**: 请求/响应的数据模型验证

### 📋 配置和模板

- **config.yaml**: 集中管理所有配置（模型配置、工具配置、日志级别等）
- **prompts/**: XML格式的提示词模板，便于维护和版本控制

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑.env添加API keys

# 3. 修改配置
# 编辑config.yaml设置模型和工具

# 4. 运行示例
python examples/basic_research.py

# 5. 启动API服务
python -m src.api.server
```

## 💡 设计原则

1. **模块化**: 每个模块职责单一，便于测试和维护
2. **可扩展**: 通过继承BaseAgent/BaseTool轻松添加新功能
3. **配置驱动**: 主要行为通过配置文件控制，无需修改代码
4. **类型安全**: 使用Pydantic和类型提示确保数据正确性
5. **易于测试**: 清晰的依赖注入，便于Mock和单元测试

## 🔑 关键文件标注说明

- ⭐ 标记的是系统核心文件，优先实现
- 其他文件根据需要逐步添加
- tests/和docs/可以随开发进度完善

## 📝 文件创建步骤

### Phase 1: 基础设施 (第1-2天)

**目标**: 搭建项目骨架和基础工具

1. **项目初始化**

   ```bash
   # 创建目录结构
   mkdir -p src/{core,agents,tools,models,utils,api}
   mkdir -p {tests,logs,examples,docs,prompts}
   
   # 初始化文件
   touch requirements.txt .env.example .gitignore
   touch Dockerfile docker-compose.yml config.yaml
   ```

2. **Utils模块** (最先完成)

   - `utils/logger.py` - 设置日志格式和文件输出
   - `utils/xml_parser.py` - 实现robust的XML解析
   - `utils/retry.py` - 基于tenacity的重试装饰器
   - `utils/streaming.py` - 异步流处理工具

### Phase 2: 模型层 (第2-3天)

**目标**: 实现统一的LLM调用接口

1. Models模块

   - `models/llm.py` - 基于LangChain封装多模型支持

   ```python
   # 关键实现点：
   - 配置化的模型初始化
   - 统一的调用接口 (invoke/stream)
   - 思考模型的响应解析
   - Token计数和成本统计
   ```

### Phase 3: 工具系统 (第3-4天)

**目标**: 实现XML工具调用框架

1. **Tools基础框架**
   - `tools/base.py` - BaseTool抽象类
   - `tools/registry.py` - 工具注册器
   - `tools/prompt_generator.py` - XML提示词生成
   - `tools/permissions.py` - 权限控制系统
2. **具体工具实现**
   - `tools/implementations/web_search.py`
   - `tools/implementations/artifact_ops.py`

### Phase 4: 核心工作流 (第4-5天)

**目标**: 搭建LangGraph工作流

1. Core模块
   - `core/state.py` - 定义AgentState
   - `core/graph.py` - LangGraph工作流配置
   - `core/controller.py` - 执行控制器
   - `core/context_manager.py` - Context管理

### Phase 5: Agent实现 (第5-6天)

**目标**: 实现各类Agent

1. Agents模块
   - `agents/base.py` - BaseAgent接口
   - `agents/lead_agent.py` - 主协调Agent
   - `agents/search_agent.py` - 搜索Agent
   - `agents/crawl_agent.py` - 抓取Agent

### Phase 6: API接口 (第6-7天)

**目标**: 对外服务接口

1. API模块
   - `api/schemas.py` - Pydantic数据模型
   - `api/server.py` - FastAPI应用
   - `api/routes.py` - 路由定义
   - `api/websocket.py` - WebSocket支持

### Phase 7: 测试和文档 (第7-8天)

**目标**: 完善测试和文档

1. **测试用例**
   - 单元测试 (utils, tools)
   - 集成测试 (workflow, agents)
2. **运行环境**
   - 完善Dockerfile
   - 编写examples
   - 更新README

## 🚀 快速验证每个阶段

### Phase 1 验证

```python
# 测试logger
from src.utils.logger import get_logger
logger = get_logger("test")
logger.info("Logger working!")

# 测试XML解析
from src.utils.xml_parser import parse_xml
result = parse_xml("<tool><name>test</name></tool>")
```

### Phase 2 验证

```python
# 测试模型调用
from src.models.llm import get_model
model = get_model("openai", model="gpt-4")
response = model.invoke("Hello")
```

### Phase 3 验证

```python
# 测试工具注册和调用
from src.tools.registry import ToolRegistry
registry = ToolRegistry()
registry.register(my_tool)
prompt = registry.generate_prompt()
```

### Phase 4 验证

```python
# 测试基础工作流
from src.core.graph import create_workflow
app = create_workflow()
result = app.invoke({"task": "test"})
```

### Phase 5 验证

```python
# 端到端测试
from src.agents.lead_agent import LeadAgent
agent = LeadAgent()
result = await agent.execute("研究AI医疗")
```
