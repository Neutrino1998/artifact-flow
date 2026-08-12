"""Conversation admission, runtime commands, and supervised turn composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Sequence
from uuid import uuid4

from config import config
from api.services.conversation_turn_factory import (
    create_turn_handler,
    run_and_push,
    sanitize_error_event,
)
from api.services.conversation_lease import (
    ConversationLeaseConflict,
    ConversationLeaseCoordinator,
    ConversationLeaseHandle,
    ConversationLeaseLost,
    ConversationLeaseUnavailable,
)
from api.services.runtime_store import RuntimeStore
from api.services.stream_transport import StreamTransport
from core.conversation_manager import ConversationManager
from core.events import StreamEventType
from core.task_supervisor import TaskQueued, TaskScope, TaskSupervisor
from repositories.base import NotFoundError
from repositories.conversation_repo import ConversationRepository
from utils.logger import get_logger, set_request_context
from utils.time import utc_now

logger = get_logger("ArtifactFlow")


class _AutoParent:
    pass


AUTO_PARENT = _AutoParent()


class ConversationExecutionConflict(Exception):
    def __init__(self, active_message_id: str) -> None:
        super().__init__("Conversation has an active execution")
        self.active_message_id = active_message_id


class ConversationAdmissionUnavailable(Exception):
    """Admission could not safely confirm lease ownership; caller may retry."""


class InvalidParentMessage(Exception):
    pass


class NoActiveExecution(Exception):
    pass


class ExecutionStillQueued(NoActiveExecution):
    pass


class PendingInterruptNotFound(Exception):
    pass


class PendingInterruptStale(Exception):
    pass


class PendingInterruptAlreadyResolved(Exception):
    pass


class UploadQuotaExceeded(Exception):
    def __init__(self, *, used_bytes: int, incoming_bytes: int, quota_bytes: int) -> None:
        super().__init__("Upload storage quota exceeded")
        self.used_bytes = used_bytes
        self.incoming_bytes = incoming_bytes
        self.quota_bytes = quota_bytes


@dataclass(frozen=True)
class ConversationTurnRequest:
    user_id: str
    user_input: str
    conversation_id: Optional[str] = None
    parent_message_id: str | None | _AutoParent = AUTO_PARENT
    uploaded_files: Sequence[Dict[str, Any]] = ()
    force_compact: bool = False
    activate_skills: Sequence[str] = ()


@dataclass(frozen=True)
class ConversationExecutionHandle:
    conversation_id: str
    message_id: str
    stream_url: str


@dataclass(frozen=True)
class BulkDeleteResult:
    deleted: list[str]
    failed: list["BulkDeleteFailure"]


@dataclass(frozen=True)
class BulkDeleteFailure:
    conversation_id: str
    reason: Literal["not_found", "active_execution"]


class ConversationExecutionService:
    """Application service that owns Conversation live-state coordination."""

    def __init__(
        self,
        *,
        db_manager: Any,
        store: RuntimeStore,
        stream_transport: StreamTransport,
        lease_coordinator: ConversationLeaseCoordinator,
        task_supervisor: TaskSupervisor,
    ) -> None:
        self._db = db_manager
        self._store = store
        self._streams = stream_transport
        self._leases = lease_coordinator
        self._tasks = task_supervisor

    async def _with_manager(self, fn):
        async def _call(session):
            return await fn(ConversationManager(ConversationRepository(session)))

        return await self._db.with_retry(_call)

    async def _require_owned(self, conversation_id: str, user_id: str) -> None:
        await self._with_manager(
            lambda manager: manager.require_owned(conversation_id, user_id)
        )

    async def _message_belongs(
        self, conversation_id: str, message_id: str
    ) -> bool:
        async def _check(manager: ConversationManager) -> bool:
            message = await manager.get_message(message_id)
            return bool(message and message.conversation_id == conversation_id)

        return await self._with_manager(_check)

    @staticmethod
    def _ensure_lease(handle: ConversationLeaseHandle) -> None:
        try:
            handle.ensure_owned()
        except ConversationLeaseLost as exc:
            raise ConversationAdmissionUnavailable(str(exc)) from exc

    async def submit_turn(
        self, request: ConversationTurnRequest
    ) -> ConversationExecutionHandle:
        requested_conversation_id = request.conversation_id
        if requested_conversation_id is None:
            is_new = True
            conversation_id = f"conv-{uuid4().hex}"
        else:
            is_new = False
            conversation_id = requested_conversation_id
        message_id = f"msg-{uuid4().hex}"
        set_request_context(message_id=message_id, conv_id=conversation_id)

        # Security precheck only: an active cross-user conversation must remain
        # indistinguishable from a missing one, rather than leaking as 409.  The
        # authoritative require is repeated after lease acquisition below.
        if not is_new:
            await self._require_owned(conversation_id, request.user_id)
        elif isinstance(request.parent_message_id, str):
            raise InvalidParentMessage(
                "parent_message_id does not belong to this conversation"
            )

        incoming_blob_bytes = sum(
            len(file["blob"])
            for file in request.uploaded_files
            if file.get("blob") is not None
        )
        if config.ARTIFACT_USER_QUOTA_BYTES > 0 and incoming_blob_bytes > 0:
            used_bytes = await self._with_manager(
                lambda manager: manager.get_user_upload_bytes(request.user_id)
            )
            if used_bytes + incoming_blob_bytes > config.ARTIFACT_USER_QUOTA_BYTES:
                logger.warning(
                    f"Upload rejected (413): user={request.user_id} quota exceeded — "
                    f"used={used_bytes} incoming={incoming_blob_bytes} "
                    f"quota={config.ARTIFACT_USER_QUOTA_BYTES}"
                )
                raise UploadQuotaExceeded(
                    used_bytes=used_bytes,
                    incoming_bytes=incoming_blob_bytes,
                    quota_bytes=config.ARTIFACT_USER_QUOTA_BYTES,
                )

        try:
            lease = await self._leases.acquire(conversation_id, message_id)
        except ConversationLeaseConflict as exc:
            raise ConversationExecutionConflict(exc.active_owner) from exc
        except ConversationLeaseUnavailable as exc:
            raise ConversationAdmissionUnavailable(str(exc)) from exc

        stream_cleanup_needed = False
        handed_off = False
        try:
            resolved_parent = await self._finalize_admission(
                lease,
                conversation_id=conversation_id,
                user_id=request.user_id,
                is_new=is_new,
                parent_message_id=request.parent_message_id,
            )
            self._ensure_lease(lease)

            stream_cleanup_needed = True
            await self._streams.create_stream(
                message_id,
                owner_user_id=request.user_id,
                lease_check_key=lease.lease_check_key,
                lease_expected_owner=message_id,
            )
            self._ensure_lease(lease)

            async def task_events(event: TaskQueued) -> None:
                await self._streams.push_event(message_id, {
                    "type": StreamEventType.EXECUTION_QUEUED.value,
                    "timestamp": utc_now().isoformat(),
                    "data": {
                        "ahead": event.ahead,
                        "max_concurrent": event.capacity,
                    },
                })

            def workload_factory(scope: TaskScope):
                # LIFO: sandbox (registered by turn factory) -> interactive
                # -> message runtime state -> stream -> heartbeat/lease.
                scope.add_cleanup("conversation lease", lease.release)
                scope.add_cleanup(
                    "execution stream",
                    lambda: self._streams.close_stream(message_id),
                )
                scope.add_cleanup(
                    "message runtime state",
                    lambda: self._store.cleanup_message_state(message_id),
                )
                scope.add_cleanup(
                    "interactive state",
                    lambda: self._store.clear_engine_interactive(
                        conversation_id, message_id
                    ),
                )

                async def workload() -> None:
                    try:
                        lease.ensure_owned()
                        owns_lease = await self._store.mark_engine_interactive(
                            conversation_id, message_id
                        )
                    except ConversationLeaseLost:
                        return
                    except Exception:
                        logger.exception(
                            f"mark_engine_interactive errored for {message_id}; "
                            "aborting before run (fail-closed)"
                        )
                        return
                    if not owns_lease:
                        logger.warning(
                            f"Task {message_id} no longer owns its conversation "
                            "lease; aborting before run"
                        )
                        return
                    await self._execute_and_push(
                        scope=scope,
                        request=request,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        resolved_parent=resolved_parent,
                    )

                return workload

            await self._tasks.submit(
                message_id,
                workload_factory,
                event_sink=task_events,
            )
            lease.bind_fence(lambda: self._tasks.cancel(message_id))
            handed_off = True
        finally:
            if not handed_off:
                if stream_cleanup_needed:
                    try:
                        await self._streams.close_stream(message_id)
                    except Exception:
                        logger.exception(
                            f"Failed to close stream after admission failure: {message_id}"
                        )
                await lease.release()

        return ConversationExecutionHandle(
            conversation_id=conversation_id,
            message_id=message_id,
            stream_url=f"/api/v1/stream/{message_id}",
        )

    async def _finalize_admission(
        self,
        lease: ConversationLeaseHandle,
        *,
        conversation_id: str,
        user_id: str,
        is_new: bool,
        parent_message_id: str | None | _AutoParent,
    ) -> Optional[str]:
        self._ensure_lease(lease)

        async def _admit(manager: ConversationManager) -> Optional[str]:
            if is_new:
                await manager.create(conversation_id, user_id=user_id)
            else:
                await manager.require_owned(conversation_id, user_id)

            if isinstance(parent_message_id, _AutoParent):
                return await manager.get_active_branch(conversation_id)
            if parent_message_id is None:
                return None
            message = await manager.get_message(parent_message_id)
            if not message or message.conversation_id != conversation_id:
                raise InvalidParentMessage(
                    "parent_message_id does not belong to this conversation"
                )
            return parent_message_id

        resolved = await self._with_manager(_admit)
        self._ensure_lease(lease)
        return resolved

    async def _execute_and_push(
        self,
        *,
        scope: TaskScope,
        request: ConversationTurnRequest,
        conversation_id: str,
        message_id: str,
        resolved_parent: Optional[str],
    ) -> None:
        try:
            async with create_turn_handler(
                conversation_id,
                message_id,
                request.user_id,
                task_scope=scope,
                runtime_store=self._store,
            ) as handler:
                await run_and_push(
                    self._streams,
                    message_id,
                    handler.run(
                        user_input=request.user_input,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        parent_message_id=resolved_parent,
                        uploaded_files=list(request.uploaded_files),
                        force_compact=request.force_compact,
                        activate_skills=list(request.activate_skills),
                    ),
                )
        except Exception as exc:
            logger.exception(f"Failed to initialize execution: {exc}")
            await self._streams.push_event(
                message_id,
                sanitize_error_event({
                    "type": "error",
                    "timestamp": utc_now().isoformat(),
                    "data": {"success": False, "error": str(exc)},
                }),
            )

    async def inject(
        self, conversation_id: str, user_id: str, content: str
    ) -> str:
        await self._require_owned(conversation_id, user_id)
        message_id = await self._store.get_interactive_message_id(conversation_id)
        if message_id is None:
            raise NoActiveExecution()
        await self._store.inject_message(message_id, content)
        return message_id

    async def cancel(self, conversation_id: str, user_id: str) -> str:
        await self._require_owned(conversation_id, user_id)
        message_id = await self._store.get_interactive_message_id(conversation_id)
        if message_id is None:
            if await self._store.get_leased_message_id(conversation_id):
                raise ExecutionStillQueued()
            raise NoActiveExecution()
        await self._store.request_cancel(message_id)
        return message_id

    async def resume(
        self,
        *,
        conversation_id: str,
        user_id: str,
        message_id: str,
        call_id: str,
        approved: bool,
        always_allow: bool,
    ) -> None:
        await self._require_owned(conversation_id, user_id)
        if not await self._message_belongs(conversation_id, message_id):
            raise NotFoundError("Message", message_id)
        result = await self._store.resolve_interrupt(
            message_id,
            call_id,
            {"approved": approved, "always_allow": always_allow},
        )
        if result == "not_found":
            raise PendingInterruptNotFound()
        if result == "call_mismatch":
            raise PendingInterruptStale()
        if result == "already_resolved":
            raise PendingInterruptAlreadyResolved()

    async def delete(self, conversation_id: str, user_id: str) -> None:
        # Security precheck prevents an active cross-user conversation leaking as 409.
        await self._require_owned(conversation_id, user_id)
        # Every acquire attempt needs a unique CAS owner. Reusing a deterministic
        # delete owner would let an indeterminate second acquire's recovery
        # release a first delete operation that is still in flight.
        owner_id = f"delete:{uuid4().hex}"
        try:
            lease = await self._leases.acquire(conversation_id, owner_id)
        except ConversationLeaseConflict as exc:
            logger.warning(
                "Conversation delete rejected (409): active execution "
                f"(conv={conversation_id}, msg={exc.active_owner})"
            )
            raise ConversationExecutionConflict(exc.active_owner) from exc
        except ConversationLeaseUnavailable as exc:
            raise ConversationAdmissionUnavailable(str(exc)) from exc

        try:
            self._ensure_lease(lease)
            async with self._db.session() as session:
                manager = ConversationManager(ConversationRepository(session))
                await manager.require_owned(conversation_id, user_id)
                self._ensure_lease(lease)
                deleted = await manager.delete_conversation(conversation_id)
            self._ensure_lease(lease)
            if not deleted:
                raise NotFoundError("Conversation", conversation_id)
        finally:
            await lease.release()

    async def bulk_delete(
        self, conversation_ids: Sequence[str], user_id: str
    ) -> BulkDeleteResult:
        deleted: list[str] = []
        failed: list[BulkDeleteFailure] = []
        seen: set[str] = set()
        for conversation_id in conversation_ids:
            if conversation_id in seen:
                continue
            seen.add(conversation_id)
            try:
                await self.delete(conversation_id, user_id)
            except NotFoundError:
                failed.append(BulkDeleteFailure(conversation_id, "not_found"))
            except ConversationExecutionConflict:
                failed.append(BulkDeleteFailure(conversation_id, "active_execution"))
            else:
                deleted.append(conversation_id)
        return BulkDeleteResult(
            deleted=deleted,
            failed=failed,
        )

    async def shutdown(self, timeout: float = 30.0) -> None:
        await self._store.shutdown_cleanup()
        await self._tasks.shutdown(timeout=timeout)
