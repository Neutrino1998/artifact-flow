"""ExecutionLauncher — prepared turn submission facade.

This module deliberately adds no execution lifecycle of its own.  It only
centralizes the existing top-level launch sequence that used to live in the
chat router::

    create_controller -> run_and_push -> ExecutionRunner.submit

Callers still own use-case preparation (authentication/ownership checks,
conversation creation, upload conversion and quota gates).  ExecutionRunner
continues to own the conversation lease/task lifecycle, while
ExecutionController owns engine timeout, persistence, artifact flush and the
terminal decision.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

from api.services.controller_factory import (
    create_controller,
    run_and_push,
    sanitize_error_event,
)
from api.services.execution_runner import ExecutionRunner
from api.services.stream_transport import StreamTransport
from utils.logger import get_logger, set_request_context
from utils.time import utc_now

logger = get_logger("ArtifactFlow")


class _AutoParent:
    """Sentinel preserving stream_execute's omitted-parent semantics."""


AUTO_PARENT = _AutoParent()


@dataclass(frozen=True)
class ExecutionSpec:
    """A fully prepared top-level turn.

    Preconditions are intentionally outside this thin facade:

    - ``conversation_id`` exists and belongs to ``user_id``;
    - ``message_id`` is newly allocated;
    - uploaded files have already passed transport validation/conversion and
      user quota admission.

    ``parent_message_id`` is tri-state, matching ``ExecutionController``:
    ``AUTO_PARENT`` selects the active branch, ``None`` starts from the root,
    and a string branches from that message.
    """

    user_id: str
    conversation_id: str
    message_id: str
    user_input: str
    parent_message_id: str | None | _AutoParent = AUTO_PARENT
    uploaded_files: Sequence[Dict[str, Any]] = ()
    force_compact: bool = False
    activate_skills: Sequence[str] = ()


@dataclass(frozen=True)
class ExecutionHandle:
    conversation_id: str
    message_id: str
    stream_url: str


class ExecutionLauncher:
    """Submit a prepared turn through the existing execution lifecycle."""

    def __init__(
        self,
        runner: ExecutionRunner,
        stream_transport: StreamTransport,
    ) -> None:
        self._runner = runner
        self._stream_transport = stream_transport

    async def submit(self, spec: ExecutionSpec) -> ExecutionHandle:
        """Launch ``spec`` and return its durable execution identifiers.

        ``ConflictError`` / ``DuplicateExecutionError`` from ExecutionRunner
        intentionally propagate so each transport can map them to its own
        response.  Initialization failures after submission follow the
        existing chat behavior: log for ops, push a sanitized error to the
        owned stream, then close it.
        """
        set_request_context(
            message_id=spec.message_id,
            conv_id=spec.conversation_id,
        )

        async def execute_and_push() -> None:
            try:
                async with create_controller(
                    spec.conversation_id,
                    spec.message_id,
                    spec.user_id,
                ) as controller:
                    parent_kwargs: Dict[str, Optional[str]] = {}
                    if not isinstance(spec.parent_message_id, _AutoParent):
                        parent_kwargs["parent_message_id"] = spec.parent_message_id

                    await run_and_push(
                        self._stream_transport,
                        spec.message_id,
                        controller.stream_execute(
                            user_input=spec.user_input,
                            conversation_id=spec.conversation_id,
                            message_id=spec.message_id,
                            uploaded_files=list(spec.uploaded_files),
                            force_compact=spec.force_compact,
                            activate_skills=list(spec.activate_skills),
                            **parent_kwargs,
                        ),
                    )
            except Exception as exc:
                logger.exception(f"Failed to initialize execution: {exc}")
                await self._stream_transport.push_event(
                    spec.message_id,
                    sanitize_error_event({
                        "type": "error",
                        "timestamp": utc_now().isoformat(),
                        "data": {"success": False, "error": str(exc)},
                    }),
                )
                await self._stream_transport.close_stream(spec.message_id)

        await self._runner.submit(
            spec.conversation_id,
            spec.message_id,
            execute_and_push,
            user_id=spec.user_id,
            stream_transport=self._stream_transport,
        )

        return ExecutionHandle(
            conversation_id=spec.conversation_id,
            message_id=spec.message_id,
            stream_url=f"/api/v1/stream/{spec.message_id}",
        )
