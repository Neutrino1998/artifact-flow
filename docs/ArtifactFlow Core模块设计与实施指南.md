# ArtifactFlow Core模块设计与实施指南 V3

## 🎯 核心架构理念

### 三层历史管理架构
```
┌─────────────────────────────────────────┐
│  Layer 1: User ↔ Graph 对话历史          │ ← 支持分支/回滚/编辑
├─────────────────────────────────────────┤
│  Layer 2: Graph State (AgentState)      │ ← 节点间共享状态
├─────────────────────────────────────────┤
│  Layer 3: Agent Internal Messages       │ ← 工具调用循环
└─────────────────────────────────────────┘
```

### 消息组成结构（Agent内部）
```
┌─────────────────────────────────────────┐
│         系统提示词 (动态生成)             │ ← build_system_prompt()
├─────────────────────────────────────────┤
│       初始用户请求 (持久存储)             │ ← NodeMemory存储
├─────────────────────────────────────────┤
│    LLM与工具交互历史 (可压缩)            │ ← Context Manager作用域
└─────────────────────────────────────────┘
```

---

## 🔄 分支对话管理

### 对话树结构
```
user_msg_1 → graph_response_1
    ↓
user_msg_2 → graph_response_2  ← 编辑点
    ├──→ user_msg_3 → graph_response_3 (原分支)
    │         ↓
    │    user_msg_4 → graph_response_4
    │
    └──→ user_msg_2_edited → graph_response_2_edited (新分支)
              ↓
         user_msg_5 → graph_response_5
```

### 实现机制
- 每个用户消息创建新的thread_id
- 编辑消息时fork当前thread，创建新分支
- 保存分支关系树，支持切换和追溯

---

## 📊 Graph State设计（支持扩展）

```python
# core/state.py
from typing import TypedDict, Dict, List, Optional, Annotated
from langgraph.graph.message import add_messages

class NodeMemory(TypedDict):
    """单个节点的记忆"""
    initial_instruction: str           # 初始用户请求
    messages: List[Dict]               # LLM与工具交互历史(不含system)
    last_response: Optional[Dict]      # 最后的AgentResponse
    tool_rounds: int                   # 工具调用轮次
    
class AgentState(TypedDict):
    """LangGraph全局状态（可扩展）"""
    # 基础信息
    current_task: str
    session_id: str
    thread_id: str
    parent_thread_id: Optional[str]    # 分支父节点
    
    # 🔑 可扩展的节点记忆（支持动态添加Agent）
    agent_memories: Dict[str, NodeMemory]  # key: agent_name
    
    # 路由控制
    next_agent: Optional[str]
    last_agent: Optional[str]
    routing_info: Optional[Dict]
    
    # 权限确认
    pending_tool_confirmation: Optional[Dict]
    
    # Artifacts
    task_plan_id: Optional[str]
    result_artifact_ids: List[str]
    
    # Context管理
    compression_level: str  # "full", "normal", "compact"
    
    # 用户对话层
    user_message_id: str               # 当前用户消息ID
    graph_response: Optional[str]      # Graph最终响应

class ConversationTree(TypedDict):
    """用户对话树（Layer 1）"""
    conversation_id: str
    branches: Dict[str, List[str]]      # parent_msg_id -> [child_msg_ids]
    messages: Dict[str, UserMessage]    # msg_id -> message
    active_branch: str                  # 当前活跃分支

class UserMessage(TypedDict):
    """用户消息节点"""
    message_id: str
    parent_id: Optional[str]
    content: str
    thread_id: str                      # 关联的Graph执行线程
    timestamp: str
    graph_response: Optional[str]
    metadata: Dict
```

---

## 🏗️ 可扩展的Graph设计

