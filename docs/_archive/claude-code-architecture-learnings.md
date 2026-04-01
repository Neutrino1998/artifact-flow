# Claude Code Architecture Learnings

> Source: `custom-claude-code/build-output/` (TypeScript transpiled, v2.1.87) + `custom-claude-code/claw-code/rust/` (Rust port)
> Date: 2026-04-01

ArtifactFlow 可借鉴的设计模式，按优先级排序。

---

## 1. Tool 并发分批执行

ArtifactFlow 当前 engine 串行执行所有 tool calls。Claude Code 按并发安全性分批。

**机制：** 每个 Tool 定义上有三个标记，orchestrator 据此自动分批：
- `isConcurrencySafe(input)` — 能否与其他工具并行
- `isReadOnly(input)` — 是否只读
- `isDestructive(input)` — 是否破坏性操作

```
read-only tools   → Promise.all() 并发
non-read-only     → 逐个串行
```

**Ref:**
- `build-output/services/tools/toolOrchestration.ts` — `runTools()` 分批逻辑
- `build-output/services/tools/toolExecution.ts` — 单个 tool 执行 `runToolUse()`
- `build-output/Tool.ts` — Tool 类型定义，含 `isConcurrencySafe` / `isReadOnly` / `isDestructive`

**ArtifactFlow 应用：** `search` + `crawl` 天然可并行，engine loop 里按标记分批即可。

---

## 2. Context Compaction（渐进式上下文压缩）

长对话不做压缩会爆 context。Claude Code 的方案是 "保留最近 N 条 + 结构化摘要旧消息"。

**触发条件：** 消息数超阈值 AND 估算 token 超 `max_estimated_tokens`

**压缩流程：**
1. 保留最近 N 条消息（默认 4 条）原样不动
2. 移除更早的消息，生成结构化摘要：
   - Scope: 各 role 消息计数
   - Tools mentioned: 使用过的工具名集合
   - Recent user requests: 最近 3 条（截断 160 字符）
   - Pending work: 包含 "todo"/"next"/"pending"/"follow up" 的消息
   - Key files: 被引用的文件路径
   - Key timeline: 按时间线的全部摘要
3. 注入 continuation message 告知模型上下文已被压缩

**Token 估算：** 简单启发式 `len(text) / 4 + 1`，按 block 类型分别计算。

**Ref:**
- `claw-code/rust/crates/runtime/src/compact.rs` — 完整实现（485 行）
  - `should_compact()` 判断逻辑
  - `summarize_messages()` 摘要生成
  - `CompactionConfig` 配置结构

**ArtifactFlow 应用：** `conversation_manager` 目前全量发送历史。对多轮 search+crawl 任务，compaction 能避免 token 爆掉。

---

## 3. Tool Schema 缓存

每轮重新序列化 tool definitions 是浪费，且会破坏 prompt cache。

**机制：**
- `toolToAPISchema()` 按 session 缓存序列化结果
- Cache key 包含 `inputJSONSchema`（MCP 工具可能动态变）
- 防止 feature flag 中途翻转导致 schema 变化

**Ref:**
- `build-output/utils/api.ts` — `toolToAPISchema()` 缓存逻辑
- `build-output/services/api/claude.ts:1700+` — API request 构建，引用缓存后的 schema

**ArtifactFlow 应用：** 我们每轮调 API 前都重新组装 tool definitions。加 session 级缓存即可。

---

## 4. 结果溢出到磁盘

大 tool 结果直接塞 message 会浪费 context。

**机制：**
- 每个 Tool 定义 `maxResultSizeChars`
- 超出 → 持久化到磁盘文件，模型收到 preview + 文件路径
- `toolResultStorage.ts` 管理溢出文件的写入和读取

**Ref:**
- `build-output/Tool.ts` — `maxResultSizeChars` 字段
- `build-output/utils/toolResultStorage.ts` — 溢出存储逻辑
- `build-output/services/tools/toolExecution.ts` — 执行后检查结果大小

**ArtifactFlow 应用：** search/crawl 结果可能很长。可以设阈值，超出部分存磁盘或 artifact，给模型 preview + 引用。

---

## 5. 两层 Tool 过滤（静态移除 + 动态权限）

模型不该看到不可用的工具（省 token + 避免无效调用）。

**静态层（注册时）：**
```
getAllBaseTools() → deny_rules 过滤 → assembleToolPool()
```
被 deny 的工具从 schema 中完全移除，模型看不到。

**动态层（调用时）：**
```
Input Validation → PreToolUse Hooks (并行) → Classifier → Permission Decision
```
5 层权限源优先级：Rules > Hooks > Classifiers > Dialogs > Default

**Ref:**
- `build-output/tools.ts` — `getAllBaseTools()`, `getTools()`, `assembleToolPool()`, `filterToolsByDenyRules()`
- `build-output/utils/permissions/permissions.ts` — `canUseTool()` 动态权限检查
- `build-output/services/tools/toolExecution.ts:455-466` — `checkPermissionsAndCallTool()` 完整流程

**ArtifactFlow 应用：** 当前是 agent frontmatter `allowed_tools` 做 flat lookup。可以分成 "schema 可见性" 和 "运行时权限" 两层。

---

## 6. Fork Agent + Prompt Cache 共享

子 agent 复制父的完整 system prompt（bytes-exact），确保 prompt cache hit。

