"""Algorithm-level and tool-boundary tests for ``update_artifact``.

TestAlgorithm exercises ``compute_update`` / ``find_fuzzy_match`` directly
(no manager / session). TestToolBoundary uses a minimal fake
``ArtifactManager`` to lock the tool's ``ToolResult.metadata`` contract.
"""

import time
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest
from rapidfuzz.distance import Levenshtein

from config import config
from tools.base import ToolResult
from tools.builtin.update_artifact import (
    FuzzyBail,
    FuzzyMatch,
    MatchInfo,
    UpdateArtifactTool,
    compute_update,
    find_fuzzy_match,
)


# ============================================================
# TestAlgorithm — pure ``compute_update`` / ``find_fuzzy_match``
# ============================================================


class TestAlgorithm:
    """v6 spec checklist + Layer 0/1 dispatch + fuzzy_stats completeness."""

    # ---- Layer dispatch ----

    def test_layer0_exact_hit(self):
        info = compute_update("hello world", "world", "WORLD")
        assert info.success
        assert info.match_type == "exact"
        assert info.new_content == "hello WORLD"
        assert info.fuzzy_stats is None  # Layer 2 did not run

    def test_layer0_rejects_non_unique(self):
        info = compute_update("aaa BANANA bbb BANANA ccc", "BANANA", "X")
        assert not info.success
        assert "appears 2 times" in info.message

    def test_layer1_normalized_hit(self):
        # NFKC: Ⅳ → IV
        info = compute_update("章节Ⅳ结束", "章节IV结束", "X")
        assert info.success
        assert info.match_type == "normalized"
        assert info.new_content == "X"
        assert info.fuzzy_stats is None  # Layer 2 did not run

    # ---- span delta contract (offset / deleted_len) ----
    # The matched span is the authoritative source for ARTIFACT_UPDATED deltas.
    # Core invariant the frontend relies on: replacing [offset, offset+deleted_len)
    # in the ORIGINAL content with new_str reproduces new_content exactly.

    @staticmethod
    def _assert_span_reconstructs(content: str, old: str, new: str):
        info = compute_update(content, old, new)
        assert info.success
        assert info.offset is not None and info.deleted_len is not None
        reconstructed = (
            content[: info.offset] + new + content[info.offset + info.deleted_len:]
        )
        assert reconstructed == info.new_content
        return info

    def test_span_layer0_exact(self):
        info = self._assert_span_reconstructs("hello world", "world", "WORLD")
        assert info.match_type == "exact"
        assert (info.offset, info.deleted_len) == (6, 5)

    def test_span_layer0_first_of_unique(self):
        # leading context so offset != 0 — guards against a hard-coded 0
        info = self._assert_span_reconstructs("aaa TARGET bbb", "TARGET", "X")
        assert info.offset == 4

    def test_span_layer1_normalized(self):
        # NFKC Ⅳ→IV: matched span is in ORIGINAL coords (the single char Ⅳ)
        info = self._assert_span_reconstructs("章节Ⅳ结束", "章节IV结束", "X")
        assert info.match_type == "normalized"

    def test_span_layer2_fuzzy(self):
        content = "这是一段关于人工智能技术的详细介绍。"
        info = self._assert_span_reconstructs(content, "关于人工智能枝术的详细介绍", "替换后")
        assert info.match_type == "fuzzy"
        # span maps to the matched (correct-spelling) region in the original
        assert content[info.offset: info.offset + info.deleted_len] == info.matched_text

    def test_span_absent_on_failure(self):
        info = compute_update("aaa BANANA bbb BANANA ccc", "BANANA", "X")
        assert not info.success
        assert info.offset is None and info.deleted_len is None

    # ---- Layer 2 v6: 'matched' path ----

    def test_layer2_single_char_substitution_m13(self):
        """Real-log case: m=13, Lev=1 (枝 → 技). allowed_dist clamps to 1."""
        content = "这是一段关于人工智能技术的详细介绍。"
        old_str = "关于人工智能枝术的详细介绍"  # m=13, one char wrong
        info = compute_update(content, old_str, "替换后")
        assert info.success
        assert info.match_type == "fuzzy"
        assert info.fuzzy_stats["k"] == 1
        assert info.fuzzy_stats["distance"] == 1
        assert info.fuzzy_stats["outcome"] == "matched"
        assert "替换后" in info.new_content

    def test_layer2_short_old_str_ratio_cap_rejects(self):
        """m=13 with Lev=3 must fail because ratio cap pins k=1, not 16."""
        # Force Layer 2 by making Layer 0/1 miss
        content = "这是一段关于人工智能技术的详细介绍。"
        # 3 substitutions: 关→Q, 工→Q, 术→Q
        old_str = "Q于人Q智能技Q的详细介绍"  # m=13, ~3 substitutions vs content
        info = compute_update(content, old_str, "X")
        assert not info.success
        # k must have clamped to 1
        if info.fuzzy_stats:
            assert info.fuzzy_stats["k"] == 1
            assert info.fuzzy_stats["outcome"].startswith("bail_")

    # ---- RapidFuzz cutoff contract regression ----

    def test_rapidfuzz_cutoff_contract_returns_k_plus_one(self):
        """If RapidFuzz ever changes to return None above cutoff, our `<= k`
        guard would silently let everything through. Lock the contract."""
        d = Levenshtein.distance("abc", "xyz", score_cutoff=2)
        assert d is not None, "RapidFuzz contract changed: it now returns None above cutoff"
        assert d == 3, f"Expected k+1=3, got {d}"

    # ---- v5 invariant: full center expansion, no top-N gating ----

    def test_v5_all_rare_shingles_expanded(self):
        """Pigeonhole does not promise the surviving shingle ranks first by
        rarity, so v5 expands ALL rare shingles instead of picking top-N.
        Use varied English so anchors are unique throughout (otherwise tied
        alignments would mask the property under test)."""
        old_str = (
            "The quick brown fox jumps over the lazy dog "
            "and runs away into the deep dark forest beyond."
        )
        # Single-char typo in the middle so Layer 0/1 miss.
        bad_old = old_str.replace("jumps", "jumqs", 1)
        content = "Before. " + old_str + " After."
        info = compute_update(content, bad_old, "REPL")
        assert info.success, info.message
        assert info.match_type == "fuzzy"
        assert info.fuzzy_stats["distance"] == 1

    # ---- Hard input-size cap ----

    def test_oversize_old_str_bails_before_step1(self):
        """Input length > MAX_FUZZY_OLD_STR_LEN must bail BEFORE Step 1 — at
        m ≈ 400K, Python-side Step 1-3 alone exceeds the wall-clock budget
        and the deadline guard cannot interrupt mid-build. Bail must fire
        in O(1) time regardless of m."""
        import time as _time

        big_m = config.MAX_FUZZY_OLD_STR_LEN + 10000
        oversize = "a" * big_m
        t0 = _time.perf_counter()
        info = compute_update("short content", oversize, "X")
        elapsed_ms = (_time.perf_counter() - t0) * 1000

        assert not info.success
        assert info.fuzzy_stats["outcome"] == "bail_budget"
        # Must not have done any algorithmic work
        assert info.fuzzy_stats["rare_shingles"] == 0
        assert info.fuzzy_stats["raw_centers"] == 0
        # Should be near-instant (no shingle build, no scan, no sort)
        assert elapsed_ms < 10, f"oversize gate took {elapsed_ms:.1f}ms"

    # ---- Step 3 budget bail ----

    def test_center_budget_exceeded_bails(self):
        """60 distinct anchors spread > allowed_dist apart → > MAX_UNIQUE_CENTERS."""
        n = config.MAX_UNIQUE_CENTERS + 10  # 60
        anchors = [f"ANCH{i:02d}" for i in range(n)]  # 6 chars, no low-info shingles
        old_str = "_".join(anchors)  # ~419 chars
        spacer = "x" * 250  # >> allowed_dist (= 16)
        content = spacer.join(anchors) + spacer

        info = compute_update(content, old_str, "REPL")
        assert not info.success
        assert info.fuzzy_stats["outcome"] == "bail_budget"
        assert info.fuzzy_stats["unique_centers"] > config.MAX_UNIQUE_CENTERS

    # ---- Step 4 wall-clock deadline bail ----

    def test_wall_clock_deadline_bails(self, monkeypatch):
        """Tighten the budget to 50 ms and mock each Levenshtein call to
        ~12 ms — bail triggers after a handful of calls. Keeps test runtime
        small while proving the inner-loop deadline check actually fires."""
        monkeypatch.setattr(config, "MAX_FUZZY_WALL_CLOCK_MS", 50)

        real_dist = Levenshtein.distance

        def slow_dist(s1, s2, score_cutoff=None):
            time.sleep(0.012)
            return real_dist(s1, s2, score_cutoff=score_cutoff)

        monkeypatch.setattr(
            "tools.builtin.update_artifact.Levenshtein",
            MagicMock(distance=slow_dist),
        )

        # Layer 0/1 must miss → introduce typo in a unique-anchor region.
        # Anchors at prefix/suffix ensure a single rare center survives Step 3
        # so Step 4 enters with one center × many (2k+1)² verify calls.
        prefix = "UNIQ_PFX_RARE_TAG_"
        middle = "abcdefghij" * 16  # 160 chars
        typo_middle = middle[:80] + "X" + middle[81:]
        suffix = "_UNIQ_SFX_RARE_TAG"
        old_str = prefix + typo_middle + suffix  # m ≈ 196
        content = "lead " + prefix + middle + suffix + " tail"

        t0 = time.monotonic()
        info = compute_update(content, old_str, "REPL")
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert not info.success
        assert info.fuzzy_stats["outcome"] == "bail_deadline"
        assert info.fuzzy_stats["verify_calls"] > 0
        # Sanity: actually exceeded the patched budget (otherwise test is stale)
        assert elapsed_ms >= config.MAX_FUZZY_WALL_CLOCK_MS * 0.9

    # ---- Preamble low-entropy bail ----

    def test_low_entropy_bails(self, monkeypatch):
        """Force L < ANCHOR_MIN_USABLE_LEN by widening ratio cap."""
        monkeypatch.setattr(config, "FUZZY_MAX_RATIO", 0.5)
        # m=11, k=5, L = min(6, 11//6) = 1 < 3 → bail
        info = compute_update("hello world test data", "hellxx test", "X")
        assert not info.success
        assert info.fuzzy_stats["outcome"] == "bail_low_entropy"
        assert info.fuzzy_stats["L"] < config.ANCHOR_MIN_USABLE_LEN

    # ---- bail_no_anchor (no rare shingle in content) ----

    def test_no_anchor_bails_when_completely_unrelated(self):
        info = compute_update(
            "Hello world test document about Python programming.",
            "これは完全に無関係なテキストです",  # m=15, no Latin shingles in content
            "REPL",
        )
        assert not info.success
        assert info.fuzzy_stats["outcome"] == "bail_no_anchor"

    # ---- bail_ambiguous (multiple distinct regions) ----

    def test_ambiguous_distinct_regions_fail(self):
        # Two copies of "AMBIGUOUS_TARGET" 18 chars apart, old_str has 1 typo.
        content = "before AMBIGUOUS_TARGET after AMBIGUOUS_TARGET end"
        old_str = "AMBIGUOUS_TARGEX"  # m=16, k=1
        info = compute_update(content, old_str, "X")
        assert not info.success
        assert info.fuzzy_stats["outcome"] == "bail_ambiguous"

    def test_ambiguous_same_center_tied_spans_fail(self):
        """Reviewer reproducer: same center, two distinct (ms, me) tied at
        d=1. With a silent tie-break we'd eat a prefix character; with the
        ``span_tied`` flag we bail loudly. Different (ms, me) → different
        new_content, so we cannot pick one without misleading the model.

        Concretely: (ms=1, me=14) replaces "ZabcdefABCDEF" → "ZRYY";
                    (ms=2, me=14) replaces "abcdefABCDEF" → "ZZRYY".
        """
        info = compute_update("ZZabcdefABCDEFYY", "XabcdefABCDEF", "R")
        assert not info.success
        assert info.fuzzy_stats["outcome"] == "bail_ambiguous"
        # Lock the invariant: a silent tie-break would have produced one of
        # these — assert we did NOT.
        assert info.new_content is None

    # ---- bail_no_window (rare anchors exist but Lev too high) ----

    def test_no_window_bails_when_lev_above_cutoff(self):
        """Anchor matches, but the surrounding text differs far past k."""
        content = "AAA RARE_ANCHOR_FOO XXXXXXXXXXXX YYYYY"
        # Pad old_str with stuff that does NOT appear after the anchor in content
        old_str = "RARE_ANCHOR_FOO" + "Z" * 30  # m=45, k=4
        info = compute_update(content, old_str, "REPL")
        assert not info.success
        assert info.fuzzy_stats["outcome"] == "bail_no_window"

    # ---- Long old_str fast verify ----

    def test_long_old_str_verify_under_100ms(self):
        """1500+ char old_str with 1 typo near middle. Pseudo-random text
        keeps anchors unique throughout (repetitive middles would create
        tied alignments and trip bail_ambiguous, masking the timing claim).
        """
        import random

        rng = random.Random(42)
        # Single token of pseudo-random letters + space so shingles vary.
        body = "".join(rng.choices("abcdefghijklmnopqrstuvwxyz ", k=1600))
        # Inject typo at the middle so Layer 0/1 miss.
        typo_pos = 800
        old_str = body[:typo_pos] + "X" + body[typo_pos + 1:]
        content = "lead " + body + " tail"

        t0 = time.monotonic()
        info = compute_update(content, old_str, "REPL")
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert info.success, info.message
        assert info.match_type == "fuzzy"
        assert elapsed_ms < 100, f"verify took {elapsed_ms:.1f}ms"

    # ---- fuzzy_stats payload contract (TestAlgorithm scope) ----

    BASE_STAT_FIELDS = {
        "m", "n", "k", "L",
        "rare_shingles", "raw_centers", "unique_centers",
        "verify_calls", "elapsed_ms",
        "outcome", "old_str_hash",
    }

    def _assert_base_stats(self, stats: Dict[str, Any], expected_outcome: str) -> None:
        missing = self.BASE_STAT_FIELDS - set(stats.keys())
        assert not missing, f"Missing fuzzy_stats fields: {missing}"
        assert stats["outcome"] == expected_outcome
        assert stats["old_str_hash"].startswith("sha256:")

    def test_fuzzy_stats_completeness_matched(self):
        content = "这是一段关于人工智能技术的详细介绍。"
        info = compute_update(content, "关于人工智能枝术的详细介绍", "X")
        self._assert_base_stats(info.fuzzy_stats, "matched")
        # matched-only fields
        assert "distance" in info.fuzzy_stats
        assert "similarity_pct" in info.fuzzy_stats

    def test_fuzzy_stats_completeness_bail_low_entropy(self, monkeypatch):
        monkeypatch.setattr(config, "FUZZY_MAX_RATIO", 0.5)
        info = compute_update("hello world test data", "hellxx test", "X")
        self._assert_base_stats(info.fuzzy_stats, "bail_low_entropy")
        # bail paths must NOT carry distance / similarity_pct
        assert "distance" not in info.fuzzy_stats
        assert "similarity_pct" not in info.fuzzy_stats

    def test_fuzzy_stats_completeness_bail_no_anchor(self):
        info = compute_update("English text only here.", "完全不同的中文文本", "X")
        self._assert_base_stats(info.fuzzy_stats, "bail_no_anchor")
        assert "distance" not in info.fuzzy_stats

    def test_fuzzy_stats_completeness_bail_budget(self):
        n = config.MAX_UNIQUE_CENTERS + 10
        anchors = [f"ANCH{i:02d}" for i in range(n)]
        old_str = "_".join(anchors)
        spacer = "x" * 250
        content = spacer.join(anchors) + spacer
        info = compute_update(content, old_str, "REPL")
        self._assert_base_stats(info.fuzzy_stats, "bail_budget")

    def test_fuzzy_stats_completeness_bail_ambiguous(self):
        content = "before AMBIGUOUS_TARGET after AMBIGUOUS_TARGET end"
        info = compute_update(content, "AMBIGUOUS_TARGEX", "X")
        self._assert_base_stats(info.fuzzy_stats, "bail_ambiguous")

    def test_fuzzy_stats_completeness_bail_no_window(self):
        content = "AAA RARE_ANCHOR_FOO XXXXXXXXXXXX YYYYY"
        old_str = "RARE_ANCHOR_FOO" + "Z" * 30
        info = compute_update(content, old_str, "REPL")
        self._assert_base_stats(info.fuzzy_stats, "bail_no_window")

    def test_fuzzy_stats_never_contains_old_str_raw(self):
        """Privacy invariant: only the sha256 hash leaks, never raw text."""
        secret_old = "SUPER_SECRET_PHRASE_12345"
        info = compute_update("unrelated content", secret_old, "X")
        assert info.fuzzy_stats is not None
        flat = repr(info.fuzzy_stats)
        assert secret_old not in flat
        assert "old_str_hash" in info.fuzzy_stats


