"""
自定义工具系统测试

覆盖：
- MD 文件解析（frontmatter + body）
- ToolParameter enum 字段
- HttpTool 实例化和参数定义
- secrets 模板变量解析
- JSONPath 提取
- BaseTool._coerce_params 类型转换
- load_custom_tools 批量加载
"""

import os
import pytest
import tempfile
from unittest.mock import patch

from tools.base import BaseTool, ToolParameter, ToolPermission, ToolResult
from tools.custom.loader import load_custom_tool, load_custom_tools
from tools.custom.http_tool import HttpTool, HttpToolConfig, _extract_jsonpath
from tools.custom.secrets import resolve_secrets


# ============================================================
# secrets 模板变量解析
# ============================================================

class TestResolveSecrets:
    def test_resolve_string(self):
        with patch.dict(os.environ, {"MY_KEY": "secret123"}):
            assert resolve_secrets("Bearer {{MY_KEY}}") == "Bearer secret123"

    def test_resolve_dict(self):
        with patch.dict(os.environ, {"TOKEN": "abc"}):
            result = resolve_secrets({"Authorization": "Bearer {{TOKEN}}", "X-Custom": "static"})
            assert result == {"Authorization": "Bearer abc", "X-Custom": "static"}

    def test_resolve_list(self):
        with patch.dict(os.environ, {"A": "1", "B": "2"}):
            result = resolve_secrets(["{{A}}", "{{B}}", "plain"])
            assert result == ["1", "2", "plain"]

    def test_missing_var_keeps_placeholder(self):
        with patch.dict(os.environ, {}, clear=True):
            # 确保变量不存在
            os.environ.pop("NONEXISTENT_VAR", None)
            result = resolve_secrets("key={{NONEXISTENT_VAR}}")
            assert result == "key={{NONEXISTENT_VAR}}"

    def test_multiple_vars_in_one_string(self):
        with patch.dict(os.environ, {"HOST": "api.example.com", "PORT": "8080"}):
            result = resolve_secrets("https://{{HOST}}:{{PORT}}/api")
            assert result == "https://api.example.com:8080/api"

    def test_non_string_passthrough(self):
        assert resolve_secrets(42) == 42
        assert resolve_secrets(None) is None
        assert resolve_secrets(True) is True


# ============================================================
# JSONPath 提取
# ============================================================

class TestExtractJsonpath:
    def test_simple_key(self):
        assert _extract_jsonpath({"data": {"price": 100}}, "$.data.price") == 100

    def test_root(self):
        data = {"a": 1}
        assert _extract_jsonpath(data, "$") == data
        assert _extract_jsonpath(data, "") == data

    def test_array_index(self):
        data = {"items": [{"name": "a"}, {"name": "b"}]}
        assert _extract_jsonpath(data, "$.items[1].name") == "b"

    def test_missing_key_returns_none(self):
        assert _extract_jsonpath({"a": 1}, "$.b.c") is None

    def test_array_out_of_bounds(self):
        assert _extract_jsonpath({"items": [1, 2]}, "$.items[5]") is None

    def test_nested_path(self):
        data = {"a": {"b": {"c": {"d": "deep"}}}}
        assert _extract_jsonpath(data, "$.a.b.c.d") == "deep"


# ============================================================
# MD 文件解析
# ============================================================