**Fork 定义：**
```typescript
FORK_AGENT = {
  agentType: 'fork',
  tools: ['*'],            // 继承全部工具
  maxTurns: 200,
  model: 'inherit',        // 继承父 model
  permissionMode: 'bubble' // 权限向上冒泡
}
```

**关键设计：**
- `buildForkedMessages()` — 为所有 tool_uses 创建相同的 placeholder results，最大化 cache hit
- `buildChildMessage()` — 注入 fork 指令（禁止再 fork、直接用工具、只报告结果）
- 可选 git worktree 隔离，fork 改代码不影响主分支

**Ref:**
- `build-output/tools/AgentTool/forkSubagent.ts:32-171` — fork 完整实现
- `build-output/tools/AgentTool/loadAgentsDir.js` — agent 定义加载

**ArtifactFlow 应用：** 我们的 Lead→Search/Crawl 切换是硬编码。Fork 模式 + cache 共享在多 subagent 场景下能省大量 token。设计参考。

---

## 7. Hook 系统 — 外部 Shell 命令拦截

不是代码插件，是 settings.json 配置的 shell 命令。

```json
{
  "hooks": {
    "PreToolUse": [{ "command": "my-validator.sh" }],
    "PostToolUse": [{ "command": "my-logger.sh" }]
  }
}
```

Hook 返回值能力：
- `allow` / `deny` / `ask` — 权限决策
- 修改 tool input（passthrough）
- 注入额外 context
- 停止执行 + 返回原因

Bash safety classifier 和 hooks 并行执行，不阻塞。

**Ref:**
- `build-output/services/tools/toolHooks.ts` — hook 执行逻辑
- `claw-code/rust/crates/runtime/src/config.rs` — hook 配置加载与合并
- `claw-code/src/reference_data/subsystems/hooks.json` — 104 个 hook 模块清单

**ArtifactFlow 应用：** 我们的 permission interrupt 只有 allow/deny。Hook 的 "修改 input" 和 "注入 context" 更灵活，后期可考虑。

---

## 8. Deferred Tool Search（按需加载工具 Schema）

工具太多放不进 context 时的优雅方案。

**机制：**
- 工具标记 `shouldDefer: true` → API schema 里加 `defer_loading: true`
- 模型只看到工具名（无参数 schema），调用前必须先调 `ToolSearch` 获取完整定义
- 直接调用未发现的 deferred tool → validation error + hint

**Ref:**
- `build-output/tools/ToolSearchTool/` — ToolSearch 工具实现
- `build-output/Tool.ts` — `shouldDefer` / `alwaysLoad` 标记
- `build-output/utils/api.ts` — schema 构建时处理 `defer_loading`

**ArtifactFlow 应用：** 当前工具数量少，暂不需要。工具数量增长后可借鉴。

---

## 9. Configuration 三层合并 + CLAUDE.md 向上遍历

**Settings 三层：**
```
~/.claude/settings.json           # User scope
./.claude/settings.json           # Project scope (committed)
./.claude/settings.local.json     # Local scope (gitignored)
```
Deep merge：后者覆盖前者，对象字段递归合并。

**指令文件发现：** 从工作目录向上遍历，依次查找：
- `CLAUDE.md`
- `CLAUDE.local.md`
- `.claude/CLAUDE.md`

每文件 4000 字符上限，总量 12000 字符上限。

**Ref:**
- `claw-code/rust/crates/runtime/src/config.rs` — 配置发现、加载、deep merge（796 行）
- `claw-code/rust/crates/runtime/src/prompt.rs:189-209` — CLAUDE.md 向上遍历发现

---

## 10. 主循环结构（QueryEngine）

对比 ArtifactFlow 的 pi-style `while not completed` loop：

```
QueryEngine.submitMessage()        # async generator, yields streaming events
  → processUserInput()             # 解析 prompt、展开粘贴内容
  → query()                        # 核心循环
    → normalizeMessagesForAPI()    # 消息格式转换
    → claude API call (streaming)  # 流式调用
    → runTools()                   # 工具执行（分批）
    → loop until stop_reason       # end_turn / max_turns / budget / abort
```

**停止条件：**
- `stop_reason === 'end_turn'` — 模型主动结束
- `stop_reason === 'tool_use'` — 需要执行工具（继续循环）
- Max turns 达到（默认 8）
- Token budget 超限
- Abort signal

**Ref:**
- `build-output/QueryEngine.ts:209-639` — 主 engine，消息流
- `build-output/query.ts:219-250` — 核心循环，turn 管理
- `build-output/services/tools/StreamingToolExecutor.ts` — 流式工具执行

**ArtifactFlow 对比：** 我们的 pi-style loop 更简洁（flat while loop），Claude Code 用 async generator yield 事件流。两者本质相同，只是 streaming 粒度不同。

---

## 优先级总结

| 模式 | 难度 | 收益 | 建议 |
|------|------|------|------|
| Tool 并发分批 | 中 | 高 | 优先实现 |
| Context compaction | 中 | 高 | 优先实现 |
| Schema 缓存 | 低 | 中 | 随手做 |
| 结果溢出到磁盘 | 低 | 中 | 随手做 |
| 两层 tool 过滤 | 低 | 低 | 可选 |
| Fork agent + cache 共享 | 高 | 高 | 设计参考 |
| Deferred tool search | 高 | 低 | 暂不需要 |
| Hook 系统 | 高 | 中 | 后期 |
