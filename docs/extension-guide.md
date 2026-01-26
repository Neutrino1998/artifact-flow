# 扩展指南

本指南介绍如何扩展 ArtifactFlow，包括添加新的 Agent 和 Tool。

## 添加新 Agent {#添加新-agent}

### 步骤概览

1. 创建 Agent 类（继承 `BaseAgent`）
2. 定义配置和所需工具
3. 实现系统提示词
4. 注册到 Graph

### 完整示例：CodeAgent

创建一个用于代码分析的 Agent：

```python
# src/agents/code_agent.py

from typing import Dict, Any, List, Optional
from agents.base import BaseAgent, AgentConfig

class CodeAgent(BaseAgent):
    """代码分析 Agent"""

    def __init__(self, config: Optional[AgentConfig] = None, toolkit=None):
        if not config:
            config = AgentConfig(
                name="code_agent",  # 命名规范：xxx_agent
                description="代码分析与审查",
                capabilities=[
                    "代码结构分析",
                    "代码质量评估",
                    "重构建议"
                ],
                required_tools=[
                    "read_file",      # 需要先创建这些工具
                    "analyze_code"
                ],
                model="qwen3-next-80b-instruct",
                temperature=0.3,  # 代码分析需要精确
                max_tool_rounds=5,
                streaming=True
            )
        super().__init__(config, toolkit)

    def build_system_prompt(self, context: Optional[Dict[str, Any]] = None) -> str:
        """
        构建系统提示词

        Args:
            context: 动态上下文（如 artifacts_inventory）
        """
        return """# 角色
你是一个专业的代码分析 Agent，负责分析代码结构、评估代码质量、提供重构建议。

# 能力
- 理解多种编程语言的代码结构
- 识别代码中的问题和改进点
- 提供具体的重构建议

# 工作流程
1. 接收代码分析指令
2. 使用工具读取和分析代码
3. 整理分析结果
4. 返回结构化的分析报告

# 输出要求
- 分析结果要具体、可操作
- 指出问题时给出改进建议
- 使用代码示例说明
"""
        # 注意：工具说明由 build_complete_system_prompt() 自动追加

    def format_final_response(self, content: str, tool_history: List[Dict]) -> str:
        """
        格式化最终响应

        Args:
            content: LLM 的最终回复
            tool_history: 工具调用历史
        """
        return f"""## 代码分析报告

{content}
"""
```

### 注册到 Graph

```python
# src/core/graph.py

from agents.code_agent import CodeAgent

async def create_multi_agent_graph(
    tool_permissions: Optional[Dict[str, ToolPermission]] = None,
    artifact_manager: Optional[ArtifactManager] = None,
    checkpointer: Optional[Any] = None,
    db_path: str = "data/langgraph.db"
):
    graph_builder = ExtendableGraph()

    # 创建 Agent 实例
    lead = LeadAgent()
    search = SearchAgent()
    crawl = CrawlAgent()
    code = CodeAgent()  # 新增

    # 创建工具包并绑定到 Agent
    # （toolkit 创建逻辑，见 create_multi_agent_graph 完整实现）

    # 注册子 Agent 到 Lead（使其出现在 Lead 的 system prompt 中）
    lead.register_subagent(search.config)
    lead.register_subagent(crawl.config)
    lead.register_subagent(code.config)  # 新增

    # 注册到 Graph（顺序：先 subagent，再 lead）
    graph_builder.register_agent(search)
    graph_builder.register_agent(crawl)
    graph_builder.register_agent(code)  # 新增
    graph_builder.register_agent(lead)

    # 设置入口点
    graph_builder.set_entry_point("lead_agent")

    # 编译
    return graph_builder.compile(checkpointer=checkpointer)
```

### Lead Agent 自动感知

通过 `lead.register_subagent(code.config)` 注册后，Lead Agent 的系统提示词会自动包含新 Agent：

