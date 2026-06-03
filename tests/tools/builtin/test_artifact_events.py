"""ArtifactService SSE-only ARTIFACT_* event contract.

Covers: (1) create/rewrite/update emit the right payloads when emit is bound;
(2) the span delta reconstructs new content; (3) REST-style (no emit bound)
stays silent; (4) the live-content cap omits oversized bodies.

Note: the old "drift guard" test is gone — the event-type values now derive
directly from StreamEventType via the deferred-import accessors
(_evt_artifact_created/_evt_artifact_updated), so literal drift is structurally
impossible rather than test-enforced.
"""

import uuid

import pytest

from core.events import StreamEventType
from db.models import User
from repositories.artifact_repo import ArtifactRepository
from repositories.conversation_repo import ConversationRepository
from tools.builtin.artifact_service import ArtifactService

# 本测试用枚举值断言已发事件类型;Service 内部经 _evt_artifact_* 访问器(延迟 import)
# 取的就是这两个同源值。
_EVT_ARTIFACT_CREATED = StreamEventType.ARTIFACT_CREATED.value
_EVT_ARTIFACT_UPDATED = StreamEventType.ARTIFACT_UPDATED.value


@pytest.fixture
async def session_id(conversation_repo: ConversationRepository, test_user: User) -> str:
    conv_id = f"conv-{uuid.uuid4().hex}"
    await conversation_repo.create_conversation(conversation_id=conv_id, user_id=test_user.id)
    return conv_id


@pytest.fixture
def service(artifact_repo: ArtifactRepository) -> ArtifactService:
    return ArtifactService(artifact_repo)


class _Recorder:
    """Stands in for the engine's _emit closure: (event_type, agent, data, *, sse_only)."""

    def __init__(self):
        self.events = []

    async def __call__(self, event_type, agent=None, data=None, *, sse_only=False):
        self.events.append({"type": event_type, "agent": agent, "data": data, "sse_only": sse_only})

    def of(self, event_type):
        return [e for e in self.events if e["type"] == event_type]


