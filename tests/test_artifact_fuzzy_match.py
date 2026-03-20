"""
Artifact fuzzy match regression tests.

Extracted from real production logs (2026-03-19) where update_artifact
failed repeatedly. Two failure classes:

1. "Best match difference is too large" — model hallucinated adjacent lines
   that are actually separated by intermediate content.
2. "Failed to find matching text" — model hallucinated spaces between CJK
   and Latin/digit characters (e.g. "普恵 AI" vs "普恵AI").
"""

import pytest
from tools.builtin.artifact_ops import ArtifactMemory


# ============================================================
# Case 1: Non-adjacent lines (edit distance too large)
# ============================================================

# Actual artifact content (task_plan v2) — items 3 and 4 are NOT adjacent,
# separated by "候选文章列表" section.
TASK_PLAN_CONTENT = """\
# 任务：金融领域AI科技新闻稿件

## 任务流程
1. [✓] 搜索本月金融领域AI科技新闻 — search_agent — 已完成
2. [✓] 筛选4-5篇值得关注的文章 — lead_agent — 已完成
3. [✗] 爬取文章1 — crawl_agent — 执行中

## 候选文章列表
1. [✓] 保险业AI应用深化，分红险保底利率下调至1.25% — http://www.eeo.com.cn/2026/0310/808029.shtml
2. [✓] 科技保险新政发布，人保财险先行落地绿色算力保险 — http://www.eeo.com.cn/2026/0303/804555.shtml
3. [✓] Wind Alice智能金融助理上线：AI Agent重构商品决策逻辑 — https://m.163.com/dy/article/KN5U1AN005198RSU.html
4. [✓] 阿里发布三款中型千问3.5新模型，每百万Token低至0.2元 — https://cj.sina.com.cn/articles/view/1319475951/4ea59aef00101izxi?finpagefr=p_103

## 新闻稿件草稿
（撰写过程中逐步填充）
4. [✗] 撰写新闻稿部分1 — lead_agent — 待执行
5. [✗] 爬取文章2 — crawl_agent — 待执行
6. [✗] 撰写新闻稿部分2 — lead_agent — 待执行
7. [✗] 爬取文章3 — crawl_agent — 待执行
8. [✗] 撰写新闻稿部分3 — lead_agent — 待执行
9. [✗] 爬取文章4 — crawl_agent — 待执行
10. [✗] 撰写新闻稿部分4 — lead_agent — 待执行
11. [✗] 整合完成最终稿件 — lead_agent — 待执行

## 候选文章列表
（搜索完成后填写）

## 新闻稿件草稿
（撰写过程中逐步填充）"""


class TestCase1_NonAdjacentLines:
    """Model hallucinated items 3 and 4 as adjacent, but they're separated
    by a whole section. This is a model error, not an algorithm bug."""

    def setup_method(self):
        self.artifact = ArtifactMemory(
            artifact_id="task_plan",
            content_type="text/markdown",
            title="金融AI新闻稿件任务计划",
            content=TASK_PLAN_CONTENT,
        )

    def test_non_adjacent_lines_rejected(self):
        """Model sends old_str spanning non-adjacent lines → should fail."""
        old_str = (
            "3. [✗] 爬取文章1 — crawl_agent — 执行中\n"
            "4. [✗] 撰写新闻稿部分1 — lead_agent — 待执行"
        )
        new_str = (
            "3. [✓] 爬取文章1 — crawl_agent — 已完成\n"
            "4. [✓] 撰写新闻稿部分1 — lead_agent — 已完成\n"
            "5. [✗] 爬取文章2 — crawl_agent — 执行中"
        )
        success, msg, _, _ = self.artifact.compute_update(old_str, new_str)
        assert not success
        print(f"Correctly rejected: {msg}")

    def test_single_line_update_succeeds(self):
        """Correct approach: update one line at a time → should succeed."""
        old_str = "3. [✗] 爬取文章1 — crawl_agent — 执行中"
        new_str = "3. [✓] 爬取文章1 — crawl_agent — 已完成"
        success, msg, new_content, info = self.artifact.compute_update(old_str, new_str)
        assert success
        assert "3. [✓] 爬取文章1 — crawl_agent — 已完成" in new_content


# ============================================================
# Case 2: CJK-Latin space hallucination (Japanese text)
# ============================================================

