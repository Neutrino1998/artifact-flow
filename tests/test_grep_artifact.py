"""
Tests for GrepArtifactTool (ripgrep-faithful artifact content search).

依赖 conftest.py 的 artifact_repo + test_user，与 test_read_artifact_pagination.py 同款。
"""

import uuid

import pytest

from config import config
from db.models import User
from repositories.artifact_repo import ArtifactRepository
from repositories.conversation_repo import ConversationRepository
from tools.builtin.artifact_ops import ArtifactManager
from tools.builtin.grep_artifact import (
    GrepArtifactTool,
    _compile_pattern,
    _format_flat,
    _scan_content,
)


@pytest.fixture
async def session_id(conversation_repo: ConversationRepository, test_user: User) -> str:
    conv_id = f"conv-{uuid.uuid4().hex}"
    await conversation_repo.create_conversation(
        conversation_id=conv_id, user_id=test_user.id
    )
    return conv_id


@pytest.fixture
def artifact_manager(artifact_repo: ArtifactRepository) -> ArtifactManager:
    return ArtifactManager(artifact_repo)


@pytest.fixture
def grep_tool(artifact_manager: ArtifactManager) -> GrepArtifactTool:
    return GrepArtifactTool(artifact_manager)


async def _create_artifact(
    manager: ArtifactManager,
    session_id: str,
    content: str,
    aid: str = None,
    title: str = "Test Doc",
) -> str:
    """Helper: create artifact, return its id."""
    manager.set_session(session_id)
    if aid is None:
        aid = f"doc_{uuid.uuid4().hex[:8]}"
    ok, _ = await manager.create_artifact(
        session_id=session_id,
        artifact_id=aid,
        content_type="text/plain",
        title=title,
        content=content,
    )
    assert ok
    return aid


# ============================================================
# 模块级纯函数单测（不需 manager / session 真实数据）
# ============================================================