class TestLoadCustomTool:
    def _write_md(self, tmpdir: str, filename: str, content: str) -> str:
        path = os.path.join(tmpdir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_basic_http_tool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md = """---
name: test_api
description: "Test API tool"
type: http
endpoint: "https://api.example.com/test"
method: POST
parameters:
  - name: query
    type: string
    description: "Search query"
    required: true
  - name: limit
    type: integer
    description: "Max results"
    default: 10
---

Use this tool to test API calls.
"""
            path = self._write_md(tmpdir, "test_api.md", md)
            tool = load_custom_tool(path)

            assert isinstance(tool, HttpTool)
            assert tool.name == "test_api"
            assert "Test API tool" in tool.description
            assert "Use this tool to test API calls." in tool.description
            assert tool.permission == ToolPermission.CONFIRM  # 默认 confirm

            params = tool.get_parameters()
            assert len(params) == 2
            assert params[0].name == "query"
            assert params[0].required is True
            assert params[1].name == "limit"
            assert params[1].default == 10

    def test_enum_parameters(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md = """---
name: stock_tool
description: "Stock price query"
type: http
endpoint: "https://api.example.com/stock"
method: GET
parameters:
  - name: market
    type: string
    description: "Market"
    enum: [US, HK, SH]
    default: "US"
---
"""
            path = self._write_md(tmpdir, "stock.md", md)
            tool = load_custom_tool(path)

            params = tool.get_parameters()
            assert params[0].enum == ["US", "HK", "SH"]
            assert params[0].default == "US"

    def test_permission_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md = """---
name: safe_tool
description: "Safe read-only tool"
type: http
permission: auto
endpoint: "https://api.example.com/read"
method: GET
parameters: []
---
"""
            path = self._write_md(tmpdir, "safe.md", md)
            tool = load_custom_tool(path)
            assert tool.permission == ToolPermission.AUTO

    def test_headers_with_secrets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md = """---
name: auth_tool
description: "Tool with auth"
type: http
endpoint: "https://api.example.com/data"
method: GET
headers:
  Authorization: "Bearer {{API_KEY}}"
  X-Custom: "static-value"
parameters: []
---
"""
            path = self._write_md(tmpdir, "auth.md", md)
            tool = load_custom_tool(path)

            assert isinstance(tool, HttpTool)
            # headers 保留模板（运行时解析）
            assert tool._headers["Authorization"] == "Bearer {{API_KEY}}"
            assert tool._headers["X-Custom"] == "static-value"

    def test_invalid_no_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_md(tmpdir, "bad.md", "no frontmatter here")
            with pytest.raises(ValueError, match="YAML frontmatter"):
                load_custom_tool(path)

    def test_unsupported_type(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md = """---
name: bad_tool
description: "Bad type"
type: graphql
---
"""
            path = self._write_md(tmpdir, "bad.md", md)
            with pytest.raises(ValueError, match="Unsupported tool type"):
                load_custom_tool(path)


# ============================================================
# 批量加载
# ============================================================

class TestLoadCustomTools:
    def test_load_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(3):
                path = os.path.join(tmpdir, f"tool_{i}.md")
                with open(path, "w") as f:
                    f.write(f"""---
name: tool_{i}
description: "Tool {i}"
type: http
endpoint: "https://api.example.com/{i}"
method: GET
parameters: []
---
""")

            tools = load_custom_tools(tmpdir)
            assert len(tools) == 3
            names = {t.name for t in tools}
            assert names == {"tool_0", "tool_1", "tool_2"}

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tools = load_custom_tools(tmpdir)
            assert tools == []

    def test_nonexistent_directory(self):
        tools = load_custom_tools("/nonexistent/path")
        assert tools == []

    def test_skips_non_md_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 写一个 .txt 和一个 .md
            with open(os.path.join(tmpdir, "readme.txt"), "w") as f:
                f.write("not a tool")

            with open(os.path.join(tmpdir, "tool.md"), "w") as f:
                f.write("""---
name: real_tool
description: "Real tool"
type: http
endpoint: "https://example.com"
method: GET
parameters: []
---
""")

            tools = load_custom_tools(tmpdir)
            assert len(tools) == 1
            assert tools[0].name == "real_tool"

    def test_bad_file_skipped_gracefully(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 一个好的，一个坏的
            with open(os.path.join(tmpdir, "bad.md"), "w") as f:
                f.write("no frontmatter")

            with open(os.path.join(tmpdir, "good.md"), "w") as f:
                f.write("""---
name: good_tool
description: "Good"
type: http
endpoint: "https://example.com"
method: GET
parameters: []
---
""")

            tools = load_custom_tools(tmpdir)
            assert len(tools) == 1
            assert tools[0].name == "good_tool"


# ============================================================
# BaseTool._coerce_params
# ============================================================

class TestCoerceParams:
    """测试 BaseTool._coerce_params 类型转换"""

    class DummyTool(BaseTool):
        def get_parameters(self):
            return [
                ToolParameter(name="text", type="string", description=""),
                ToolParameter(name="count", type="integer", description=""),
                ToolParameter(name="rate", type="number", description=""),
                ToolParameter(name="flag", type="boolean", description=""),
                ToolParameter(name="items", type="array[string]", description=""),
            ]

        async def execute(self, **params):
            return ToolResult(success=True, data=str(params))

    def test_string_stays_string(self):
        tool = self.DummyTool(name="t", description="t")
        result = tool._coerce_params({"text": "hello"})
        assert result["text"] == "hello"
        assert isinstance(result["text"], str)

    def test_integer_conversion(self):
        tool = self.DummyTool(name="t", description="t")
        result = tool._coerce_params({"count": "42"})
        assert result["count"] == 42
        assert isinstance(result["count"], int)

    def test_number_conversion(self):
        tool = self.DummyTool(name="t", description="t")
        result = tool._coerce_params({"rate": "3.14"})
        assert result["rate"] == 3.14
        assert isinstance(result["rate"], float)

    def test_boolean_true_variants(self):
        tool = self.DummyTool(name="t", description="t")
        for val in ["true", "True", "TRUE", "1", "yes"]:
            result = tool._coerce_params({"flag": val})
            assert result["flag"] is True

    def test_boolean_false(self):
        tool = self.DummyTool(name="t", description="t")
        for val in ["false", "False", "0", "no"]:
            result = tool._coerce_params({"flag": val})
            assert result["flag"] is False

    def test_array_stays_as_is(self):
        tool = self.DummyTool(name="t", description="t")
        result = tool._coerce_params({"items": ["a", "b"]})
        assert result["items"] == ["a", "b"]

    def test_already_typed_passthrough(self):
        tool = self.DummyTool(name="t", description="t")
        # 如果已经是 int（非 str），不做转换
        result = tool._coerce_params({"count": 42})
        assert result["count"] == 42

    def test_invalid_integer_stays_string(self):
        tool = self.DummyTool(name="t", description="t")
        result = tool._coerce_params({"count": "not_a_number"})
        # 转换失败，保持原值
        assert result["count"] == "not_a_number"

    def test_invalid_boolean_stays_string(self):
        tool = self.DummyTool(name="t", description="t")
        # 非法布尔值不应被静默转为 False，应保持原值
        for val in ["maybe", "tru", "on", "enable"]:
            result = tool._coerce_params({"flag": val})
            assert result["flag"] == val, f"'{val}' should stay as string, got {result['flag']}"

    def test_unknown_param_passthrough(self):
        tool = self.DummyTool(name="t", description="t")
        result = tool._coerce_params({"unknown_field": "123"})
        assert result["unknown_field"] == "123"


# ============================================================
# validate_params enum 校验
# ============================================================

class TestValidateParams:
    """测试 validate_params 的 enum 校验"""

    class EnumTool(BaseTool):
        def get_parameters(self):
            return [
                ToolParameter(name="color", type="string", description="",
                              enum=["red", "green", "blue"]),
                ToolParameter(name="name", type="string", description=""),
            ]

        async def execute(self, **params):
            return ToolResult(success=True, data="ok")

    def test_valid_enum_value(self):
        tool = self.EnumTool(name="t", description="t")
        assert tool.validate_params({"color": "red", "name": "test"}) is None

    def test_invalid_enum_value(self):
        tool = self.EnumTool(name="t", description="t")
        error = tool.validate_params({"color": "yellow", "name": "test"})
        assert error is not None
        assert "yellow" in error
        assert "red" in error

    def test_non_enum_param_not_checked(self):
        tool = self.EnumTool(name="t", description="t")
        # name 没有 enum，任意值都 OK
        assert tool.validate_params({"color": "blue", "name": "anything"}) is None
