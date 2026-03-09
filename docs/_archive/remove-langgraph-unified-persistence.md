# 去除 LangGraph/LangChain 依赖 + 统一持久化设计

> 状态：设计阶段，待持久化改造（P5/P6）完成后实施

---

## 背景

### 当前依赖程度

LangGraph/LangChain 在项目中的使用范围很小：

| 依赖 | 文件 | 用途 |
|------|------|------|
| `StateGraph`, `END` | `core/graph.py` | 图定义与路由 |
| `interrupt`, `StreamWriter` | `core/graph.py` | 权限确认中断 + 流式事件 |
| `Command` | `core/controller.py` | 恢复中断执行 |
| `AsyncSqliteSaver` | `core/graph.py` | Checkpoint 持久化 |
| `AIMessage` 等 | `models/llm.py` | 消息类型（纯数据结构） |

实际 LLM 调用走 LiteLLM，状态管理用自定义 `AgentState`，事件系统用自定义 `StreamEvent`。LangGraph 本质上只提供了一层状态机壳子。

### 当前持久化问题：两套独立机制，各丢各的数据

| 层 | 存了什么 | 丢了什么 |
|---|---|---|
| **Messages 表** | 用户问题 + 最终 response 文本 | 中间执行过程全丢 |
| **LangGraph checkpoint** (304MB) | 每个节点的全量 state BLOB | 执行完就没用，无法查询 |
| **SSE 事件流** | 内存队列，30s TTL | 连接断了就没了 |
| **execution_metrics** | 内存中累积，只发前端一次 | 服务端不持久化 |

前端刷新页面只能看到一问一答，整个执行过程（agent 调用链、工具参数与结果、token 用量、耗时）全部丢失。

---

## 方案：统一事件模型

### 核心思想

**执行过程本身就是数据。** 把现在"即发即弃"的 SSE 事件流变成"写入即持久"的事件记录。一张 `MessageEvent` 表同时承担三个角色：

1. **历史记录** — 按 message_id 查所有事件，重建完整执行过程
2. **可观测性** — 按 event_type / agent / tool 聚合查询、统计分析
3. **interrupt/resume** — interrupt 就是事件流中的一个状态，不需要单独的 checkpoint 机制

### 数据模型

```
Message (现有，扩展 metadata)
  ├── content              用户输入
  ├── response             最终回复
  ├── metadata (JSON)      execution_metrics 汇总
  │
  └── MessageEvent (新表，append-only)
        ├── message_id     关联消息
        ├── sequence       顺序号
        ├── event_type     agent_start / llm_complete / tool_start / tool_complete / interrupt_pending / interrupt_resolved / ...
        ├── agent_name     产生事件的 agent
        ├── data (JSON)    工具参数与结果、token 用量、reasoning 等
        └── created_at     时间戳
```

### 执行流程

```
请求进入
  → 创建 Message 记录
  → 启动执行循环 (async coroutine)
      → while phase != COMPLETED:
          → 根据 phase 执行 agent / tool
          → 每个事件同时：
              ├── 推 SSE 队列（实时前端展示）
              └── 写 MessageEvent（持久化）
          → 遇到需要确认的工具：
              → 写 event_type=interrupt_pending（含序列化 state）
              → 返回前端等待确认
              → 用户确认后：读出 state，写 interrupt_resolved，继续循环
      → 执行完成
          → 汇总 metrics 写入 Message.metadata
```

### 替代 LangGraph 的实现

| LangGraph 功能 | 替代方案 |
|---|---|
| `StateGraph` + 路由 | `while phase != COMPLETED` + phase switch（`ExecutionPhase` enum 已存在） |
| `interrupt()` / `Command(resume=)` | `MessageEvent` 中 `interrupt_pending` 事件存 state，恢复时读出继续 |
| `StreamWriter` | 自定义 callback / async generator，复用现有 `StreamManager` |
| `AsyncSqliteSaver` (checkpoint) | 不需要通用 checkpoint，interrupt state 就是一条事件记录 |
| `langchain_core.messages` | 直接用 dict `{"role": ..., "content": ...}`，`llm.py` 已在做转换 |

### 收益

- **前端历史回看**：加载消息时附带完整事件链，展示工具调用、agent 协作过程
- **可观测性**：结构化事件日志，可查询、可统计（token 消耗、工具成功率、响应耗时）
- **checkpoint 透明**：从 304MB 黑盒 BLOB → 按需存一条事件记录
- **一套事务**：所有数据在同一个 DB、同一个 session，不存在跨库不一致
- **依赖极简**：技术栈收敛为 FastAPI + LiteLLM + SQLAlchemy

---

## 实施顺序

1. **先完成持久化改造（P5/P6）** — 稳定应用数据层
2. **实现 MessageEvent 表 + 写入逻辑** — 在现有 LangGraph 基础上先加事件持久化
3. **替换执行引擎** — 用自己的 async 循环替代 StateGraph，interrupt 改写为事件记录
4. **移除 LangGraph/LangChain 依赖** — 清理代码和 requirements
5. **前端适配** — 历史消息加载事件链，展示执行过程