class TestPureFunctions:

    def test_compile_pattern_regex_default(self):
        rx = _compile_pattern(r"def \w+\(", fixed_strings=False, ignore_case=False)
        assert rx.search("def foo(x):")
        assert not rx.search("class Bar:")

    def test_compile_pattern_fixed_strings_escapes(self):
        # 字面 `def \w+\(` 在文本里不可能存在，所以 fixed_strings=true 必 0 命中
        rx = _compile_pattern(r"def \w+\(", fixed_strings=True, ignore_case=False)
        assert not rx.search("def foo(x):")
        # 但字面 `def foo(` 能命中
        rx2 = _compile_pattern("def foo(", fixed_strings=True, ignore_case=False)
        assert rx2.search("def foo(x):")

    def test_compile_pattern_ignore_case(self):
        rx = _compile_pattern("HELLO", fixed_strings=False, ignore_case=True)
        assert rx.search("hello world")
        assert rx.search("HELLO world")

    def test_scan_empty_content_returns_empty(self):
        rx = _compile_pattern("x", fixed_strings=False, ignore_case=False)
        assert _scan_content("", rx, context=0, max_count=20) == []

    def test_scan_no_match_returns_empty(self):
        rx = _compile_pattern("nomatch", fixed_strings=False, ignore_case=False)
        assert _scan_content("a\nb\nc\n", rx, context=0, max_count=20) == []

    def test_scan_basic_single_match_no_context(self):
        rx = _compile_pattern("foo", fixed_strings=False, ignore_case=False)
        hits = _scan_content("aaa\nfoo\nbbb\n", rx, context=0, max_count=20)
        # 1-indexed line 2, is_match=True
        assert hits == [(2, "foo", True)]

    def test_scan_context_expands_window(self):
        rx = _compile_pattern("foo", fixed_strings=False, ignore_case=False)
        content = "a\nb\nfoo\nc\nd\n"
        hits = _scan_content(content, rx, context=1, max_count=20)
        # match at line 3 (1-indexed), context=1 → lines 2-4
        assert hits == [
            (2, "b", False),
            (3, "foo", True),
            (4, "c", False),
        ]

    def test_scan_overlapping_contexts_merge_dedup(self):
        # 两个命中（行 3 / 行 5），context=2 → ranges [1-5] 和 [3-7]
        # 行 3,4,5 重叠，必须去重
        rx = _compile_pattern("X", fixed_strings=False, ignore_case=False)
        content = "a\nb\nX\nd\nX\nf\ng\n"  # X at lines 3, 5
        hits = _scan_content(content, rx, context=2, max_count=20)
        # 期望: 行 1-7 全部出现一次（去重），is_match 标记正确
        line_nos = [h[0] for h in hits]
        assert line_nos == [1, 2, 3, 4, 5, 6, 7]
        is_match_at = {h[0]: h[2] for h in hits}
        assert is_match_at[3] is True
        assert is_match_at[5] is True
        assert is_match_at[1] is False
        assert is_match_at[4] is False

    def test_scan_non_overlapping_contexts_keep_gap(self):
        # 两个命中相距太远（行 2 / 行 8），context=1 → ranges [1-3] 和 [7-9]
        # 中间行 4-6 应缺失，formatter 会插 `--`
        rx = _compile_pattern("X", fixed_strings=False, ignore_case=False)
        content = "a\nX\nb\nc\nd\ne\nf\nX\ng\n"
        hits = _scan_content(content, rx, context=1, max_count=20)
        line_nos = [h[0] for h in hits]
        # 不应包含 4,5,6
        assert line_nos == [1, 2, 3, 7, 8, 9]

    def test_scan_max_count_limits_matches_only(self):
        # 5 个潜在命中行，max_count=3 → 只取前 3 个
        # context=0 → 输出只有命中行
        rx = _compile_pattern("X", fixed_strings=False, ignore_case=False)
        content = "X\nX\nX\nX\nX\n"
        hits = _scan_content(content, rx, context=0, max_count=3)
        assert len(hits) == 3
        assert all(h[2] for h in hits)  # 全部是命中
        assert [h[0] for h in hits] == [1, 2, 3]

    def test_format_flat_inserts_separator_on_gap(self):
        hits = [
            (1, "a", False),
            (2, "X", True),
            (3, "b", False),
            (7, "c", False),
            (8, "X", True),
            (9, "d", False),
        ]
        out = _format_flat(hits)
        lines = out.split("\n")
        assert "--" in lines
        assert lines == [
            "1-a",
            "2:X",
            "3-b",
            "--",
            "7-c",
            "8:X",
            "9-d",
        ]

    def test_format_flat_no_separator_when_adjacent(self):
        hits = [(1, "a", True), (2, "b", True), (3, "c", True)]
        out = _format_flat(hits)
        assert "--" not in out
        assert out == "1:a\n2:b\n3:c"

    # ─── 全文 finditer 语义回归（P1 修复）────────────────────────────
    # 旧实现是逐行 search，把每行当独立字符串喂给 regex，导致 \A/\Z
    # 在每行边界都误命中、跨行 pattern 永远 0 命中。新实现对整文跑
    # finditer 再映射回行号，兑现 description 里 "Python re syntax" 契约。

    def test_scan_anchor_A_only_matches_artifact_start(self):
        r"""\A 应该只匹配 artifact 起始；旧实现会在每一行 'foo' 都误报。"""
        rx = _compile_pattern(r"\Afoo", fixed_strings=False, ignore_case=False)
        # foo 不在 artifact 开头 → 必须 0 命中
        assert _scan_content("bar\nfoo\n", rx, context=0, max_count=20) == []
        # foo 在 artifact 开头 → 必须 1 命中
        hits = _scan_content("foo\nbar\n", rx, context=0, max_count=20)
        assert hits == [(1, "foo", True)]

    def test_scan_anchor_Z_only_matches_artifact_end(self):
        r"""\Z 应该只匹配 artifact 结尾；旧实现会在每一行末尾误报。"""
        rx = _compile_pattern(r"bar\Z", fixed_strings=False, ignore_case=False)
        # bar 不在末尾 → 0 命中
        assert _scan_content("bar\nfoo\n", rx, context=0, max_count=20) == []
        # bar 在末尾（注意末尾换行不算 'after Z'，Python re 的 \Z 行为）
        hits = _scan_content("foo\nbar", rx, context=0, max_count=20)
        assert hits == [(2, "bar", True)]

    def test_scan_cross_line_pattern_matches_on_starting_line(self):
        """foo\\nbar 应该跨行命中，在起始行（foo 所在行）打点；旧实现 0 命中。"""
        rx = _compile_pattern("foo\nbar", fixed_strings=False, ignore_case=False)
        hits = _scan_content("xxx\nfoo\nbar\nyyy\n", rx, context=0, max_count=20)
        # 起始行是 'foo'（line 2），bar 不单独标 match
        assert hits == [(2, "foo", True)]

    def test_scan_same_line_multiple_matches_dedup(self):
        """同一行多次命中 → 行级算 1 个命中，max_count 计数也按行算。"""
        rx = _compile_pattern("X", fixed_strings=False, ignore_case=False)
        hits = _scan_content("XXX\nY\nXXX\n", rx, context=0, max_count=20)
        # 两行各 3 个字面 X，但每行只报 1 次
        assert hits == [(1, "XXX", True), (3, "XXX", True)]

    def test_scan_zero_width_matches_dropped_uniformly(self):
        r"""所有零宽匹配整体 drop —— 不管 pattern 形态(`\A` / `\b` / `^` / `\Z` /
        `^$`)、不管落在空行还是非空行,统统不报。

        理由(详见 _scan_content docstring):从源码启发式判 anchor 意图
        做不到无 false positive,选择"全 drop"换简单和可证明。失去的
        capability 是 `^$` 找空行,ArtifactFlow 里没真实用例。
        """
        # bare \A 落在非空行
        rx = _compile_pattern(r"\A", fixed_strings=False, ignore_case=False)
        assert _scan_content("foo\nbar\n", rx, context=0, max_count=20) == []
        # bare \A 落在前导空行(之前补丁链的一个反例)
        assert _scan_content("\nfoo\n", rx, context=0, max_count=20) == []

        # bare \Z 落在末尾空行
        rx = _compile_pattern(r"\Z", fixed_strings=False, ignore_case=False)
        assert _scan_content("foo\n\n", rx, context=0, max_count=20) == []

        # bare ^ 不会把每行都标 match
        rx = _compile_pattern(r"^", fixed_strings=False, ignore_case=False)
        assert _scan_content("foo\nbar\nbaz\n", rx, context=0, max_count=20) == []

        # bare \b 单词边界
        rx = _compile_pattern(r"\b", fixed_strings=False, ignore_case=False)
        assert _scan_content("hello world\n", rx, context=0, max_count=20) == []

        # `^$` 找空行也 drop(放弃此能力,文档已说明)
        rx = _compile_pattern(r"^$", fixed_strings=False, ignore_case=False)
        assert _scan_content("foo\n\nbar\n", rx, context=0, max_count=20) == []

    def test_scan_caret_dollar_still_match_line_boundaries_under_multiline(self):
        """re.MULTILINE 下 ^/$ 按行边界匹配，与旧实现等价（这部分行为不变）。"""
        rx = _compile_pattern(r"^foo", fixed_strings=False, ignore_case=False)
        # foo 在多行的不同位置
        hits = _scan_content("foo\nbar foo\nfoo bar\n", rx, context=0, max_count=20)
        # 行 1 (^foo) 和 行 3 (^foo bar)；行 2 'bar foo' 中 foo 不在行首
        assert hits == [(1, "foo", True), (3, "foo bar", True)]


