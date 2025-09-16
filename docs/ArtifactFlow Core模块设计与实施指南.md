# ArtifactFlow Core模块设计与实施指南

## 🏗️ 核心设计原则

### 📋 核心理念
**充分利用LangGraph能力，避免重复造轮子，保持简单直接**

---

## 🎯 五大设计原则

### 1️⃣ **工具权限控制 - 伪装路由模式**
```
原则：复用"伪装工具"模式，权限检查产生路由信号
```
- **BaseAgent层**：执行权限检查，对于需要确认的工具（CONFIRM/RESTRICTED），返回特殊的"路由信号"
- **特殊信号格式**：类似`call_subagent`，返回包含`_needs_confirmation: true`的ToolResult
- **Graph层路由**：识别特殊信号，路由到`user_confirmation`节点
- **保持一致性**：Agent认为自己在调用工具，实际触发了权限确认流程

```python
# 示例：需要确认的工具返回
ToolResult(
    success=True,
    data={
        "_needs_confirmation": True,
        "_tool_name": "send_email",
        "_params": {...},
        "_permission_level": "CONFIRM"
    }
)
```

### 2️⃣ **错误处理 - 自然流转原则**
```
原则：错误即数据，让其自然流转，由接收节点决定处理方式
```
- **不过度设计**：BaseAgent的错误已封装为AgentResponse，包含`success=False`
- **正常路由**：错误响应像正常响应一样被路由和处理
- **节点自主决策**：接收节点（通常是Lead Agent）根据错误内容决定：
  - 重试其他策略
  - 路由到其他Agent
  - 向用户报告
- **简单直接**：避免复杂的错误级别分类，让系统自然演化

### 3️⃣ **执行控制 - LangGraph原生能力**
```
原则：最大化利用LangGraph的checkpoint和interrupt机制
```
- **Checkpoint**：使用MemorySaver自动管理状态快照
- **Interrupt**：利用`interrupt_before/after`实现暂停点
- **Thread管理**：通过`thread_id`实现多会话并行
- **Controller职责**：
  - 薄封装LangGraph API
  - 管理thread生命周期
  - 处理用户确认请求

### 4️⃣ **Context管理 - 即时压缩策略**
```
原则：在需要时压缩，保持历史完整性
```
- **历史存储**：完整messages（`BaseAgent`中`_execute_generator`的`messages`，注意这个是一个node自己的messages，一个node不需要另外一个node完整的历史记录，只需要他的return agent response就行）历史存储在Graph State中
- **压缩时机**：在每个节点`build_system_prompt`前触发
- **压缩策略**：
  - Phase 1：简单字符长度截断（MVP）
  - Phase 2：智能总结和关键点提取（优化）
- **实现位置**：`context_manager.prepare_context()`在节点执行前调用

```python
# 工作流程
Graph State (完整历史) 
    ↓
ContextManager.prepare_context()  # 压缩
    ↓
build_system_prompt(compressed_context)  # 使用压缩后的
    ↓
Agent.execute()
```

### 5️⃣ **流式输出 - 事件驱动架构**
```
原则：利用LangGraph的astream_events，统一事件格式
```
- **使用原生API**：`graph.astream_events()`获取所有节点事件
- **事件类型映射**：
  - `on_chain_start` → 节点开始
  - `on_chain_stream` → 节点输出
  - `on_chain_end` → 节点完成
- **保持BaseAgent流式能力**：节点内部仍可使用Agent的stream方法

---

## 📁 Core模块文件职责

### **state.py**
- 定义`AgentState` (TypedDict)
- 包含：messages、current_task、artifacts、routing信息
- 状态更新reducer函数
- 不包含复杂逻辑，只是数据结构

### **graph.py**
- 节点定义（lead_agent_node、search_agent_node等）
- 条件路由函数（route_after_lead、route_after_search）
- Graph编译和checkpointer配置
- Interrupt points设置

### **controller.py**
- Thread生命周期管理（start/pause/resume/rollback）
- 用户确认处理（confirm_tool、reject_tool）
- 执行状态查询
- 薄封装LangGraph API，不做过多抽象

### **context_manager.py**
- `prepare_context()` - 主入口，被节点调用
- 压缩策略实现（字符截断 → 智能总结）
- Token计数工具
- 关键信息提取（未来优化）

---

## 🚫 反模式警示

1. **不要**在BaseAgent中处理graph级别的逻辑
2. **不要**创建复杂的错误分类系统（至少现在不要）
3. **不要**重新实现LangGraph已有的功能
4. **不要**过早优化Context压缩（先用简单截断）
5. **不要**在State中存储临时数据（只存储需要跨节点共享的）

