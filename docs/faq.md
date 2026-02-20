# FAQ

常见问题与排查指南。

## 环境与安装

### 为什么需要 Python 3.11+？

LangGraph 的 `interrupt()` 功能依赖 Python 3.11 引入的异步特性。低版本 Python 会导致：

- `interrupt()` 无法正确暂停执行
- 权限确认流程失效
- 状态恢复异常

**检查版本：**

```bash
python --version
# 需要 Python 3.11.0 或更高
```

---

### crawl4ai-setup 失败

常见错误：

```
Error: Playwright browsers not installed
```

**解决方案：**

```bash
# 安装 playwright 浏览器
playwright install chromium

# 重新运行 setup
crawl4ai-setup
```

如果仍然失败，尝试：

```bash
# 完整安装
pip uninstall crawl4ai
pip install crawl4ai[all]
crawl4ai-setup
```

---

### aiosqlite 版本冲突

错误信息：

```
ERROR: pip's dependency resolver does not currently take into account all the packages...
```

**原因：** LangGraph 和其他包对 aiosqlite 版本要求不一致。

**解决方案：**

```bash
# 使用 requirements.txt 中锁定的版本
pip install -r requirements.txt --force-reinstall
```

---

## 认证问题

### 服务启动报 `ARTIFACTFLOW_JWT_SECRET` 未设置

服务启动时会检查 JWT 密钥，未设置则拒绝启动：

```
RuntimeError: ARTIFACTFLOW_JWT_SECRET environment variable is not set.
```

**解决方案：**

```bash
# 生成并设置 JWT 密钥
export ARTIFACTFLOW_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# 或写入 .env 文件（推荐）
echo "ARTIFACTFLOW_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env
```

---

### 如何创建第一个管理员账号？

```bash
python scripts/create_admin.py admin
# 按提示输入密码

# 或直接指定密码
python scripts/create_admin.py admin --password your_password
```

该脚本会：
1. 创建 admin 角色的用户
2. 将所有 `user_id` 为空的历史对话归属到该用户（可用 `--no-claim` 跳过）

---

### CLI 报 401 / "Not authenticated"

CLI 需要先登录获取 token：

```bash
python run_cli.py login
# 输入用户名和密码

# 验证登录状态
python run_cli.py status
```

如果 token 过期，重新执行 `login` 即可。

---

### 前端跳转到登录页 / 频繁登出

可能原因：

1. **Token 过期**：默认有效期 7 天，重新登录即可
2. **用户被禁用**：管理员通过 `PUT /auth/users/{id}` 禁用了账号（`is_active=false`），联系管理员
3. **JWT 密钥变更**：服务端重启后使用了不同的 `ARTIFACTFLOW_JWT_SECRET`，所有旧 token 失效

---

### SSE 连接返回 401

SSE 端点同样需要认证。确保前端使用 `fetch`（而非 `EventSource`）连接 SSE，以便携带 `Authorization` header。ArtifactFlow 前端已正确处理（`frontend/src/lib/sse.ts`）。

---

## 运行问题

### API 服务启动失败

**检查端口占用：**

```bash
lsof -i :8000
# 如果有进程占用，kill 或换端口
python run_server.py --port 8001
```

**检查数据库目录：**

```bash
# 确保 data 目录存在且可写
mkdir -p data
chmod 755 data
```

---

### SSE 连接立即断开

可能原因：

1. **thread_id 不存在**：检查 POST /chat 返回的 thread_id
2. **TTL 超时**：POST 后超过 30 秒未连接 SSE
3. **反向代理缓冲**：Nginx 等代理可能缓冲 SSE

**Nginx 配置：**

```nginx
location /api/v1/stream/ {
    proxy_pass http://backend;
    proxy_buffering off;
    proxy_cache off;
    proxy_set_header Connection '';
    proxy_http_version 1.1;
    chunked_transfer_encoding off;
}
```

---

### Agent 执行卡住

**启用调试日志：**

```python
from utils.logger import set_global_debug
set_global_debug(True)
```

**检查 LLM API：**

```bash
# 测试 API 连接
curl -X POST https://your-llm-api/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "your-model", "messages": [{"role": "user", "content": "test"}]}'
```

**检查环境变量：**

```bash
# 确保 API Key 已设置
echo $OPENAI_API_KEY
echo $DASHSCOPE_API_KEY
```

---

### 权限中断后无法恢复

**检查 Checkpointer 状态：**

```bash
# 查看 LangGraph 数据库
sqlite3 data/langgraph.db ".tables"
sqlite3 data/langgraph.db "SELECT thread_id FROM checkpoints LIMIT 5"
```

