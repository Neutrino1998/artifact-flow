"""
Tests for GrepArtifactTool (ripgrep-faithful artifact content search).

依赖 conftest.py 的 artifact_repo + test_user，与 test_read_artifact_pagination.py 同款。
"""

import time
import uuid

import pytest

from config import config
from db.models import User
from repositories.artifact_repo import ArtifactRepository
from repositories.conversation_repo import ConversationRepository
from tools.builtin.artifact_service import ArtifactService
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
def artifact_manager(artifact_repo: ArtifactRepository) -> ArtifactService:
    return ArtifactService(artifact_repo)


@pytest.fixture
def grep_tool(artifact_manager: ArtifactService) -> GrepArtifactTool:
    return GrepArtifactTool(artifact_manager)


async def _create_artifact(
    manager: ArtifactService,
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

    def test_scan_raw_match_cap_bounds_single_dense_line(self, monkeypatch):
        """Finding 1:单行海量命中时 max_count(去重行)永远到不了,raw-match 上界
        必须接管,否则 finditer 被全部迭代(同步 CPU wedge)。触顶置 stats[scan_capped]。"""
        monkeypatch.setattr(config, "GREP_MAX_SCAN_MATCHES", 50)
        rx = _compile_pattern("a", fixed_strings=False, ignore_case=False)
        content = "a" * 10_000  # 单行,1 个去重行 / 10000 个原始命中
        stats: dict = {}
        hits = _scan_content(content, rx, context=0, max_count=20, stats=stats)
        # 全 collapse 到 line 1 → 1 个命中行,但扫描提前触顶
        assert hits == [(1, content, True)]
        assert stats.get("scan_capped") is True

    def test_scan_no_cap_signal_when_under_budget(self, monkeypatch):
        """未触顶时不置 scan_capped(避免误报 incomplete)。"""
        monkeypatch.setattr(config, "GREP_MAX_SCAN_MATCHES", 50)
        rx = _compile_pattern("X", fixed_strings=False, ignore_case=False)
        stats: dict = {}
        _scan_content("X\nX\nX\n", rx, context=0, max_count=20, stats=stats)
        assert "scan_capped" not in stats

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

    def test_format_flat_truncates_long_line(self, monkeypatch):
        """单行超 GREP_MAX_LINE_CHARS → 截断 + 标记总长（reviewer P2，ripgrep --max-columns 式）。"""
        monkeypatch.setattr(config, "GREP_MAX_LINE_CHARS", 10)
        out = _format_flat([(1, "x" * 100, True)])
        assert out.startswith("1:" + "x" * 10 + " ")
        assert "100 chars" in out
        assert len(out) < 60  # 远小于原始 100 + 行号
        # 不超限的行不受影响
        assert _format_flat([(2, "short", False)]) == "2-short"

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

    def test_scan_anchor_z_only_matches_artifact_end(self):
        r"""\z 只匹配 artifact 结尾。RE2 用 \z 表示 end-of-input（Python 的 \Z 在
        RE2 下非法、编译报错）—— 这是工具切到 RE2/ripgrep 方言后的契约。"""
        rx = _compile_pattern(r"bar\z", fixed_strings=False, ignore_case=False)
        # bar 不在末尾 → 0 命中
        assert _scan_content("bar\nfoo\n", rx, context=0, max_count=20) == []
        # bar 在末尾
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
        r"""所有零宽匹配整体 drop —— 不管 pattern 形态(`\A` / `\b` / `^` / `\z` /
        `^$`)、不管落在空行还是非空行,统统不报。

        理由(详见 _scan_content docstring):从源码启发式判 anchor 意图
        做不到无 false positive,选择"全 drop"换简单和可证明。失去的
        capability 是 `^$` 找空行,ArtifactFlow 里没真实用例。
        附:RE2 在行边界会**重复**返回零宽 anchor 命中,这条 drop 一并中和。
        """
        # bare \A 落在非空行
        rx = _compile_pattern(r"\A", fixed_strings=False, ignore_case=False)
        assert _scan_content("foo\nbar\n", rx, context=0, max_count=20) == []
        # bare \A 落在前导空行(之前补丁链的一个反例)
        assert _scan_content("\nfoo\n", rx, context=0, max_count=20) == []

        # bare \z 落在末尾空行(RE2 用 \z,非 Python \Z)
        rx = _compile_pattern(r"\z", fixed_strings=False, ignore_case=False)
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
        artifact_manager: ArtifactService,
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
        artifact_manager: ArtifactService,
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
        artifact_manager: ArtifactService,
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
        artifact_manager: ArtifactService,
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
        artifact_manager: ArtifactService,
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
        artifact_manager: ArtifactService,
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
        artifact_manager: ArtifactService,
        session_id: str,
    ):
        artifact_manager.set_session(session_id)
        result = await grep_tool(pattern="x", id="does_not_exist")
        assert not result.success
        assert "not found" in (result.error or "").lower()

    async def test_invalid_regex_fails_loud(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
        session_id: str,
    ):
        aid = await _create_artifact(artifact_manager, session_id, "anything\n")

        result = await grep_tool(pattern="(unclosed", id=aid)
        assert not result.success
        assert "Invalid regex" in (result.error or "")

    async def test_artifact_anchor_via_tool(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
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
        artifact_manager: ArtifactService,
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
        artifact_manager: ArtifactService,
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
        artifact_manager: ArtifactService,
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
        artifact_manager: ArtifactService,
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
        artifact_manager: ArtifactService,
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
        self, artifact_manager: ArtifactService
    ):
        # 故意不 set_session
        tool = GrepArtifactTool(artifact_manager)
        result = await tool(pattern="x")
        assert not result.success
        assert "No active session" in (result.error or "")

    async def test_no_manager(self):
        tool = GrepArtifactTool(service=None)
        result = await tool(pattern="x")
        assert not result.success
        assert "ArtifactService not configured" in (result.error or "")

    async def test_max_count_zero_returns_no_match(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
        session_id: str,
    ):
        aid = await _create_artifact(artifact_manager, session_id, "X\nX\nX\n")
        result = await grep_tool(pattern="X", id=aid, max_count=0)
        assert result.success
        assert "No matches for" in result.data

    async def test_negative_context_treated_as_zero(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
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
        tool = GrepArtifactTool(service=None)
        assert tool.max_result_size_chars == 50000


# ============================================================
# RE2 方言 + 抗 ReDoS（GREP-01：引擎切到 RE2）
# ============================================================


class TestRe2Dialect:

    def test_redos_pattern_completes_fast(self):
        """灾难性回溯 pattern 在 RE2 上是线性的：旧 Python re 对 'a'*30 就要 ~50s,
        这里 'a'*2000+X 必须毫秒级完成（结构性免疫，是本次修复的核心）。"""
        rx = _compile_pattern(r"(a+)+$", fixed_strings=False, ignore_case=False)
        victim = "a" * 2000 + "X"
        start = time.perf_counter()
        hits = _scan_content(victim, rx, context=0, max_count=20)
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"RE2 scan took {elapsed:.3f}s — 疑似回退到回溯引擎"
        assert hits == []  # X 在末尾,锚定 $ 不命中

    def test_nested_quantifier_on_repetitive_content_fast(self):
        """另一类经典 ReDoS：'(a|aa)+b' 对长重复串。RE2 线性，必须秒内完成。"""
        rx = _compile_pattern(r"(a|aa)+b", fixed_strings=False, ignore_case=False)
        start = time.perf_counter()
        _scan_content("a" * 100_000, rx, context=0, max_count=20)
        assert time.perf_counter() - start < 1.0

    async def test_backreference_rejected_loudly(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
        session_id: str,
    ):
        """RE2 不支持 backreference → 编译期响亮失败，错误文案点明方言。"""
        aid = await _create_artifact(artifact_manager, session_id, "foo foo\n")
        result = await grep_tool(pattern=r"(\w+)\s\1", id=aid)
        assert not result.success
        assert "Invalid regex" in (result.error or "")
        assert "RE2" in (result.error or "")

    async def test_lookahead_rejected_loudly(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
        session_id: str,
    ):
        """RE2 不支持 look-around → 编译期响亮失败。"""
        aid = await _create_artifact(artifact_manager, session_id, "foobar\n")
        result = await grep_tool(pattern=r"foo(?=bar)", id=aid)
        assert not result.success
        assert "Invalid regex" in (result.error or "")

    async def test_capital_Z_rejected_with_z_hint(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
        session_id: str,
    ):
        r"""Python 习惯的 \Z 在 RE2 下非法；错误文案引导改用 \z。"""
        aid = await _create_artifact(artifact_manager, session_id, "foo\nbar")
        result = await grep_tool(pattern=r"bar\Z", id=aid)
        assert not result.success
        assert r"\z" in (result.error or "")

    async def test_z_anchor_works_via_tool(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
        session_id: str,
    ):
        r"""\z 走完工具栈应只匹配 artifact 末尾。"""
        aid = await _create_artifact(artifact_manager, session_id, "foo\nbar")
        result = await grep_tool(pattern=r"bar\z", id=aid)
        assert result.success
        assert "2:bar" in result.data


# ============================================================
# Unicode 偏移正确性（re2 字符偏移 vs 字节偏移，本项目大量中文）
# ============================================================


class TestUnicodeOffsets:

    async def test_chinese_content_line_numbers_correct(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
        session_id: str,
    ):
        """多字节中文内容下,命中行号必须按字符(非字节)正确映射。
        若 re2 返回字节偏移,这里行号会错乱。"""
        content = "第一行中文内容\n第二行也是中文\n目标TARGET在这里\n第四行结尾\n"
        aid = await _create_artifact(artifact_manager, session_id, content)
        result = await grep_tool(pattern="TARGET", id=aid)
        assert result.success
        # TARGET 在第 3 行
        assert "3:目标TARGET在这里" in result.data
        assert "1 matches" in result.data

    async def test_emoji_astral_plane_line_numbers_correct(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
        session_id: str,
    ):
        """星平面字符(4 字节 UTF-8 / Python 1 code point)下行号仍正确。"""
        content = "abc😀def\n第二行🎉表情\nNEEDLE命中行\n"
        aid = await _create_artifact(artifact_manager, session_id, content)
        result = await grep_tool(pattern="NEEDLE", id=aid)
        assert result.success
        assert "3:NEEDLE命中行" in result.data


# ============================================================
# 资源护栏（GREP-02 聚合预算 / GREP-03 上界 / 输入封顶）
# ============================================================


class TestResourceGuards:

    async def test_pattern_length_capped(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(config, "GREP_MAX_PATTERN_CHARS", 10)
        aid = await _create_artifact(artifact_manager, session_id, "anything\n")
        result = await grep_tool(pattern="a" * 50, id=aid)
        assert not result.success
        assert "too long" in (result.error or "").lower()

    async def test_context_clamped_to_upper_bound(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """超大 context 被 clamp,不会铺满全文（GREP-03）。"""
        monkeypatch.setattr(config, "GREP_MAX_CONTEXT", 1)
        content = "a\nb\nc\nX\nd\ne\nf\n"
        aid = await _create_artifact(artifact_manager, session_id, content)
        # 传 context=1000 → clamp 到 1 → 只展开 X 上下各 1 行
        result = await grep_tool(pattern="X", id=aid, context=1000)
        assert result.success
        assert "4:X" in result.data
        assert "3-c" in result.data and "5-d" in result.data
        # context=1 不应触及更远的行
        assert "1-a" not in result.data

    async def test_content_truncation_emits_hint(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """单 artifact 超 GREP_CONTENT_MAX_CHARS → 截断扫描量 + surface 'search incomplete'。"""
        monkeypatch.setattr(config, "GREP_CONTENT_MAX_CHARS", 20)
        # 前 20 字符内无命中,后段有 → 截断后 No matches,但必须带 incomplete 提示
        content = "x" * 30 + "NEEDLE\n"
        aid = await _create_artifact(artifact_manager, session_id, content)
        result = await grep_tool(pattern="NEEDLE", id=aid)
        assert result.success
        assert "No matches" in result.data
        assert "search incomplete" in result.data.lower()

    async def test_session_scan_budget_surfaces_incomplete(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """session 聚合扫描超 budget → summary surface 'not all content searched'。"""
        monkeypatch.setattr(config, "GREP_SESSION_SCAN_BUDGET_CHARS", 15)
        # 第一个 artifact 就吃掉预算并命中,第二个因预算耗尽不再扫描
        await _create_artifact(
            artifact_manager, session_id, "NEEDLE here padding\n", aid="a1"
        )
        await _create_artifact(
            artifact_manager, session_id, "NEEDLE also here\n", aid="a2"
        )
        result = await grep_tool(pattern="NEEDLE")
        assert result.success
        assert "not all content searched" in result.data

    async def test_session_budget_before_match_not_definitive_no_match(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Finding 3a:budget 在任何命中前耗尽 → 不能返回确定性 No matches
        (未搜的内容里可能有命中)。"""
        monkeypatch.setattr(config, "GREP_SESSION_SCAN_BUDGET_CHARS", 10)
        # NEEDLE 在第 10 字符之后 → 截断后扫不到 → No matches,但必须标 incomplete
        await _create_artifact(
            artifact_manager, session_id, "zzzzzzzzzzzzNEEDLE\n", aid="a1"
        )
        result = await grep_tool(pattern="NEEDLE")
        assert result.success
        assert "No matches" in result.data
        # 关键:不是确定性无命中,必须带 incomplete 提示
        assert "not all content searched" in result.data

    async def test_dense_same_line_matches_bounded_and_surfaced(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Finding 1:单行海量命中被 raw-match 上界拦住 + surface incomplete。
        无上界时 finditer 会被抽干(同步 CPU wedge)。"""
        monkeypatch.setattr(config, "GREP_MAX_SCAN_MATCHES", 100)
        content = "a" * 5000  # 单行 5000 个 'a' 命中,全 collapse 到 line 1
        aid = await _create_artifact(artifact_manager, session_id, content)
        start = time.perf_counter()
        result = await grep_tool(pattern="a", id=aid)
        assert time.perf_counter() - start < 1.0
        assert result.success
        # 单行 → 1 行级命中,但扫描提前触顶 → 必须 surface
        assert "search incomplete" in result.data.lower()

    async def test_single_huge_line_output_bounded(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Reviewer P2:单条巨行命中,输出 body 不再是整行 —— 交给引擎落盘前就封顶。"""
        monkeypatch.setattr(config, "GREP_MAX_LINE_CHARS", 100)
        monkeypatch.setattr(config, "GREP_MAX_SCAN_MATCHES", 100)  # 加速,免抽 100万命中
        content = "a" * 1_000_000  # 单行百万字符
        aid = await _create_artifact(artifact_manager, session_id, content)
        result = await grep_tool(pattern="a", id=aid)
        assert result.success
        # 改前 body ≈ 100万;现在远小于,命中行被截断 + 标记
        assert len(result.data) < 5000
        assert "line truncated" in result.data

    async def test_session_raw_budget_shared_across_artifacts(
        self,
        grep_tool: GrepArtifactTool,
        artifact_manager: ArtifactService,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Reviewer round 4:raw-match 预算 **per-tool-call 跨 artifact 累计共享**,不是
        每个 artifact 重置 —— 否则多个密集单行 artifact 累积无界 raw 迭代、同步 wedge
        事件循环(200 个 ≈86s)。预算被第一个 artifact 吃满后,后续不再扫 + surface。"""
        monkeypatch.setattr(config, "GREP_MAX_SCAN_MATCHES", 100)
        # 3 个单行各 200 个 'a' → 第一个就吃满 100 的 raw 预算
        for i in range(3):
            await _create_artifact(artifact_manager, session_id, "a" * 200, aid=f"d{i}")
        result = await grep_tool(pattern="a")
        assert result.success
        # per-call 共享:只第一个 artifact 被扫(若 per-artifact 重置则会扫满 3 个)
        assert "across 1 artifacts" in result.data
        assert "not all content searched" in result.data