### 动态Agent注册机制
```python
# core/graph.py
from typing import Dict, Callable
from langgraph.graph import StateGraph, END

class ExtendableGraph:
    """可扩展的Graph构建器"""
    
    def __init__(self):
        self.workflow = StateGraph(AgentState)
        self.agents: Dict[str, BaseAgent] = {}
        self.node_functions: Dict[str, Callable] = {}
        
        # 注册核心节点
        self._register_core_nodes()
    
    def register_agent(self, agent: BaseAgent):
        """注册新Agent（支持运行时添加）"""
        agent_name = agent.config.name
        self.agents[agent_name] = agent
        
        # 创建节点函数
        node_func = self._create_node_function(agent_name)
        self.node_functions[agent_name] = node_func
        
        # 添加到workflow
        self.workflow.add_node(agent_name, node_func)
        
        # 添加通用路由规则
        self._add_routing_rules(agent_name)
        
        print(f"✅ Registered agent: {agent_name}")
    
    def _create_node_function(self, agent_name: str):
        """为Agent创建通用节点函数"""
        async def agent_node(state: AgentState) -> AgentState:
            agent = self.agents[agent_name]
            
            # 获取或创建节点记忆
            if agent_name not in state.get("agent_memories", {}):
                state.setdefault("agent_memories", {})[agent_name] = None
            
            memory = state["agent_memories"].get(agent_name)
            
            # 判断是恢复执行还是新任务
            if state.get("pending_tool_confirmation") and \
               state.get("last_agent") == agent_name:
                # 恢复执行
                response = await agent.execute(
                    instruction="",
                    external_history=memory["messages"] if memory else [],
                    pending_tool_result=state["pending_tool_confirmation"]["result"]
                )
            else:
                # 新任务或子任务
                if agent_name == "lead_agent":
                    instruction = state["current_task"]
                else:
                    # 子Agent从routing_info获取指令
                    instruction = state.get("routing_info", {}).get("instruction", "")
                
                response = await agent.execute(instruction)
            
            # 保存记忆
            state["agent_memories"][agent_name] = NodeMemory(
                initial_instruction=instruction if instruction else memory.get("initial_instruction", ""),
                messages=response.messages,
                last_response=response.to_dict(),
                tool_rounds=response.metadata.get("tool_rounds", 0)
            )
            
            # 处理路由
            self._handle_routing(state, response, agent_name)
            
            return state
        
        return agent_node
    
    def _add_routing_rules(self, agent_name: str):
        """添加Agent的路由规则"""
        # 所有Agent都可以路由到user_confirmation
        def route_func(state: AgentState) -> str:
            if state.get("next_agent"):
                next_node = state["next_agent"]
                state["next_agent"] = None  # 清空
                return next_node
            return END
        
        self.workflow.add_conditional_edges(
            agent_name,
            route_func,
            {
                "user_confirmation": "user_confirmation",
                "lead_agent": "lead_agent",
                "search_agent": "search_agent", 
                "crawl_agent": "crawl_agent",
                END: END
            }
        )
    
    def _handle_routing(self, state: AgentState, response, agent_name: str):
        """统一的路由处理"""
        state["last_agent"] = agent_name
        
        if response.routing:
            routing = response.routing
            
            if routing["type"] == "permission_confirmation":
                state["next_agent"] = "user_confirmation"
                state["pending_tool_confirmation"] = {
                    "tool_name": routing["tool_name"],
                    "params": routing["params"],
                    "from_agent": agent_name,
                    "permission_level": routing.get("permission_level")
                }
            elif routing["type"] == "subagent":
                state["next_agent"] = routing["target"]
                state["routing_info"] = routing
            else:
                # 可扩展其他路由类型
                state["routing_info"] = routing
    
    def compile(self):
        """编译Graph"""
        from langgraph.checkpoint import MemorySaver
        checkpointer = MemorySaver()
        return self.workflow.compile(
            checkpointer=checkpointer,
            interrupt_before=["user_confirmation"]  # 用户确认前中断
        )
```

---

## 🎭 对话管理器（支持分支）

