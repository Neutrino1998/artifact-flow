# Tool Result → Artifact 挂载设计

> Inspired by: Claude Code 的 "tool result overflow to disk" 模式
> Ref: `custom-claude-code/build-output/Tool.ts` (`maxResultSizeChars`), `build-output/utils/toolResultStorage.ts`
> Date: 2026-04-01

## 问题

数据获取类工具（web_search, web_fetch, crawl）的结果直接塞进 `ToolResult.data` → 进入 LLM context。结果越大，context 浪费越严重，且 `ContextManager` 的硬截断会丢失信息。

**Claude Code 的方案：** 每个 tool 定义 `maxResultSizeChars`，超出后结果持久化到磁盘，模型只收到 preview + 文件路径。

**ArtifactFlow 的优势：** 我们已经有 artifact 系统 + `read_artifact` 工具 + inventory preview。不需要发明新的存储层 — 直接挂载到 artifact 即可。

## 现有接入点

| 组件 | 现状 | 位置 |
|------|------|------|
| `ToolResult.data` | 全量文本，无大小限制 | `src/tools/base.py:28-33` |
| `ArtifactManager.create_artifact()` | 支持 `source` 参数（`"agent"` / `"user_upload"`） | `src/tools/builtin/artifact_ops.py:429` |
| Inventory preview | 已有截断（`INVENTORY_PREVIEW_LENGTH`，默认 2k chars） | `src/core/context_manager.py:143` |
| `read_artifact` 工具 | 模型可按需读全文 | `src/tools/builtin/artifact_ops.py` ReadArtifactTool |
| `build_snapshot()` | SSE 实时推送 artifact 快照 | `src/tools/builtin/artifact_ops.py:599` |

## 设计方案

### 核心思路

工具执行后，如果结果超过阈值，自动创建一个 `source="tool"` 的 artifact 存放全量数据。`ToolResult.data` 只返回摘要 + artifact 引用。模型需要细节时调 `read_artifact`。

### 数据流

```
web_fetch(url) 执行完毕
  │
  ├─ len(result) <= threshold?
  │   └─ Yes → 正常返回 ToolResult(data=result)
  │
  └─ No → ArtifactManager.create_artifact(
  │         id="fetch_{hash}",
  │         content_type="text/markdown",
  │         title="Fetched: {url}",
  │         content=full_result,
  │         source="tool"
  │       )
  │
  └─ 返回 ToolResult(
         data="<tool_artifact id='fetch_{hash}' chars={len}>{preview}</tool_artifact>",
         metadata={"artifact_snapshot": snapshot}
     )
```

### 模型看到的

**挂载前（现状）：**
```xml
<tool_result name="web_fetch" success="true">
<data>
  <page type="html" words="15234">
    ... 20000 chars 全文 ...
  </page>
</data>
</tool_result>
```

**挂载后：**
```xml
<tool_result name="web_fetch" success="true">
<data>
  <tool_artifact id="fetch_a3b2c1" type="text/markdown" chars="48210">
    Full content stored as artifact. Use read_artifact to access.
  </tool_artifact>
  <summary>
    Page: "Example Article Title" (15234 words)
    Key sections: Introduction, Methods, Results, Discussion
    ... 前 500 chars preview ...
  </summary>
</data>
</tool_result>
```

模型在下一轮的 system prompt 里通过 `<artifacts_inventory>` 也能看到这个 artifact 的 preview（已有机制，零改动）。

### 需要的改动

**1. `source="tool"` 新类型**

`ArtifactMemory` 和 DB 层已支持任意 source 字符串，无需 schema 变更。仅需在 inventory 展示时区分：

```python
# context_manager.py — _build_artifacts_inventory()
# tool artifacts 用更紧凑的格式，不占太多 system prompt 预算
if source == "tool":
    lines.append(f'<content_preview length="500">{preview[:500]}</content_preview>')
```

**2. BaseTool 新增挂载能力**

```python
# tools/base.py

@dataclass
class ToolResult:
    success: bool
    data: str = ""
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

# 不改 ToolResult — 挂载逻辑放在工具内部，通过构造函数注入 ArtifactManager
```

工具通过构造函数拿到 `ArtifactManager` 引用（已有先例：artifact_ops 工具都这样做）。挂载是工具自己的决策，不是框架强制的。

**3. 工具侧实现（以 web_fetch 为例）**

```python
MOUNT_THRESHOLD = 8000  # chars

async def execute(self, url: str, **params) -> ToolResult:
    result = await self._fetch(url)

    if len(result.content) <= MOUNT_THRESHOLD:
        return ToolResult(success=True, data=self._format_xml(result))

    # 挂载到 artifact
    artifact_id = f"fetch_{hash(url)[:8]}"
    await self._artifact_manager.create_artifact(
        session_id=self._session_id,
        artifact_id=artifact_id,
        content_type="text/markdown",
        title=f"Fetched: {result.title or url}",
        content=result.content,
        source="tool",
    )

    summary = self._build_summary(result, preview_chars=500)
    snapshot = self._artifact_manager.build_snapshot(self._session_id, artifact_id)

    return ToolResult(
        success=True,
        data=summary,
        metadata={"artifact_snapshot": snapshot},
    )
```

**4. Engine 层 — 零改动**

engine 已经：
- 把 `ToolResult.data` 格式化进 context（`xml_formatter.format_result()`）
- 把 `ToolResult.metadata` 推送到 SSE（`artifact_snapshot`）
- 在 loop 结束 `flush_all()` 持久化所有 dirty artifact

tool artifact 自然走相同路径，不需要特殊处理。

### 阈值策略

| 工具 | 建议阈值 | 理由 |
|------|---------|------|
| `web_fetch` | 8,000 chars | 一般文章 3-5k，超过说明是长文档 |
| `web_search` | 不挂载 | 结果本身是摘要列表，通常 <2k |
| `crawl` (未来) | 4,000 chars/page | 多页结果聚合，每页单独 artifact |

阈值可以是工具级常量，不需要全局配置。每个工具最了解自己的数据特征。

### 前端影响

- `source="tool"` 的 artifact 通过现有 SSE `artifact_snapshot` 推送，前端已有渲染逻辑
- 可选：在 artifact 列表 UI 中用不同 badge 区分 `agent` / `user_upload` / `tool` 来源
- 无阻塞性改动

### 收益

1. **Context 节约**：20k 全文 → 500 char preview，省 ~97% context
2. **信息无损**：全文在 artifact 里，`read_artifact` 随时可读
3. **前端可见**：用户在 artifact 面板直接看到抓取结果，不用翻聊天记录
4. **版本追踪**：同一 URL 多次抓取 → artifact 版本历史（已有机制）
5. **零框架改动**：engine、context_manager、SSE 都不需要改

### 不做什么

- 不加全局 `maxResultSizeChars` 配置 — 各工具自己决定
- 不改 `ToolResult` 数据结构 — 挂载是工具内部行为
- 不做自动摘要（LLM summarize） — preview 截断足够，避免额外 API 调用
- 不对 `web_search` 挂载 — 搜索结果本身就是摘要