---

## 🚀 编码实施顺序

### 📝 Phase 1: BaseAgent增强（保持向后兼容）

#### 1.1 修改 AgentResponse - 增加messages字段
```python
# agents/base.py
@dataclass
class AgentResponse:
    success: bool = True
    content: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    reasoning_content: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    routing: Optional[Dict[str, Any]] = None
    token_usage: Optional[Dict[str, Any]] = None
    messages: List[Dict] = field(default_factory=list)  # 新增：完整对话历史
```

#### 1.2 修改 _execute_generator - 返回messages
```python
# 在生成器最后，完成事件之前
current_response.messages = messages.copy()  # 返回完整对话历史
```

#### 1.3 修改工具执行 - 增加权限检查
```python
# agents/base.py - _execute_single_tool方法
async def _execute_single_tool(self, tool_call) -> ToolResult:
    if self.toolkit:
        tool = self.toolkit.get_tool(tool_call.name)
        
        # 检查权限级别
        if tool and tool.permission in [ToolPermission.CONFIRM, ToolPermission.RESTRICTED]:
            # 返回特殊的"需要确认"信号
            return ToolResult(
                success=True,
                data={
                    "_needs_confirmation": True,
                    "_tool_name": tool_call.name,
                    "_params": tool_call.params,
                    "_permission_level": tool.permission.value,
                    "_reason": f"Tool '{tool_call.name}' requires {tool.permission.value} permission"
                },
                metadata={"is_permission_request": True}
            )
        
        # PUBLIC工具直接执行
        return await self.toolkit.execute_tool(tool_call.name, tool_call.params)
```

**测试点**：运行 `multi_agent_test.py`，确保向后兼容

---

### 📝 Phase 2: Core基础设施

#### 2.1 创建 state.py - 定义数据结构
```python
# core/state.py
from typing import TypedDict, List, Dict, Optional, Annotated
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    """LangGraph的状态定义"""
    # 使用Annotated和reducer函数管理messages
    messages: Annotated[List[Dict], add_messages]
    
    # 基础字段
    current_task: str
    session_id: Optional[str]
    
    # 路由控制
    next_agent: Optional[str]
    last_agent: Optional[str]
    
    # 工具确认
    pending_confirmation: Optional[Dict]
    
    # Artifacts
    task_plan_id: Optional[str]
    result_artifact_ids: List[str]
    
    # 错误信息
    last_error: Optional[str]
    
    # Context管理
    context_level: str  # "full", "normal", "compact", "minimal"
```

#### 2.2 创建最小化 graph.py
```python
# core/graph.py - 第一版
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

def create_simple_graph():
    """创建最简单的工作流：Lead→Search→END"""
    workflow = StateGraph(AgentState)
    
    # 节点定义
    workflow.add_node("lead_agent", lead_agent_node)
    workflow.add_node("search_agent", search_agent_node)
    
    # 设置入口
    workflow.set_entry_point("lead_agent")
    
    # 简单路由
    workflow.add_edge("lead_agent", "search_agent")
    workflow.add_edge("search_agent", END)
    
    # 编译（带checkpoint）
    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)
```

**测试点**：创建 `test_simple_graph.py`，测试基本流程

---

### 📝 Phase 3: 增加权限控制流程

#### 3.1 扩展 graph.py - 增加确认节点
```python
# core/graph.py - 增加权限控制
def create_graph_with_confirmation():
    workflow = StateGraph(AgentState)
    
    # 所有节点
    workflow.add_node("lead_agent", lead_agent_node)
    workflow.add_node("search_agent", search_agent_node)
    workflow.add_node("crawl_agent", crawl_agent_node)
    workflow.add_node("user_confirmation", user_confirmation_node)
    
    # 条件路由
    workflow.add_conditional_edges(
        "lead_agent",
        route_after_lead,
        {
            "search": "search_agent",
            "crawl": "crawl_agent",
            "confirm": "user_confirmation",
            "end": END
        }
    )
    
    # 设置interrupt
    workflow.add_edge("user_confirmation", "lead_agent", interrupt_before=True)
    
    return workflow.compile(checkpointer=MemorySaver())

def route_after_lead(state: AgentState) -> str:
    """Lead Agent之后的路由逻辑"""
    last_message = state["messages"][-1] if state["messages"] else {}
    content = str(last_message.get("content", ""))
    
    # 检查是否需要确认
    if "_needs_confirmation" in content:
        state["pending_confirmation"] = extract_confirmation_info(content)
        return "confirm"
    
    # 检查是否要路由到subagent
    if "_route_to" in content:
        if "search_agent" in content:
            return "search"
        elif "crawl_agent" in content:
            return "crawl"
    
    return "end"
```