```python
# core/conversation_manager.py
from uuid import uuid4
from typing import Optional, Dict, List

class ConversationManager:
    """用户对话管理器（Layer 1）"""
    
    def __init__(self, graph):
        self.graph = graph
        self.conversations: Dict[str, ConversationTree] = {}
    
    def start_conversation(self) -> str:
        """开始新对话"""
        conv_id = str(uuid4())
        self.conversations[conv_id] = ConversationTree(
            conversation_id=conv_id,
            branches={},
            messages={},
            active_branch=""
        )
        return conv_id
    
    async def send_message(
        self,
        conv_id: str,
        user_content: str,
        parent_msg_id: Optional[str] = None
    ) -> UserMessage:
        """发送用户消息（可能创建分支）"""
        conversation = self.conversations[conv_id]
        msg_id = str(uuid4())
        thread_id = str(uuid4())
        
        # 如果有parent，检查是否创建分支
        if parent_msg_id and parent_msg_id in conversation["messages"]:
            parent = conversation["messages"][parent_msg_id]
            # 检查parent是否已有子消息（需要分支）
            if parent_msg_id in conversation["branches"]:
                print(f"🌿 Creating new branch from message {parent_msg_id}")
        
        # 创建消息
        user_msg = UserMessage(
            message_id=msg_id,
            parent_id=parent_msg_id,
            content=user_content,
            thread_id=thread_id,
            timestamp=datetime.now().isoformat(),
            graph_response=None,
            metadata={}
        )
        
        # 保存消息和分支关系
        conversation["messages"][msg_id] = user_msg
        if parent_msg_id:
            conversation["branches"].setdefault(parent_msg_id, []).append(msg_id)
        
        # 执行Graph
        initial_state = {
            "current_task": user_content,
            "session_id": conv_id,
            "thread_id": thread_id,
            "parent_thread_id": parent.get("thread_id") if parent_msg_id else None,
            "user_message_id": msg_id,
            "agent_memories": {},
            "compression_level": "normal"
        }
        
        # 如果是从某个分支继续，复制父节点的状态
        if parent_msg_id and parent_msg_id in conversation["messages"]:
            parent_thread = conversation["messages"][parent_msg_id]["thread_id"]
            parent_state = await self._get_thread_state(parent_thread)
            if parent_state:
                # 复制关键状态（artifacts等）
                initial_state["task_plan_id"] = parent_state.get("task_plan_id")
                initial_state["result_artifact_ids"] = parent_state.get("result_artifact_ids", [])
        
        # 运行Graph
        config = {"configurable": {"thread_id": thread_id}}
        final_state = await self.graph.ainvoke(initial_state, config)
        
        # 保存响应
        user_msg["graph_response"] = final_state.get("graph_response", "")
        conversation["active_branch"] = msg_id
        
        return user_msg
    
    def get_conversation_history(
        self,
        conv_id: str,
        branch_path: Optional[List[str]] = None
    ) -> List[UserMessage]:
        """获取对话历史（可指定分支路径）"""
        conversation = self.conversations[conv_id]
        
        if branch_path:
            # 返回指定路径的消息
            return [conversation["messages"][msg_id] for msg_id in branch_path
                   if msg_id in conversation["messages"]]
        else:
            # 返回当前活跃分支的消息
            return self._get_active_branch(conversation)
    
    def _get_active_branch(self, conversation: ConversationTree) -> List[UserMessage]:
        """获取当前活跃分支的完整路径"""
        if not conversation["active_branch"]:
            return []
        
        path = []
        current = conversation["messages"][conversation["active_branch"]]
        
        # 向上追溯到根
        while current:
            path.insert(0, current)
            if current["parent_id"]:
                current = conversation["messages"].get(current["parent_id"])
            else:
                break
        
        return path
```

---

## 🚀 简化的Controller（聚焦权限处理）

