"""
自定义工具加载器 — 从 MD 文件解析 HttpToolConfig

与 agents/loader.py 对称的设计：
- YAML frontmatter: name, description, permission, type, endpoint, method, headers, parameters, ...
- MD body: 扩展 description（给 LLM 的详细使用指导）

示例 MD 文件：

---
name: query_stock_price
description: "查询指定股票的实时价格"
permission: auto
type: http
endpoint: "https://api.example.com/stock/price"
method: POST
headers:
  Authorization: "Bearer {{SECRET_API_KEY}}"
parameters:
  - name: symbol
    type: string
    description: "股票代码，如 AAPL"
    required: true
  - name: market
    type: string
    description: "市场"
    enum: [US, HK, SH]
    default: "US"
response_extract: "$.data.price"
timeout: 30
---

Query real-time stock price from the exchange API.
Use this when the user asks about current stock prices.
"""

import os
import yaml
from typing import List, Optional

from tools.base import BaseTool, ToolParameter
from tools.custom.http_tool import HttpTool, HttpToolConfig
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


def load_custom_tool(md_path: str) -> BaseTool:
    """
    从 MD 文件加载单个自定义工具

    Args:
        md_path: MD 文件路径

    Returns:
        BaseTool 实例

    Raises:
        ValueError: 文件格式不正确
    """
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 解析 YAML frontmatter
    if not content.startswith("---"):
        raise ValueError(f"MD file must start with YAML frontmatter: {md_path}")

    end_idx = content.index("---", 3)
    frontmatter_str = content[3:end_idx].strip()
    body = content[end_idx + 3:].strip()

    frontmatter = yaml.safe_load(frontmatter_str)

    tool_type = frontmatter.get("type", "http")

    if tool_type == "http":
        return _build_http_tool(frontmatter, body)
    else:
        raise ValueError(f"Unsupported tool type: {tool_type} in {md_path}")


def _build_http_tool(frontmatter: dict, body: str) -> HttpTool:
    """从 frontmatter + body 构建 HttpTool"""

    # 解析参数定义
    _VALID_PARAM_TYPES = {"string", "integer", "number", "boolean"}

    param_defs = []
    for p in frontmatter.get("parameters", []):
        param_type = p.get("type", "string")
        if param_type not in _VALID_PARAM_TYPES:
            raise ValueError(
                f"Unsupported parameter type '{param_type}' for '{p['name']}'. "
                f"Valid types: {sorted(_VALID_PARAM_TYPES)}"
            )
        param_defs.append(ToolParameter(
            name=p["name"],
            type=param_type,
            description=p.get("description", ""),
            required=p.get("required", True),
            default=p.get("default"),
            enum=p.get("enum"),
        ))

    # description: frontmatter 的 description + body（body 作为扩展说明）
    description = frontmatter.get("description", "")
    if body:
        description = f"{description}\n\n{body}" if description else body

    config = HttpToolConfig(
        name=frontmatter["name"],
        description=description,
        permission=frontmatter.get("permission", "confirm"),
        endpoint=frontmatter.get("endpoint", ""),
        method=frontmatter.get("method", "GET"),
        headers=frontmatter.get("headers", {}),
        parameters=param_defs,
        response_extract=frontmatter.get("response_extract"),
        timeout=frontmatter.get("timeout", 30),
    )

    return HttpTool(config)


def load_custom_tools(tools_dir: Optional[str] = None) -> List[BaseTool]:
    """
    加载目录下所有 .md 自定义工具定义

    Args:
        tools_dir: 工具 MD 文件目录，默认为 config/tools/

    Returns:
        BaseTool 实例列表
    """
    if tools_dir is None:
        # 默认从项目根目录 config/tools/ 加载
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        tools_dir = os.path.join(project_root, "config", "tools")

    if not os.path.isdir(tools_dir):
        logger.debug(f"Custom tools directory not found: {tools_dir}")
        return []

    tools = []
    for filename in sorted(os.listdir(tools_dir)):
        if not filename.endswith(".md") or filename.startswith("_"):
            continue

        md_path = os.path.join(tools_dir, filename)
        try:
            tool = load_custom_tool(md_path)
            tools.append(tool)
            logger.info(f"Loaded custom tool: {tool.name} from {filename}")
        except Exception as e:
            logger.error(f"Failed to load custom tool from {filename}: {e}")

    return tools