class TestEmitBehavior:
    async def test_no_emit_when_unbound(self, service: ArtifactService, session_id: str):
        """REST-style Service (emit unbound) emits nothing."""
        service.set_session(session_id)
        await service.create_artifact(session_id, "a1", "text/markdown", "T", "hello")
        # emit never bound → _emit is None → no crash, nothing recorded anywhere.
        assert service._emit is None

    async def test_create_emits_full_content(self, service: ArtifactService, session_id: str):
        service.set_session(session_id)
        rec = _Recorder()
        service.bind_emit(rec)
        await service.create_artifact(session_id, "doc", "text/markdown", "Doc", "hello world")
        created = rec.of(_EVT_ARTIFACT_CREATED)
        assert len(created) == 1
        e = created[0]
        assert e["sse_only"] is True          # never persisted
        assert e["data"]["id"] == "doc"
        assert e["data"]["current_version"] == 1
        assert e["data"]["content"] == "hello world"

    async def test_rewrite_emits_full_content(self, service: ArtifactService, session_id: str):
        service.set_session(session_id)
        await service.create_artifact(session_id, "doc", "text/markdown", "Doc", "v1 body")
        rec = _Recorder()
        service.bind_emit(rec)
        await service.rewrite_artifact(session_id, "doc", "completely new body")
        updated = rec.of(_EVT_ARTIFACT_UPDATED)
        assert len(updated) == 1
        assert updated[0]["data"]["content"] == "completely new body"
        assert updated[0]["data"]["current_version"] == 2
        assert "delta" not in updated[0]["data"]

    async def test_update_emits_span_delta_that_reconstructs(self, service: ArtifactService, session_id: str):
        # bind BEFORE create (real loop order: bind_emit at loop start) so the
        # create establishes the frontend base and the subsequent update is a delta.
        original = "alpha beta gamma"
        service.set_session(session_id)
        rec = _Recorder()
        service.bind_emit(rec)
        await service.create_artifact(session_id, "doc", "text/markdown", "Doc", original)
        ok, _, _ = await service.update_artifact(session_id, "doc", "beta", "BETA")
        assert ok
        e = rec.of(_EVT_ARTIFACT_UPDATED)[0]
        delta = e["data"]["delta"]
        # frontend applies: replace [offset, offset+deleted_len) with inserted_text
        rebuilt = (
            original[: delta["offset"]]
            + delta["inserted_text"]
            + original[delta["offset"] + delta["deleted_len"]:]
        )
        assert rebuilt == "alpha BETA gamma"
        assert e["data"]["current_version"] == 2

    async def test_first_update_of_preexisting_sends_full_content_not_delta(
        self, service: ArtifactService, session_id: str
    ):
        """A pre-existing artifact (created/flushed a prior turn → not seen this
        turn's live stream) must get FULL content on its first update, so the
        frontend has a base before any delta (no mid-turn DB query)."""
        # Simulate prior-turn artifact: create + flush, then a FRESH turn starts.
        service.set_session(session_id)
        await service.create_artifact(session_id, "doc", "text/markdown", "Doc", "alpha beta gamma")
        await service.flush_all(session_id)
        # New turn: bind emit (resets base tracking), then the agent's FIRST touch
        # is an update on this pre-existing artifact.
        rec = _Recorder()
        service.bind_emit(rec)
        ok, _, _ = await service.update_artifact(session_id, "doc", "beta", "BETA")
        assert ok
        e = rec.of(_EVT_ARTIFACT_UPDATED)[0]
        assert "delta" not in e["data"]          # no base yet → full content
        assert e["data"]["content"] == "alpha BETA gamma"
        # A SECOND update now has a base → delta.
        ok2, _, _ = await service.update_artifact(session_id, "doc", "gamma", "GAMMA")
        assert ok2
        e2 = rec.of(_EVT_ARTIFACT_UPDATED)[1]
        assert "delta" in e2["data"]

    async def test_upload_staging_emits_created_and_defers_persist(
        self, service: ArtifactService, session_id: str, artifact_repo
    ):
        """create_from_upload now STAGES (no immediate commit): emits
        ARTIFACT_CREATED (source=user_upload), persists only on flush_all."""
        service.set_session(session_id)
        rec = _Recorder()
        service.bind_emit(rec)
        ok, _, info = await service.create_from_upload(
            session_id, "Report.md", "uploaded body", "text/markdown",
        )
        assert ok
        e = rec.of(_EVT_ARTIFACT_CREATED)[0]
        assert e["data"]["source"] == "user_upload"
        assert e["data"]["content"] == "uploaded body"
        # not in DB yet (staged only)
        assert await artifact_repo.get_artifact(session_id, info["id"]) is None
        await service.flush_all(session_id)
        assert await artifact_repo.get_artifact(session_id, info["id"]) is not None

    async def test_upload_dedups_within_workingset(self, service: ArtifactService, session_id: str):
        """Two same-name uploads in one turn dedup in the WorkingSet (not just DB):
        the second gets an _N id even though the first hasn't flushed."""
        service.set_session(session_id)
        _, _, a = await service.create_from_upload(session_id, "doc.md", "one", "text/markdown")
        _, _, b = await service.create_from_upload(session_id, "doc.md", "two", "text/markdown")
        assert a["id"] != b["id"]

    async def test_oversized_create_omits_content(self, service: ArtifactService, session_id: str, monkeypatch):
        from config import config
        monkeypatch.setattr(config, "ARTIFACT_LIVE_CONTENT_MAX_CHARS", 10)
        service.set_session(session_id)
        rec = _Recorder()
        service.bind_emit(rec)
        await service.create_artifact(session_id, "big", "text/markdown", "Big", "x" * 50)
        e = rec.of(_EVT_ARTIFACT_CREATED)[0]
        assert e["data"].get("content_omitted") is True
        assert "content" not in e["data"]