# ============================================================
# 单 artifact 模式（id 传入）
# ============================================================


class TestSingleArtifactMode:

    async def test_default_regex(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactManager,
        session_id: str,
    ):
        content = "import re\ndef foo(x):\n    return x\n\ndef baz():\n    pass\n"
        aid = await _create_artifact(artifact_manager, session_id, content)

        result = await grep_tool(pattern=r"def \w+\(", id=aid)
        assert result.success
        # 命中 line 2 (def foo() 和 line 5 (def baz()）
        assert "2:def foo(x):" in result.data
        assert "5:def baz():" in result.data
        assert "2 matches" in result.data
        # 不应有 heading
        assert aid + "\n" not in result.data.split("\n\n")[0]

    async def test_fixed_strings_disables_regex(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactManager,
        session_id: str,
    ):
        content = "alpha.beta\nalphaXbeta\n"
        aid = await _create_artifact(artifact_manager, session_id, content)

        # 默认 regex → `.` 匹配任意字符 → 两行都命中
        result_regex = await grep_tool(pattern="alpha.beta", id=aid)
        assert result_regex.success
        assert "2 matches" in result_regex.data

        # fixed_strings=true → 字面 `alpha.beta` → 只第 1 行命中
        result_lit = await grep_tool(
            pattern="alpha.beta", id=aid, fixed_strings=True
        )
        assert result_lit.success
        assert "1 matches" in result_lit.data
        assert "1:alpha.beta" in result_lit.data
        assert "alphaXbeta" not in result_lit.data

    async def test_ignore_case(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactManager,
        session_id: str,
    ):
        content = "Hello World\nHELLO again\nhi there\n"
        aid = await _create_artifact(artifact_manager, session_id, content)

        result = await grep_tool(pattern="hello", id=aid, ignore_case=True)
        assert result.success
        assert "2 matches" in result.data
        assert "1:Hello World" in result.data
        assert "2:HELLO again" in result.data

    async def test_context_lines_and_gap(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactManager,
        session_id: str,
    ):
        # 命中行远离 → 验证 -- 分隔
        content = "a\nX\nb\nc\nd\ne\nf\nX\ng\n"
        aid = await _create_artifact(artifact_manager, session_id, content)

        result = await grep_tool(pattern="X", id=aid, context=1)
        assert result.success
        assert "--" in result.data
        assert "2:X" in result.data
        assert "8:X" in result.data
        # 不应包含中间的 c/d/e（gap 区）
        assert "4-c" not in result.data
        assert "5-d" not in result.data

    async def test_max_count_per_artifact(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactManager,
        session_id: str,
    ):
        content = "X\nX\nX\nX\nX\n"
        aid = await _create_artifact(artifact_manager, session_id, content)

        result = await grep_tool(pattern="X", id=aid, max_count=3)
        assert result.success
        assert "3 matches" in result.data
        # 应该只出现前 3 行
        assert "1:X" in result.data
        assert "3:X" in result.data
        # 第 4、5 行不该出现
        lines = [l for l in result.data.split("\n") if l.startswith("4:") or l.startswith("5:")]
        assert lines == []

    async def test_no_match_returns_success(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactManager,
        session_id: str,
    ):
        aid = await _create_artifact(artifact_manager, session_id, "foo\nbar\n")

        result = await grep_tool(pattern="zzz", id=aid)
        assert result.success
        assert "No matches for" in result.data
        assert "'zzz'" in result.data

    async def test_id_not_found(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactManager,
        session_id: str,
    ):
        artifact_manager.set_session(session_id)
        result = await grep_tool(pattern="x", id="does_not_exist")
        assert not result.success
        assert "not found" in (result.error or "").lower()

    async def test_invalid_regex_fails_loud(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactManager,
        session_id: str,
    ):
        aid = await _create_artifact(artifact_manager, session_id, "anything\n")

        result = await grep_tool(pattern="(unclosed", id=aid)
        assert not result.success
        assert "Invalid regex" in (result.error or "")

    async def test_artifact_anchor_via_tool(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactManager,
        session_id: str,
    ):
        """End-to-end: `\\Afoo` 走完工具栈后只匹配 artifact 起始，不在每行误报。"""
        aid = await _create_artifact(artifact_manager, session_id, "bar\nfoo\nbaz\n")
        result = await grep_tool(pattern=r"\Afoo", id=aid)
        assert result.success
        # 没有 'foo' 在 artifact 开头 → No matches
        assert "No matches for" in result.data