# ============================================================
# TestToolBoundary — minimal fake ArtifactManager
# ============================================================


class TestToolBoundary:
    """Lock the tool layer's metadata transport + XML shape.

    The fake manager mirrors three real attributes that ``UpdateArtifactTool``
    actually touches — if any of those are renamed / restructured, these tests
    should break visibly (not silently).
    """

    @staticmethod
    def _make_fake_manager(match_info: Dict[str, Any]):
        fake = MagicMock(name="ArtifactManager")
        fake.current_session_id = "test-session"
        fake.update_artifact = AsyncMock(
            return_value=(True, "Successfully updated artifact 'aid' (v2)", match_info)
        )
        memory_stub = MagicMock()
        memory_stub.current_version = 2
        fake.get_artifact = AsyncMock(return_value=memory_stub)
        return fake

    @pytest.mark.asyncio
    async def test_fuzzy_stats_passthrough_identity(self):
        """``ToolResult.metadata['fuzzy_stats']`` MUST be the same object the
        manager returned — no copy, no field rewrap. Locks the contract that
        feeds MessageEvent.data->metadata->fuzzy_stats end-to-end."""
        fuzzy_stats = {
            "m": 13, "n": 30, "k": 1, "L": 6,
            "rare_shingles": 2, "raw_centers": 2, "unique_centers": 1,
            "verify_calls": 7, "elapsed_ms": 1,
            "outcome": "matched", "old_str_hash": "sha256:abc",
            "distance": 1, "similarity_pct": 92.3,
        }
        match_info = {
            "match_type": "fuzzy",
            "similarity": 0.923,
            "expected_text": "old text",
            "matched_text": "old text fixed",
            "changes": [("old text fixed", "new")],
            "fuzzy_stats": fuzzy_stats,
        }
        fake = self._make_fake_manager(match_info)
        tool = UpdateArtifactTool(fake)
        result = await tool.execute(id="aid", old_str="x", new_str="y")
        assert result.success
        assert result.metadata["fuzzy_stats"] is fuzzy_stats  # identity, not equality

    @pytest.mark.asyncio
    async def test_normalize_detail_block_in_xml(self):
        """Layer 1 hit → ``<normalize_detail>`` + ``normalized="X%"`` attr."""
        match_info = {
            "match_type": "normalized",
            "similarity": 0.985,
            "expected_text": "expected text here",
            "matched_text": "matched text here",
            "changes": [("matched text here", "new")],
        }
        fake = self._make_fake_manager(match_info)
        tool = UpdateArtifactTool(fake)
        result = await tool.execute(id="aid", old_str="x", new_str="y")
        assert result.success
        assert 'normalized="98.5%"' in result.data
        assert "<normalize_detail>" in result.data
        assert "<expected>expected text here</expected>" in result.data
        assert "<matched>matched text here</matched>" in result.data
        # No fuzzy artifacts in a normalized hit
        assert "fuzzy=" not in result.data
        assert "<fuzzy_detail>" not in result.data

    @pytest.mark.asyncio
    async def test_fuzzy_detail_block_in_xml(self):
        """Layer 2 hit → ``<fuzzy_detail>`` + ``fuzzy="X%"`` attr."""
        match_info = {
            "match_type": "fuzzy",
            "similarity": 0.923,
            "expected_text": "expected text",
            "matched_text": "matched text",
            "changes": [("matched text", "new")],
            "fuzzy_stats": {"outcome": "matched"},
        }
        fake = self._make_fake_manager(match_info)
        tool = UpdateArtifactTool(fake)
        result = await tool.execute(id="aid", old_str="x", new_str="y")
        assert result.success
        assert 'fuzzy="92.3%"' in result.data
        assert "<fuzzy_detail>" in result.data
        # Mutually exclusive with normalize block
        assert "normalized=" not in result.data
        assert "<normalize_detail>" not in result.data

    @pytest.mark.asyncio
    async def test_exact_match_no_diff_block(self):
        """Layer 0 hit → plain ``<artifact>`` envelope, no diff block."""
        match_info = {
            "match_type": "exact",
            "similarity": 1.0,
            "changes": [("old", "new")],
        }
        fake = self._make_fake_manager(match_info)
        tool = UpdateArtifactTool(fake)
        result = await tool.execute(id="aid", old_str="x", new_str="y")
        assert result.success
        assert "<normalize_detail>" not in result.data
        assert "<fuzzy_detail>" not in result.data
        assert "normalized=" not in result.data
        assert "fuzzy=" not in result.data

    @pytest.mark.asyncio
    async def test_failure_propagates_fuzzy_stats_metadata(self):
        """On Layer 2 bail, the tool must surface ``fuzzy_stats`` in metadata
        even though the call failed — observability needs the bail reason."""
        bail_stats = {
            "m": 13, "n": 30, "k": 1, "L": 6,
            "rare_shingles": 0, "raw_centers": 0, "unique_centers": 0,
            "verify_calls": 0, "elapsed_ms": 1,
            "outcome": "bail_no_anchor", "old_str_hash": "sha256:abc",
        }
        fake = MagicMock(name="ArtifactManager")
        fake.current_session_id = "test-session"
        fake.update_artifact = AsyncMock(
            return_value=(False, "no anchor", {"fuzzy_stats": bail_stats})
        )
        fake.get_artifact = AsyncMock()
        tool = UpdateArtifactTool(fake)
        result = await tool.execute(id="aid", old_str="x", new_str="y")
        assert not result.success
        assert result.metadata["fuzzy_stats"] is bail_stats