# Actual artifact content (news_draft_ja v4, after Wind Alice citation removed).
# Note: NO spaces between CJK chars and Latin/digits.
JA_ARTICLE_CONTENT = """\
## アリババ千問3.5モデルがリリース、AI推論コストが新低に

2026年3月、アリババグループは「通義千問」シリーズの最新イテレーション製品である3つの中型「千問3.5」（Qwen3.5-Medium）大規模言語モデルを正式にリリースしました。このリリースは、アリババがモデル性能とコスト効率のバランスを取る上で重要なブレークスルーを達成したことを示し、金融業界およびその他の垂直分野におけるAIの規模化応用のためのコスト障壁を解消しました。

今回リリースされた3つの中型モデルのパラメータ数は100億レベルから1000億レベルの間で、それぞれ「汎用対話強化」「垂直業界分析（金融、医療など）」「長文処理」に対して特別に最適化されています。中型モデルと位置付けられていますが、千問3.5は論理的推論、コード生成、マルチモーダル理解能力において前世代と比較して著しく向上しており、一部のタスクのパフォーマンスは大型フラッグシップモデルに近づいています。

最も画期的なのは価格戦略です。アリババクラウドは、これら3つの新モデルの推論価格を100万トークンあたり0.2元人民幣に引き下げることを発表し、前世代の同様の製品と比較して約60%-70%削減されました。この「コスト效益革命」は、企業が大規模モデルを呼び出す敷居を大幅に下げ、AI応用が「試行」から「規模化応用」へと進むことを可能にしました。日活ユーザーが100万レベルの金融応用にとって、これは推論コストの指数関数的な低下を意味します。

金融業界の応用面では、このような高コストパフォーマンスモデルは銀行のスマートカスタマーサービス、保険の引受・請求支援、証券のスマート投資アドバイスなどの分野で広く応用される可能性があります。今月の保険業のAI深化傾向と組み合わせると、千問3.5は従来の大規模モデルのコストが高く、応答が遅いという痛点を解決し、保険会社がスマート請求、顧客プロファイルなどのリンクでより効率的な技術落地を実現するのに役立ちます。例えば、金融研究報告書分析シーンにおいて、新モデルは重要なデータと論理チェーンをより正確に抽出でき、投資研究効率を大幅に向上させることができます。

アリババはまた、ModelScope（魔搭）プラットフォームを通じて一部の重みをオープンソースし、主流フレームワークのデプロイをサポートし、開発者がこれに基づいて革新的な応用を構築することを奨励することを約束しました。この生態系開放戦略は、金融業界におけるAI技術の浸透をさらに加速させるでしょう。

この動きは国内の大規模モデル市場の競争構造を激化させ、業界全体を「普恵AI」の方向に推進しています。千問3.5シリーズの落地に伴い、アリババは「大小モデル協同」の生態系システムの構築を加速しています。大型モデルは複雑な決定を担当し、中型モデルは高頻度、低コストのタスクを担当します。今後半年以内に、100以上の業界応用が千問3.5 APIに接続し、AI技術が「クラウド実験」から真に「産業落地」へと進むと予想されています。

[出典｜アリババが3つの中型千問3.5新モデルをリリース、100万トークンあたり0.2元](https://cj.sina.com.cn/articles/view/1319475951/4ea59aef00101izxi?finpagefr=p_103)

---"""

# What the model sent — spaces inserted between CJK and Latin/digits
JA_MODEL_OLD_STR = (
    "この動きは国内の大規模モデル市場の競争構造を激化させ、業界全体を「普恵 AI」の方向に推進しています。"
    "千問 3.5 シリーズの落地に伴い、アリババは「大小モデル協同」の生態系システムの構築を加速しています。"
    "大型モデルは複雑な決定を担当し、中型モデルは高頻度、低コストのタスクを担当します。"
    "今後半年以内に、100 以上の業界応用が千問 3.5 API に接続し、"
    "AI 技術が「クラウド実験」から真に「産業落地」へと進むと予想されています。\n\n"
    "[出典｜アリババが 3 つの中型千問 3.5 新モデルをリリース、100 万トークンあたり 0.2 元]"
    "(https://cj.sina.com.cn/articles/view/1319475951/4ea59aef00101izxi?finpagefr=p_103)\n\n---"
)