```
# 可调用的 SubAgent

- **search_agent**: Web search and information retrieval specialist
  - Capabilities: Web search, Information retrieval
- **crawl_agent**: Web content extraction and cleaning specialist
  - Capabilities: Deep content extraction, Web scraping
- **code_agent**: 代码分析与审查
  - Capabilities: 代码结构分析, 代码质量评估, 重构建议
```

## 添加新工具 {#添加新工具}

### 步骤概览

1. 创建工具类（继承 `BaseTool`）
2. 定义参数和权限
3. 实现执行逻辑
4. 注册到 ToolRegistry

### 完整示例：ReadFileTool

```python
# src/tools/implementations/file_ops.py

from pathlib import Path
from typing import List
from tools.base import BaseTool, ToolParameter, ToolResult, ToolPermission

class ReadFileTool(BaseTool):
    """读取文件内容"""

    def __init__(self):
        super().__init__(
            name="read_file",
            description="读取指定路径的文件内容",
            permission=ToolPermission.CONFIRM  # 需要用户确认
        )

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="path",
                type="string",
                description="文件路径",
                required=True
            ),
            ToolParameter(
                name="encoding",
                type="string",
                description="文件编码",
                required=False,
                default="utf-8"
            ),
            ToolParameter(
                name="max_lines",
                type="integer",
                description="最大读取行数，0 表示不限制",
                required=False,
                default=0
            )
        ]

    async def execute(
        self,
        path: str,
        encoding: str = "utf-8",
        max_lines: int = 0
    ) -> ToolResult:
        try:
            file_path = Path(path)

            # 安全检查
            if not file_path.exists():
                return ToolResult(
                    success=False,
                    error=f"文件不存在: {path}"
                )

            if not file_path.is_file():
                return ToolResult(
                    success=False,
                    error=f"路径不是文件: {path}"
                )

            # 读取文件
            content = file_path.read_text(encoding=encoding)

            # 限制行数
            if max_lines > 0:
                lines = content.split('\n')
                if len(lines) > max_lines:
                    content = '\n'.join(lines[:max_lines])
                    content += f"\n... (truncated, {len(lines) - max_lines} more lines)"

            return ToolResult(
                success=True,
                data={
                    "path": str(file_path.absolute()),
                    "content": content,
                    "size": file_path.stat().st_size,
                    "lines": content.count('\n') + 1
                },
                metadata={
                    "encoding": encoding,
                    "truncated": max_lines > 0 and len(content.split('\n')) >= max_lines
                }
            )

        except UnicodeDecodeError:
            return ToolResult(
                success=False,
                error=f"无法以 {encoding} 编码读取文件"
            )
        except PermissionError:
            return ToolResult(
                success=False,
                error=f"没有读取权限: {path}"
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=str(e)
            )
```

### 注册工具

```python
# src/tools/registry.py 或初始化代码中

from tools.implementations.file_ops import ReadFileTool

def create_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()

    # 注册现有工具
    registry.register_tool_to_library(WebSearchTool())
    registry.register_tool_to_library(WebFetchTool())
    # ...

    # 注册新工具
    registry.register_tool_to_library(ReadFileTool())

    return registry
```

### 为 Agent 分配工具

在 Agent 配置中通过 `required_tools` 指定所需工具：

```python
# 在 Agent 配置中添加工具名称
class CodeAgent(BaseAgent):
    def __init__(self, config=None, toolkit=None):
        if not config:
            config = AgentConfig(
                name="code_agent",
                # ...
                required_tools=[
                    "read_file",      # 新工具
                    "analyze_code"
                ]
            )
        super().__init__(config, toolkit)
```

`create_multi_agent_graph` 会根据 `required_tools` 自动创建对应的 `AgentToolkit` 并绑定到 Agent。

## 权限级别选择指南

| 权限 | 适用场景 | 示例 |
|------|----------|------|
| `PUBLIC` | 只读、无副作用的操作 | 搜索、查询 |
| `NOTIFY` | 有副作用但低风险 | 保存到临时文件 |
| `CONFIRM` | 访问敏感数据或有副作用 | 读取文件、发送请求 |
| `RESTRICTED` | 高风险操作 | 删除文件、执行代码 |