# ============================================================
# Session 模式（id 省略）
# ============================================================


class TestSessionMode:

    async def test_heading_output_across_artifacts(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactManager,
        session_id: str,
    ):
        aid1 = await _create_artifact(
            artifact_manager, session_id, "alpha\nneedle\ngamma\n", aid="art_a"
        )
        aid2 = await _create_artifact(
            artifact_manager, session_id, "delta\nepsilon\nneedle here\n", aid="art_b"
        )

        result = await grep_tool(pattern="needle")
        assert result.success

        # heading 必须出现两次 artifact id
        assert "art_a" in result.data
        assert "art_b" in result.data
        # 每个块内有命中行
        assert "2:needle" in result.data
        assert "3:needle here" in result.data
        # summary 在末尾
        assert "2 matches across 2 artifacts" in result.data

    async def test_no_match_session_wide(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactManager,
        session_id: str,
    ):
        await _create_artifact(artifact_manager, session_id, "alpha\nbeta\n", aid="a1")
        await _create_artifact(artifact_manager, session_id, "gamma\ndelta\n", aid="a2")

        result = await grep_tool(pattern="zzz_no_such_word")
        assert result.success
        assert "No matches for" in result.data

    async def test_skip_empty_content_artifacts(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactManager,
        session_id: str,
    ):
        await _create_artifact(artifact_manager, session_id, "", aid="empty_one")
        await _create_artifact(
            artifact_manager, session_id, "hit_here\n", aid="real_one"
        )

        result = await grep_tool(pattern="hit_here")
        assert result.success
        assert "real_one" in result.data
        # 空 artifact 不该出现在 heading 里
        assert "empty_one" not in result.data
        assert "1 matches across 1 artifacts" in result.data

    async def test_in_memory_cache_visible(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactManager,
        session_id: str,
    ):
        """新建 artifact 在 flush 前应该也能被 grep 到（list_artifacts 已 merge cache）。"""
        await _create_artifact(
            artifact_manager,
            session_id,
            "freshly_written_token\n",
            aid="cached_art",
        )
        # 不调用 flush_all

        result = await grep_tool(pattern="freshly_written_token")
        assert result.success
        assert "cached_art" in result.data
        assert "1:freshly_written_token" in result.data

    async def test_session_cap_truncation(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactManager,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """单 artifact 有大量命中、session cap 调到 5 → summary 应含 'Hit session cap'。"""
        monkeypatch.setattr(config, "SESSION_GREP_MAX_TOTAL", 5)

        content = "X\n" * 50  # 50 个命中行
        await _create_artifact(
            artifact_manager, session_id, content, aid="huge_art"
        )

        # max_count=100 > session cap → 真正生效的应该是 session cap 5
        result = await grep_tool(pattern="X", max_count=100)
        assert result.success
        assert "Hit session cap (5)" in result.data
        assert "Refine pattern" in result.data
        # 输出里应该正好 5 个命中行
        match_lines = [
            l for l in result.data.split("\n")
            if l.startswith(tuple(f"{n}:" for n in range(1, 51)))
        ]
        assert len(match_lines) == 5


# ============================================================
# 边界 / 参数验证
# ============================================================


class TestEdgeCases:

    async def test_no_active_session(
        self, artifact_manager: ArtifactManager
    ):
        # 故意不 set_session
        tool = GrepArtifactTool(artifact_manager)
        result = await tool(pattern="x")
        assert not result.success
        assert "No active session" in (result.error or "")

    async def test_no_manager(self):
        tool = GrepArtifactTool(manager=None)
        result = await tool(pattern="x")
        assert not result.success
        assert "ArtifactManager not configured" in (result.error or "")

    async def test_max_count_zero_returns_no_match(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactManager,
        session_id: str,
    ):
        aid = await _create_artifact(artifact_manager, session_id, "X\nX\nX\n")
        result = await grep_tool(pattern="X", id=aid, max_count=0)
        assert result.success
        assert "No matches for" in result.data

    async def test_negative_context_treated_as_zero(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactManager,
        session_id: str,
    ):
        aid = await _create_artifact(
            artifact_manager, session_id, "a\nX\nb\n"
        )
        result = await grep_tool(pattern="X", id=aid, context=-3)
        assert result.success
        # 无 context → 只输出命中行
        assert "2:X" in result.data
        assert "1-a" not in result.data
        assert "3-b" not in result.data

    async def test_grep_default_max_result_size_chars(self):
        """grep_artifact 默认 50000，溢出由引擎中间件落盘兜底。"""
        tool = GrepArtifactTool(manager=None)
        assert tool.max_result_size_chars == 50000