JA_MODEL_NEW_STR = (
    "この動きは国内の大規模モデル市場の競争構造を激化させ、業界全体を「普恵 AI」の方向に推進しています。"
    "千問 3.5 シリーズの落地に伴い、アリババは「大小モデル協同」の生態系システムの構築を加速しています。"
    "大型モデルは複雑な決定を担当し、中型モデルは高頻度、低コストのタスクを担当します。"
    "今後半年以内に、100 以上の業界応用が千問 3.5 API に接続し、"
    "AI 技術が「クラウド実験」から真に「産業落地」へと進むと予想されています。\n\n---"
)

# Actual text in the artifact (no spaces between CJK and Latin/digits)
JA_ACTUAL_TEXT = (
    "この動きは国内の大規模モデル市場の競争構造を激化させ、業界全体を「普恵AI」の方向に推進しています。"
    "千問3.5シリーズの落地に伴い、アリババは「大小モデル協同」の生態系システムの構築を加速しています。"
    "大型モデルは複雑な決定を担当し、中型モデルは高頻度、低コストのタスクを担当します。"
    "今後半年以内に、100以上の業界応用が千問3.5 APIに接続し、"
    "AI技術が「クラウド実験」から真に「産業落地」へと進むと予想されています。\n\n"
    "[出典｜アリババが3つの中型千問3.5新モデルをリリース、100万トークンあたり0.2元]"
    "(https://cj.sina.com.cn/articles/view/1319475951/4ea59aef00101izxi?finpagefr=p_103)\n\n---"
)


class TestCase2_CJKLatinSpaceHallucination:
    """Model hallucinates spaces between CJK and Latin/digit chars.
    E.g. "普恵AI" → "普恵 AI", "千問3.5" → "千問 3.5".

    DMP match_main returns -1 even though the text is semantically identical.
    This is the more interesting case — the algorithm could potentially
    handle this if we normalize whitespace or lower the threshold."""

    def setup_method(self):
        self.artifact = ArtifactMemory(
            artifact_id="news_draft_ja",
            content_type="text/markdown",
            title="2026年3月AI金融技術ニュース（日本語版）",
            content=JA_ARTICLE_CONTENT,
        )

    def test_cjk_space_hallucination_now_succeeds(self):
        """CJK-Latin space diffs should be handled by normalized match."""
        success, msg, new_content, info = self.artifact.compute_update(
            JA_MODEL_OLD_STR, JA_MODEL_NEW_STR
        )
        assert success, f"Expected success but got: {msg}"
        assert info["match_type"] == "normalized"
        # Citation should be removed in the result
        assert "[出典｜アリババが3つの中型千問3.5新モデルをリリース" not in new_content
        print(f"Result: {msg}, similarity={info['similarity']:.1%}")

    def test_exact_text_succeeds(self):
        """Sanity check: using the actual text (no hallucinated spaces) works."""
        actual_new_str = (
            "この動きは国内の大規模モデル市場の競争構造を激化させ、業界全体を「普恵AI」の方向に推進しています。"
            "千問3.5シリーズの落地に伴い、アリババは「大小モデル協同」の生態系システムの構築を加速しています。"
            "大型モデルは複雑な決定を担当し、中型モデルは高頻度、低コストのタスクを担当します。"
            "今後半年以内に、100以上の業界応用が千問3.5 APIに接続し、"
            "AI技術が「クラウド実験」から真に「産業落地」へと進むと予想されています。\n\n---"
        )
        success, msg, new_content, info = self.artifact.compute_update(
            JA_ACTUAL_TEXT, actual_new_str
        )
        assert success
        assert "[出典｜" not in new_content  # citation removed

    def test_space_diff_count(self):
        """Show how many space diffs exist between model text and actual."""
        # Count the character-level differences
        import difflib
        s = difflib.SequenceMatcher(None, JA_MODEL_OLD_STR, JA_ACTUAL_TEXT)
        diffs = []
        for tag, i1, i2, j1, j2 in s.get_opcodes():
            if tag != 'equal':
                diffs.append((tag, JA_MODEL_OLD_STR[i1:i2], JA_ACTUAL_TEXT[j1:j2]))
        print(f"\nTotal diff regions: {len(diffs)}")
        for i, (tag, a, b) in enumerate(diffs):
            print(f"  [{i}] {tag}: '{a}' → '{b}'")

    def test_normalized_match_preserves_surrounding_content(self):
        """Ensure normalization match doesn't corrupt text outside the matched region."""
        success, msg, new_content, info = self.artifact.compute_update(
            JA_MODEL_OLD_STR, JA_MODEL_NEW_STR
        )
        assert success
        # The preceding paragraph should be untouched
        assert "ModelScope（魔搭）プラットフォーム" in new_content
        # The article header should be untouched
        assert "## アリババ千問3.5モデルがリリース" in new_content


