# Post-Migration Remaining Items

> LangGraph 移除后（commit bc7dbdc）系统性梳理：设计文档中设计了但未实装的功能，以及前端配合调整。

来源文档：`docs/_archive/remove-langgraph-unified-persistence.md`

---

## 1. 设计文档中未实装的项

### 1.1 Compaction Phase 1 — 跨轮 compaction 触发

| | |
|---|---|
| 设计位置 | 设计文档 行406-408 |
| 当前状态 | **未实装** |
| 分析 | `context_manager.py` 有轮内 truncation（`compress_messages(preserve_recent=5)`），但无跨轮 compaction 触发。需要在 `controller.stream_execute()` 准备 history 时检查 `last_input_tokens` 阈值，超过红线时先 compact 再执行 |
| 依赖 | `last_input_tokens` 持久化（见 1.3） |

### 1.2 Compaction Phase 2 — LLM 摘要生成

| | |
|---|---|
| 设计位置 | 设计文档 行409 |
| 当前状态 | **未实装** |
| 分析 | 异步调用配置模型生成逐对摘要，写入 `content_summary` / `response_summary`。DB 列已存在（`models.py:186-187`），但无写入逻辑 |
| 依赖 | Phase 1 compaction 触发机制 |

### 1.3 `last_input_tokens` 持久化

| | |
|---|---|
| 设计位置 | 设计文档 行404, 413 |
| 当前状态 | **未实装** |
| 分析 | `engine.py` 有 `token_usage["prompt_tokens"]`（每轮 LLM 调用返回），需在 `controller.py` post-processing 中写入 `Message.metadata.last_input_tokens`。当前 metadata 只写了 `always_allowed_tools` |

### 1.4 `execution_metrics` 持久化到 Message.metadata

| | |
|---|---|
| 设计位置 | 设计文档 |
| 当前状态 | **未实装** |
| 分析 | 当前 `execution_metrics` 仅通过 SSE `complete` 事件发送，不写 DB。需在 `controller.py` post-processing 中补写到 `Message.metadata.execution_metrics`。这样前端可以在历史消息中展示执行指标 |

### 1.5 `content_summary` / `response_summary` API 暴露

| | |
|---|---|
| 设计位置 | — |
| 当前状态 | **未实装** |
| 分析 | DB 列已存在，但 `MessageResponse` schema（`api/schemas/chat.py`）无 summary 字段。需扩展 schema 并在路由中序列化 |

---

## 2. 前端配合调整（功能增量）

### 2.1 单轮多工具调用展示

| | |
|---|---|
| 前端现状 | `AgentSegmentBlock` 已支持 `toolCalls[]` + `.map()` 渲染 |
| 需要的改动 | P0 中 `tool_name → tool` 字段修复后即可正常工作。应端到端验证 |

### 2.2 压缩/非压缩消息展示

| | |
|---|---|
| 前端现状 | 无 |
| 需要的改动 | 需后端先暴露 `content_summary` / `response_summary`（见 1.5），前端根据字段切换显示（原文/摘要 toggle） |

### 2.3 SSE 推流时用户输入

| | |
|---|---|
| 前端现状 | 推流状态下输入框禁用 |
| 后端现状 | `TaskManager.inject_message()` / `drain_messages()` 已实装 |
| 需要的改动 | 缺 HTTP 端点（POST /chat/{conv_id}/inject）+ 前端 UI（推流状态下输入框启用） |

### 2.4 对话历史加载执行流程

| | |
|---|---|
| 前端现状 | 无 |
| 后端现状 | `MessageEvent` 表有完整事件流 |
| 需要的改动 | 需 API 端点（GET /chat/{conv_id}/messages/{msg_id}/events）+ 前端展开组件，点击历史消息可回放执行过程 |

### 2.5 Context usage 展示

| | |
|---|---|
| 前端现状 | 无 |
| 需要的改动 | 需 `last_input_tokens` 持久化后（见 1.3），前端读取并计算 `usage_percent = tokens / model_limit`。可在消息气泡或状态栏显示 context 使用率 |

---

## 3. 旧数据清理

| 项目 | 状态 |
|------|------|
| `data/langgraph.db` | **不存在**（已清理） |
| `data/test_langgraph.db` | **不存在**（已清理） |
| `data/test_stream_langgraph.db` | **不存在**（已清理） |
| `requirements.txt` | **已清理**（无 langgraph/langchain 依赖） |
| `tests/manual/core_graph.py` | **已删除**（本次清理） |
| `tests/manual/core_graph_stream.py` | **已删除**（本次清理） |

---

## 4. 实施优先级建议

| 优先级 | 项目 | 理由 |
|--------|------|------|
| P3 | 1.3 `last_input_tokens` 持久化 | 简单且为 compaction 前置 |
| P3 | 1.4 `execution_metrics` 持久化 | 简单，提升可观测性 |
| P4 | 1.1 Compaction Phase 1 | 长对话体验关键 |
| P5 | 1.2 Compaction Phase 2 | 依赖 Phase 1 |
| P5 | 1.5 API summary 暴露 + 2.2 前端 | 依赖 Phase 2 |
| P6 | 2.3 推流中用户输入 | 端点 + UI 均需新建 |
| P6 | 2.4 历史执行流程回放 | 端点 + UI 均需新建 |
| P6 | 2.5 Context usage 展示 | 依赖 1.3 |