**确保 thread_id 一致：**

```python
# 通过 API 恢复执行
# POST /api/v1/chat/{conversation_id}/resume
{
    "thread_id": "thd-original-thread-id",  # 使用原始 thread_id
    "message_id": "msg-original-message-id",
    "approved": True
}
```

或通过 Controller：

```python
async for event in controller.stream_execute(
    thread_id=original_thread_id,
    conversation_id=conversation_id,
    message_id=message_id,
    resume_data={"type": "permission", "approved": True}
):
    # 处理事件
    pass
```

---

## 工具问题

### web_search 返回空结果

**检查搜索 API 配置：**

搜索后端使用博查 AI（Bocha API），确保 `.env` 中配置了 `BOCHA_API_KEY`：

```bash
# 检查环境变量
echo $BOCHA_API_KEY

# 检查日志
tail -f logs/artifactflow.log | grep "web_search"
```

---

### web_fetch 抓取失败

**调整抓取参数：**

```python
# web_fetch 工具支持的参数
result = await tool.execute(
    url_list=["https://example.com"],      # URL 列表（必填）
    max_content_length=10000,              # 单页最大字符数（默认 10000）
    max_concurrent=3                       # 最大并发浏览器数（默认 3，上限 5）
)
```

**检查目标网站：**

- 是否需要 JavaScript 渲染
- 是否有反爬虫机制
- 是否需要代理

---

### Artifact 更新版本冲突

错误信息：

```
VersionConflictError: Version mismatch for artifact 'xxx': expected lock_version 2, current is 3
```

**原因：** 多个 Agent 同时更新同一个 Artifact。

**解决方案：** Agent 应该：

1. 重新读取最新版本
2. 合并变更
3. 重试更新

这是正常的并发控制机制，不是 bug。

---

## 性能问题

### LLM 响应慢

**使用流式输出：** 确保前端使用 SSE 接收 `llm_chunk` 事件，而不是等待完整响应。

**调整模型：**

```python
# 对于简单任务，使用更快的模型
AgentConfig(
    model="qwen-turbo",  # 比 thinking 模型快
    # ...
)
```

---

### 内存使用过高

**检查 StreamManager 队列：**

```python
# 获取活跃流数量
print(f"Active streams: {stream_manager.active_stream_count}")

# 检查特定流状态
status = stream_manager.get_stream_status(thread_id)
print(f"Stream status: {status}")  # pending | streaming | closed | None
```

**调整 TTL：**

```python
# 减少未消费队列的存活时间
stream_manager = StreamManager(ttl_seconds=15)
```

---

### 数据库变慢

**清理历史数据：**

```sql
-- 删除 30 天前的对话
DELETE FROM messages WHERE created_at < datetime('now', '-30 days');
DELETE FROM conversations WHERE updated_at < datetime('now', '-30 days');

-- 清理 Artifact 历史版本（保留每个 artifact 最近 10 个版本）
DELETE FROM artifact_versions
WHERE id NOT IN (
    SELECT id FROM (
        SELECT id, ROW_NUMBER() OVER (
            PARTITION BY artifact_id, session_id
            ORDER BY version DESC
        ) as rn
        FROM artifact_versions
    ) WHERE rn <= 10
);

-- 重建索引
VACUUM;
```

---

## 开发问题

### 如何查看完整的 Agent 提示词？

```python
from agents.lead_agent import LeadAgent

agent = LeadAgent()

# build_system_prompt 只返回角色定义部分
base_prompt = agent.build_system_prompt(context=None)
print("=== Base Prompt ===")
print(base_prompt)

# build_complete_system_prompt 返回包含工具说明的完整提示词
# 需要先绑定 toolkit
if agent.toolkit:
    complete_prompt = agent.build_complete_system_prompt(context=None)
    print("=== Complete Prompt ===")
    print(complete_prompt)
```

---

### 如何调试工具执行？

```python
# 直接测试工具
from tools.implementations.web_search import WebSearchTool

tool = WebSearchTool()
result = await tool(query="test query")  # 使用 __call__，会自动填充默认参数
print(result)
```

---

### 如何查看 Graph 状态？

```python
# 获取当前状态快照
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

checkpointer = AsyncSqliteSaver.from_conn_string("data/langgraph.db")
state = await checkpointer.aget({"configurable": {"thread_id": "your-thread-id"}})
print(state)
```

---

## 获取帮助

如果以上都无法解决问题：

1. **检查日志**：`tail -f logs/artifactflow.log`
2. **启用调试模式**：`set_global_debug(True)`
3. **提交 Issue**：附上错误日志和复现步骤