# ============================================================
# Case 2b: Citation-only removal (simpler, from same session)
# ============================================================

# The successful v4 fuzzy match case (98.6% similarity) — for comparison
JA_WIND_ALICE_OLD_STR = """\
金融機関にとって、Wind Alice のオンラインは投資研究効率の著しい向上と決定ロジックのスマート化アップグレードを意味します。従業者にとって、AI Agent の使用をマスターすることが新たな核心的競争力となります。金融データターミナルがスマート決定パートナーへと加速的に転換するにつれて、従来の投資研究作業モードは深い変革を迎えるでしょう。

[出典｜Wind Alice スマート金融アシスタントがオンライン：AI Agent が商品決定ロジックを再構築](https://m.163.com/dy/article/KN5U1AN005198RSU.html)

---"""

JA_WIND_ALICE_ACTUAL = """\
金融機関にとって、Wind Aliceのオンラインは投資研究効率の著しい向上と決定ロジックのスマート化アップグレードを意味します。従業者にとって、AI Agentの使用をマスターすることが新たな核心的競争力となります。金融データターミナルがスマート決定パートナーへと加速的に転換するにつれて、従来の投資研究作業モードは深い変革を迎えるでしょう。

[出典｜Wind Aliceスマート金融アシスタントがオンライン：AI Agentが商品決定ロジックを再構築](https://m.163.com/dy/article/KN5U1AN005198RSU.html)

---"""


class TestCase2b_ShortCJKSpaceDiff:
    """Same type of CJK-Latin space hallucination but shorter text.
    This one SUCCEEDED at 98.6% in production — useful as baseline."""

    def setup_method(self):
        # Build a minimal artifact containing the Wind Alice section
        self.artifact = ArtifactMemory(
            artifact_id="news_draft_ja",
            content_type="text/markdown",
            title="Test",
            content=f"前文...\n\n{JA_WIND_ALICE_ACTUAL}\n\n後文...",
        )

    def test_short_space_diff_succeeds(self):
        """Shorter text with fewer space diffs → should succeed (98.6%)."""
        new_str = JA_WIND_ALICE_OLD_STR.replace(
            "\n[出典｜Wind Alice スマート金融アシスタントがオンライン：AI Agent が商品決定ロジックを再構築](https://m.163.com/dy/article/KN5U1AN005198RSU.html)\n",
            "\n"
        )
        success, msg, new_content, info = self.artifact.compute_update(
            JA_WIND_ALICE_OLD_STR, new_str
        )
        print(f"Result: success={success}, msg={msg}")
        if info:
            print(f"Similarity: {info.get('similarity', 'N/A')}")
        assert success


# ============================================================
# Index mapping correctness (reviewer regression cases)
# ============================================================

class TestIndexMapCorrectness:
    """Normalization must map positions back to the ORIGINAL text,
    not to an intermediate post-NFKC/rstrip string."""

    def test_rstrip_does_not_shift_indices(self):
        """Trailing spaces removed by rstrip must not shift the slice."""
        artifact = ArtifactMemory(
            artifact_id="test",
            content_type="text/markdown",
            title="Test",
            content="foo  \nbar\nqux",
        )
        success, msg, result, _ = artifact.compute_update("foo\nbar", "X")
        assert success
        assert result == "X\nqux", f"Got {result!r}, trailing spaces corrupted the slice"

    def test_nfkc_expansion_does_not_shift_indices(self):
        """NFKC expanding 1 char to 2 (Ⅳ→IV) must not shift the slice."""
        artifact = ArtifactMemory(
            artifact_id="test",
            content_type="text/markdown",
            title="Test",
            content="章节Ⅳ结束-后文",
        )
        success, msg, result, _ = artifact.compute_update("章节IV结束", "X")
        assert success
        assert result == "X-后文", f"Got {result!r}, NFKC expansion corrupted the slice"

    def test_combined_nfkc_and_rstrip(self):
        """Both NFKC expansion and trailing space removal in one update."""
        artifact = ArtifactMemory(
            artifact_id="test",
            content_type="text/markdown",
            title="Test",
            content="第Ⅲ章  \n内容\n后文",
        )
        success, msg, result, _ = artifact.compute_update("第III章\n内容", "X")
        assert success
        assert result == "X\n后文", f"Got {result!r}"


