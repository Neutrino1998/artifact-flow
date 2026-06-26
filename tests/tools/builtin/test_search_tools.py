"""search_tools 纯渲染函数单测(B-3 渐进式披露)。

覆盖:select: 精确选 / 关键词搜 / 过滤到可调集 / 排除 search_tools 自身 /
无匹配的引导文案 / unknown 名提示 / 空 query loud-fail。
"""

from core.effective_toolset import EffectiveToolset
from tools.base import ToolParameter, ToolPermission
from tools.builtin.search_tools import search_tools_result


class _Tool:
    """最小工具桩:render_tool_docs 读 name/description/get_parameters/show_example。"""
    def __init__(self, name, description, permission=ToolPermission.AUTO):
        self.name = name
        self.description = description
        self.permission = permission
        self.show_example = False

    def get_parameters(self):
        return [ToolParameter(name="q", type="string", description="a param")]


def _tools(*specs):
    return {name: _Tool(name, desc) for name, desc in specs}


def _eff(*names):
    return EffectiveToolset({n: ToolPermission.AUTO for n in names})


def test_select_exact_names():
    tools = _tools(
        ("github__search_repos", "Search GitHub repositories"),
        ("github__create_issue", "Open an issue"),
    )
    eff = _eff("github__search_repos", "github__create_issue")
    res = search_tools_result("select:github__search_repos", eff, tools)
    assert res.success
    assert "github__search_repos" in res.data
    assert "Search GitHub repositories" in res.data
    # 未选中的不出现
    assert "github__create_issue" not in res.data


def test_select_multiple_comma_separated():
    tools = _tools(("a__x", "tool x"), ("a__y", "tool y"))
    eff = _eff("a__x", "a__y")
    res = search_tools_result("select:a__x, a__y", eff, tools)
    assert "a__x" in res.data and "a__y" in res.data


def test_keyword_matches_name_and_description():
    tools = _tools(
        ("github__search_repos", "Find repositories"),
        ("weather", "Query the weather forecast"),
    )
    eff = _eff("github__search_repos", "weather")
    # 关键词命中 description
    res = search_tools_result("forecast", eff, tools)
    assert "weather" in res.data
    assert "github__search_repos" not in res.data


def test_filters_to_callable_set():
    # tools 字典里有,但不在 effective_toolset(未授/disabled)→ 不可被 search 出。
    # 全无匹配 → 走「No tools matched」引导分支,绝不泄露不可调工具的描述。
    tools = _tools(("secret_tool", "do secret things"))
    eff = _eff()  # 空可调集
    res = search_tools_result("select:secret_tool", eff, tools)
    assert res.success
    assert "do secret things" not in res.data
    assert "No tools matched" in res.data


def test_partial_callable_filters_unauthorized():
    # 一个可调 + 一个不可调:只渲可调的,不可调的进 unknown 提示、不泄露其描述
    tools = _tools(("weather", "forecast"), ("secret_tool", "do secret things"))
    eff = _eff("weather")  # 只 weather 可调
    res = search_tools_result("select:weather,secret_tool", eff, tools)
    assert res.success
    assert "forecast" in res.data            # 可调的渲完整 doc
    assert "do secret things" not in res.data  # 不可调的描述不泄露
    assert "secret_tool" in res.data         # 但报告它被跳过
    assert "Not found or not available" in res.data


def test_excludes_search_tools_itself():
    tools = _tools(("search_tools", "the searcher"), ("weather", "forecast tool"))
    eff = _eff("search_tools", "weather")
    res = search_tools_result("select:search_tools", eff, tools)
    # search_tools 自身不入结果
    assert "the searcher" not in res.data


def test_no_match_lists_callable_tools():
    tools = _tools(("weather", "forecast"), ("stocks", "prices"))
    eff = _eff("weather", "stocks")
    res = search_tools_result("nonexistent_xyz", eff, tools)
    assert res.success
    assert "No tools matched" in res.data
    assert "stocks" in res.data and "weather" in res.data


def test_empty_query_fails_loudly():
    res = search_tools_result("   ", _eff("weather"), _tools(("weather", "f")))
    assert res.success is False
    assert "non-empty" in res.error


def test_select_unknown_name_reported():
    tools = _tools(("weather", "forecast"))
    eff = _eff("weather")
    res = search_tools_result("select:weather,ghost__tool", eff, tools)
    assert res.success
    assert "weather" in res.data
    assert "ghost__tool" in res.data
    assert "Not found or not available" in res.data