```python
# core/controller.py
class ExecutionController:
    """执行控制器（简化版）"""
    
    def __init__(self, graph):
        self.graph = graph
        self.conversation_manager = ConversationManager(graph)
    
    async def handle_user_message(
        self,
        conv_id: str,
        user_content: str,
        parent_msg_id: Optional[str] = None
    ) -> Dict:
        """处理用户消息（主入口）"""
        # 委托给对话管理器
        user_msg = await self.conversation_manager.send_message(
            conv_id, user_content, parent_msg_id
        )
        
        return {
            "message_id": user_msg["message_id"],
            "response": user_msg["graph_response"],
            "thread_id": user_msg["thread_id"]
        }
    
    async def handle_permission_request(
        self,
        thread_id: str,
        approved: bool,
        reason: Optional[str] = None
    ):
        """处理权限请求（中断恢复）"""
        # 获取当前状态
        config = {"configurable": {"thread_id": thread_id}}
        state = await self.graph.aget_state(config)
        
        pending = state.values.get("pending_tool_confirmation")
        if not pending:
            raise ValueError("No pending confirmation")
        
        # 模拟工具执行或创建拒绝结果
        if approved:
            # 获取对应Agent的toolkit
            from_agent = pending["from_agent"]
            # 这里需要访问agent registry获取toolkit
            # 简化：直接创建成功结果
            result = ToolResult(
                success=True,
                data={"message": "Tool execution approved and completed"}
            )
        else:
            result = ToolResult(
                success=False,
                error=f"Permission denied: {reason or 'User rejected'}"
            )
        
        # 更新状态，准备恢复
        update = {
            "pending_tool_confirmation": {
                **pending,
                "result": (pending["tool_name"], result)
            },
            "next_agent": pending["from_agent"]  # 返回原Agent
        }
        
        # 恢复执行
        await self.graph.aupdate_state(config, update)
        final_state = await self.graph.ainvoke(None, config)
        
        return final_state.get("graph_response")
```

---

## 🎯 实施优先级

### Phase 1: 核心流程 ✅
- [x] BaseAgent支持中断恢复
- [ ] ExtendableGraph基础实现
- [ ] 单Agent流程测试

### Phase 2: 多Agent协作 🔧
- [ ] 注册所有现有Agent
- [ ] 测试Lead → SubAgent → Lead流程
- [ ] 权限中断与恢复

### Phase 3: 对话管理 📝
- [ ] ConversationManager实现
- [ ] 分支对话支持
- [ ] 历史回溯功能

### Phase 4: 优化 🚀
- [ ] Context压缩
- [ ] 流式输出
- [ ] 性能优化

---

## 💡 关键设计决策

### 1. 可扩展性
- 使用`Dict[str, NodeMemory]`而非硬编码的agent memories
- 动态Agent注册机制
- 通用的节点函数生成器
- 统一的路由规则

### 2. 分支对话
- 每个用户消息独立thread_id
- parent_thread_id追踪分支关系
- 状态复制机制保证分支独立性

### 3. 简化Controller
- 移除复杂的生命周期管理
- 聚焦于权限处理和对话管理
- 利用LangGraph原生能力

### 4. 三层历史分离
- Layer 1: ConversationManager管理
- Layer 2: AgentState自动保存
- Layer 3: NodeMemory独立存储

---

## ⚠️ 注意事项

1. **Agent注册顺序**：先注册被依赖的Agent（如SubAgents），最后注册Lead Agent
2. **Memory初始化**：首次访问agent_memories时需要初始化
3. **分支状态隔离**：创建分支时要复制必要状态，避免相互影响
4. **权限处理一致性**：所有Agent使用相同的权限中断机制
5. **Thread ID管理**：确保每个用户消息对应唯一的thread_id

---

## 🎯 MVP核心目标

1. **可扩展架构**：支持动态添加Agent和工具
2. **分支对话**：支持消息编辑和多分支管理
3. **权限控制**：统一的工具权限中断机制
4. **基本流程**：User → Graph → Agent → Tool → User