# ============================================================
# Layer 2: fuzzysearch fallback
# ============================================================

class TestFuzzysearchFallback:
    """Test that fuzzysearch Layer 2 catches cases that normalization misses."""

    def test_minor_typo_in_chinese(self):
        """A small character substitution that normalization can't fix."""
        content = "这是一段关于人工智能技术的详细介绍，包含了最新的研究进展和应用前景。"
        artifact = ArtifactMemory(
            artifact_id="test",
            content_type="text/markdown",
            title="Test",
            content=content,
        )
        # Model sends slightly wrong text (技术 swapped to 枝术)
        old_str = "关于人工智能枝术的详细介绍"
        new_str = "关于人工智能技术的简要概述"
        success, msg, new_content, info = artifact.compute_update(old_str, new_str)
        assert success
        assert info["match_type"] == "fuzzy"
        assert "简要概述" in new_content
        print(f"Result: {msg}")

    def test_rejects_completely_wrong_text(self):
        """Totally unrelated old_str should still be rejected."""
        artifact = ArtifactMemory(
            artifact_id="test",
            content_type="text/markdown",
            title="Test",
            content="Hello world, this is a test document about Python programming.",
        )
        success, msg, _, _ = artifact.compute_update(
            "これは完全に無関係なテキストです", "replacement"
        )
        assert not success


# ============================================================
# Normalization rules: smart quotes, dashes, special spaces
# ============================================================

class TestNormalizationRules:
    """Test that smart quotes, Unicode dashes, and special spaces
    are normalized before matching (Pi-mono parity)."""

    def test_smart_quotes(self):
        """LLM sends curly quotes, artifact has straight quotes."""
        artifact = ArtifactMemory(
            artifact_id="test",
            content_type="text/markdown",
            title="Test",
            content='He said "hello" and she replied \'world\'.',
        )
        # Model sends smart/curly quotes
        old_str = 'He said \u201chello\u201d and she replied \u2018world\u2019.'
        new_str = 'They greeted each other.'
        success, msg, new_content, info = artifact.compute_update(old_str, new_str)
        assert success
        assert info["match_type"] == "normalized"
        assert "They greeted each other." in new_content

    def test_unicode_dashes(self):
        """LLM sends em dash, artifact has ASCII hyphen."""
        artifact = ArtifactMemory(
            artifact_id="test",
            content_type="text/markdown",
            title="Test",
            content="AI - the future of technology - is here.",
        )
        # Model sends em dashes
        old_str = "AI \u2014 the future of technology \u2014 is here."
        new_str = "AI is here now."
        success, msg, new_content, info = artifact.compute_update(old_str, new_str)
        assert success
        assert info["match_type"] == "normalized"
        assert "AI is here now." in new_content

    def test_non_breaking_space(self):
        """LLM sends non-breaking space, artifact has regular space."""
        artifact = ArtifactMemory(
            artifact_id="test",
            content_type="text/markdown",
            title="Test",
            content="100 million tokens cost 0.2 yuan.",
        )
        # Model sends non-breaking spaces
        old_str = "100\u00a0million tokens cost\u00a00.2\u00a0yuan."
        new_str = "Tokens are cheap."
        success, msg, new_content, info = artifact.compute_update(old_str, new_str)
        assert success
        assert info["match_type"] == "normalized"

    def test_mixed_normalization(self):
        """Multiple normalization issues in one old_str."""
        artifact = ArtifactMemory(
            artifact_id="test",
            content_type="text/markdown",
            title="Test",
            content='研究报告 - "AI技术" 的应用前景非常广阔。',
        )
        # Model: em dash + smart quotes + CJK-Latin space
        old_str = '研究报告 \u2014 \u201cAI 技术\u201d 的应用前景非常广阔。'
        new_str = '结论：前景广阔。'
        success, msg, new_content, info = artifact.compute_update(old_str, new_str)
        assert success
        assert info["match_type"] == "normalized"
        assert "结论：前景广阔。" in new_content