## 工具参数类型

支持的参数类型：

| 类型 | Python 类型 | XML 示例 |
|------|-------------|----------|
| `string` | `str` | `<param><![CDATA[hello]]></param>` |
| `integer` | `int` | `<param><![CDATA[42]]></param>` |
| `number` | `float` | `<param><![CDATA[3.14]]></param>` |
| `boolean` | `bool` | `<param><![CDATA[true]]></param>` |
| `array` | `list` | `<param><![CDATA[["a", "b"]]]></param>` |
| `object` | `dict` | `<param><![CDATA[{"key": "value"}]]></param>` |

## 添加新 Artifact 类型

Artifact 类型通过 `content_type` 字段区分，可以自由扩展：

```python
# 创建自定义类型的 Artifact
await artifact_manager.create_artifact(
    session_id=session_id,
    artifact_id="code_review_report",
    content_type="markdown",  # 内容类型：markdown, python, json 等
    title="Code Review Report",
    content="..."
)
```

### 建议的 content_type

| 类型 | 用途 |
|------|------|
| `markdown` | Markdown 文档（任务计划、报告等） |
| `python` | Python 代码 |
| `javascript` | JavaScript 代码 |
| `json` | JSON 数据 |
| `yaml` | YAML 配置 |
| `text` | 纯文本 |

### 建议的 artifact_id 命名

| ID | 用途 |
|------|------|
| `task_plan` | 任务计划（系统保留） |
| `research_report` | 研究报告 |
| `code_review` | 代码审查报告 |
| `main.py` | 代码文件（使用文件名） |

## 自定义 LLM 模型

### 添加新模型配置

```python
# src/models/llm.py

MODEL_CONFIGS = {
    # 现有配置...

    # 添加新模型
    "my-custom-model": {
        "provider": "openai",  # 或其他 LiteLLM 支持的 provider
        "model": "my-model-name",
        "api_base": "https://my-api.example.com/v1",
        "api_key_env": "MY_API_KEY"
    }
}
```

### 在 Agent 中使用

```python
class MyAgent(BaseAgent):
    def __init__(self):
        super().__init__(AgentConfig(
            # ...
            model="my-custom-model"
        ))
```

## 测试新组件

### 测试工具

```python
# tests/test_tools.py

import pytest
from tools.implementations.file_ops import ReadFileTool

@pytest.mark.asyncio
async def test_read_file_tool():
    tool = ReadFileTool()

    # 测试正常读取
    result = await tool.execute(path="test.txt")
    assert result.success
    assert "content" in result.data

    # 测试文件不存在
    result = await tool.execute(path="nonexistent.txt")
    assert not result.success
    assert "不存在" in result.error
```

### 测试 Agent

```python
# tests/test_agents.py

import pytest
from agents.code_agent import CodeAgent

def test_code_agent_config():
    agent = CodeAgent()
    assert agent.config.name == "code_agent"
    assert "read_file" in agent.config.required_tools

def test_code_agent_system_prompt():
    agent = CodeAgent()
    # build_system_prompt 接受 context 参数（可选）
    prompt = agent.build_system_prompt(context=None)
    assert "代码分析" in prompt

def test_code_agent_format_response():
    agent = CodeAgent()
    result = agent.format_final_response(
        content="分析结果...",
        tool_history=[]
    )
    assert "代码分析报告" in result
```

## 最佳实践

### Agent 设计

1. **单一职责**：每个 Agent 专注于一类任务
2. **明确边界**：清晰定义 Agent 的能力范围
3. **合理的工具集**：只分配必要的工具
4. **详细的提示词**：包含角色、能力、工作流程、输出格式

### Tool 设计

1. **原子操作**：每个工具做一件事
2. **清晰的参数**：参数命名和描述要明确
3. **完善的错误处理**：返回有意义的错误信息
4. **合适的权限**：根据风险选择权限级别

### 测试

1. **单元测试**：测试工具的各种输入情况
2. **集成测试**：测试 Agent 与工具的协作
3. **端到端测试**：测试完整的执行流程
