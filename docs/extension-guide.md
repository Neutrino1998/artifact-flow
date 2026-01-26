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

from agents.base import BaseAgent, AgentConfig

class CodeAgent(BaseAgent):
    """代码分析 Agent"""

    def __init__(self):
        super().__init__(AgentConfig(
            name="code",
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
            max_tool_rounds=5
        ))

    def build_system_prompt(self, toolkit) -> str:
        tool_docs = toolkit.generate_tool_docs()

        return f"""# 角色
你是一个专业的代码分析 Agent，负责分析代码结构、评估代码质量、提供重构建议。

# 能力
- 理解多种编程语言的代码结构
- 识别代码中的问题和改进点
- 提供具体的重构建议

# 可用工具
{tool_docs}

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

    def format_final_response(self, content: str, state) -> str:
        """格式化最终响应"""
        return f"""## 代码分析报告

{content}
"""
```

### 注册到 Graph

```python
# src/core/graph.py

from agents.code_agent import CodeAgent

def create_multi_agent_graph(tool_registry: ToolRegistry) -> ExtendableGraph:
    graph = ExtendableGraph()

    # 注册现有 Agent
    graph.register_agent(LeadAgent())
    graph.register_agent(SearchAgent())
    graph.register_agent(CrawlAgent())

    # 注册新 Agent
    graph.register_agent(CodeAgent())  # 添加这行

    # 设置工具注册中心
    graph.set_tool_registry(tool_registry)

    return graph
```

### 更新 Lead Agent 提示词

让 Lead Agent 知道可以调用新的 Agent：

```python
# 在 Lead Agent 的系统提示词中添加
"""
# 可调用的 SubAgent

- **search**: 信息检索，用于搜索互联网
- **crawl**: 内容采集，用于抓取网页内容
- **code**: 代码分析，用于分析和审查代码  # 新增
"""
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
from tools.base import BaseTool, ToolParameter, ToolResult, ToolPermission

class ReadFileTool(BaseTool):
    """读取文件内容"""

    name = "read_file"
    description = "读取指定路径的文件内容"
    permission = ToolPermission.CONFIRM  # 需要用户确认

    def get_parameters(self) -> list[ToolParameter]:
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

```python
# 在 Agent 配置中添加工具名称
class CodeAgent(BaseAgent):
    def __init__(self):
        super().__init__(AgentConfig(
            # ...
            required_tools=[
                "read_file",      # 新工具
                "analyze_code"
            ]
        ))
```

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

Artifact 类型通过 `type` 字段区分，可以自由扩展：

```python
# 创建自定义类型的 Artifact
await artifact_manager.create_artifact(
    session_id=session_id,
    artifact_type="code_review",  # 自定义类型
    title="Code Review Report",
    content="..."
)
```

### 建议的类型命名

| 类型 | 用途 |
|------|------|
| `task_plan` | 任务计划（系统保留） |
| `result` | 执行结果（系统保留） |
| `code_review` | 代码审查报告 |
| `analysis` | 分析报告 |
| `draft` | 草稿文档 |
| `data` | 数据集 |

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
    assert agent.name == "code"
    assert "read_file" in agent.config.required_tools

def test_code_agent_system_prompt():
    agent = CodeAgent()
    # 创建 mock toolkit
    toolkit = MockToolkit(["read_file", "analyze_code"])
    prompt = agent.build_system_prompt(toolkit)
    assert "代码分析" in prompt
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