#### 3.2 实现基础 controller.py
```python
# core/controller.py
from uuid import uuid4
from typing import Optional, Dict, Any

class ExecutionController:
    def __init__(self, graph, checkpointer=None):
        self.graph = graph
        self.checkpointer = checkpointer
        self.active_threads = {}
    
    async def start_task(self, task: str, session_id: Optional[str] = None) -> str:
        """启动新任务"""
        thread_id = str(uuid4())
        config = {"configurable": {"thread_id": thread_id}}
        
        initial_state = {
            "current_task": task,
            "session_id": session_id or str(uuid4()),
            "messages": [],
            "context_level": "normal"
        }
        
        self.active_threads[thread_id] = {
            "status": "running",
            "task": task
        }
        
        return thread_id, config, initial_state
    
    async def confirm_tool(self, thread_id: str, approved: bool, reason: Optional[str] = None):
        """处理工具确认"""
        if thread_id not in self.active_threads:
            raise ValueError(f"Thread {thread_id} not found")
        
        # 更新状态，恢复执行
        update_data = {
            "tool_confirmation": {
                "approved": approved,
                "reason": reason
            }
        }
        
        config = {"configurable": {"thread_id": thread_id}}
        return await self.graph.aupdate(config, update_data)
```

**测试点**：测试工具确认流程

---

### 📝 Phase 4: Context管理（可延后）

#### 4.1 实现 context_manager.py
```python
# core/context_manager.py
class ContextManager:
    """Context压缩管理器"""
    
    COMPRESSION_LEVELS = {
        'full': 50000,      # 完整上下文
        'normal': 20000,    # 标准压缩
        'compact': 10000,   # 紧凑模式
        'minimal': 5000     # 最小化
    }
    
    def prepare_context(self, messages: List[Dict], level: str = "normal") -> List[Dict]:
        """准备上下文（Phase 1: 简单截断）"""
        max_length = self.COMPRESSION_LEVELS.get(level, 20000)
        total_length = sum(len(m.get("content", "")) for m in messages)
        
        if total_length <= max_length:
            return messages
        
        # 保留最新的消息
        truncated = []
        current_length = 0
        
        for msg in reversed(messages):
            msg_length = len(msg.get("content", ""))
            if current_length + msg_length > max_length:
                # 添加截断提示
                truncated.insert(0, {
                    "role": "system",
                    "content": f"[Earlier messages truncated due to length limit]"
                })
                break
            truncated.insert(0, msg)
            current_length += msg_length
        
        return truncated
    
    def estimate_tokens(self, text: str) -> int:
        """估算token数（简单实现）"""
        # 粗略估算：平均每4个字符一个token
        return len(text) // 4
```

---

## 📊 实施时间表

### Week 1: BaseAgent增强 ✅
- [ ] 修改AgentResponse，增加messages字段
- [ ] 修改_execute_generator，返回对话历史
- [ ] 增加工具权限检查逻辑
- [ ] 测试向后兼容性

### Week 2: Core基础 🏗️
- [ ] 编写state.py定义
- [ ] 实现最简单的graph.py
- [ ] 创建基础测试脚本
- [ ] 验证Lead→Search流程

### Week 3: 权限控制 🔐
- [ ] 增加user_confirmation节点
- [ ] 实现条件路由逻辑
- [ ] 编写controller.py基础版
- [ ] 测试工具确认流程

### Week 4: 优化完善 ⚡
- [ ] 实现context_manager.py
- [ ] 增加错误处理
- [ ] 完善路由逻辑
- [ ] 端到端集成测试

---

## ✅ MVP检查清单

**第一版必须实现的核心功能：**
- [ ] Graph能完成Lead→Search→Lead的简单流程
- [ ] 工具确认能触发interrupt并等待用户输入
- [ ] Context在超长时能自动截断
- [ ] Thread可以暂停和恢复
- [ ] 错误能正常传递给Lead Agent处理

---

## 🎯 核心原则提醒

1. **Make it work → Make it right → Make it fast**
2. **每一步都要可测试**
3. **保持向后兼容**
4. **从简单到复杂**
5. **充分利用LangGraph，不重造轮子**

---

## 📚 参考资源

- [LangGraph Documentation](https://python.langchain.com/docs/langgraph)
- [LangGraph Checkpointing](https://langchain-ai.github.io/langgraph/how-tos/persistence/)
- [LangGraph Streaming](https://langchain-ai.github.io/langgraph/how-tos/streaming/)
- 项目文档：`Multi-Agent研究系统设计提示词.md`