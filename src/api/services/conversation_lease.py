"""Conversation-specific lease ownership and heartbeat fencing."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Callable, Optional

from api.services.runtime_store import ConversationLeaseStore
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class ConversationLeaseConflict(Exception):
    """The conversation is already owned by another live operation."""

    def __init__(self, active_owner: str) -> None:
        super().__init__(f"Conversation lease is held by {active_owner}")
        self.active_owner = active_owner


class ConversationLeaseLost(Exception):
    """Lease ownership was lost or could no longer be confirmed."""


class ConversationLeaseUnavailable(Exception):
    """The store could not determine whether acquisition succeeded."""


class ConversationLeaseHandle:
    """One acquired lease, renewed from acquisition until final cleanup."""

    def __init__(
        self,
        store: ConversationLeaseStore,
        conversation_id: str,
        owner_id: str,
        *,
        lease_ttl: float,
        heartbeat_interval: Optional[float] = None,
    ) -> None:
        self._store = store
        self.conversation_id = conversation_id
        self.owner_id = owner_id
        self._lease_ttl = lease_ttl
        self._heartbeat_interval = (
            heartbeat_interval
            if heartbeat_interval is not None
            else max(0.05, lease_ttl / 3)
        )
        self._heartbeat: Optional[asyncio.Task[None]] = None
        self._lost = asyncio.Event()
        self._lost_reason: Optional[str] = None
        self._fence: Optional[Callable[[], None]] = None
        self._released = False
        self._release_lock = asyncio.Lock()

    def start_heartbeat(self) -> None:
        if self._lease_ttl <= 0 or self._heartbeat is not None:
            return
        self._heartbeat = asyncio.create_task(
            self._renew_loop(),
            name=f"lease-heartbeat-{self.owner_id}",
        )

    @property
    def lease_check_key(self) -> str:
        return self._store.get_lease_key(self.conversation_id)

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    @property
    def lost_reason(self) -> Optional[str]:
        return self._lost_reason

    def ensure_owned(self) -> None:
        if self._lost.is_set():
            raise ConversationLeaseLost(
                self._lost_reason or "Conversation lease ownership was lost"
            )

    def bind_fence(self, callback: Callable[[], None]) -> None:
        """Bind the admitted task exactly once; loss before binding fences now."""
        if self._fence is not None:
            raise RuntimeError("Conversation lease fence is already bound")
        self._fence = callback
        if self._lost.is_set():
            callback()

    def _mark_lost(self, reason: str) -> None:
        if self._released or self._lost.is_set():
            return
        self._lost_reason = reason
        self._lost.set()
        if self._fence is not None:
            self._fence()

    async def _renew_loop(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                still_owner = await self._store.renew_lease(
                    self.conversation_id,
                    self.owner_id,
                    ttl=self._lease_ttl,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    f"Lease renewal could not confirm ownership for {self.owner_id}; "
                    "fencing fail-closed"
                )
                self._mark_lost(f"Lease renewal failed: {exc}")
                return

            if not still_owner:
                logger.error(f"Lease lost for {self.owner_id}; fencing execution")
                self._mark_lost("Conversation lease was lost")
                return

    async def release(self) -> None:
        """Stop renewal and compare-and-release; failures remain TTL-bounded."""
        async with self._release_lock:
            if self._released:
                return
            self._released = True
            if self._heartbeat is not None:
                self._heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._heartbeat
                self._heartbeat = None
            try:
                await self._store.release_lease(
                    self.conversation_id,
                    self.owner_id,
                )
            except Exception:
                logger.exception(
                    "Failed to release conversation lease "
                    f"(conv={self.conversation_id}, owner={self.owner_id}); "
                    "TTL will bound recovery"
                )


class ConversationLeaseCoordinator:
    """Acquire handles and start renewal at the linearization point."""

    def __init__(
        self,
        store: ConversationLeaseStore,
        *,
        lease_ttl: float,
        heartbeat_interval: Optional[float] = None,
    ) -> None:
        self._store = store
        self._lease_ttl = lease_ttl
        self._heartbeat_interval = heartbeat_interval

    async def acquire(
        self,
        conversation_id: str,
        owner_id: str,
    ) -> ConversationLeaseHandle:
        try:
            active = await self._store.try_acquire_lease(conversation_id, owner_id)
        except Exception as exc:
            logger.exception(
                "Conversation lease acquisition could not confirm ownership "
                f"(conv={conversation_id}, owner={owner_id})"
            )
            # The acquire script may have committed before its response was lost.
            # Owner CAS makes this safe whether or not acquisition took effect.
            try:
                await self._store.release_lease(conversation_id, owner_id)
            except Exception:
                logger.exception(
                    "Best-effort release after indeterminate lease acquisition failed "
                    f"(conv={conversation_id}, owner={owner_id}); TTL will recover"
                )
            raise ConversationLeaseUnavailable(
                "Conversation lease ownership could not be confirmed"
            ) from exc
        if active is not None:
            raise ConversationLeaseConflict(active)
        handle = ConversationLeaseHandle(
            self._store,
            conversation_id,
            owner_id,
            lease_ttl=self._lease_ttl,
            heartbeat_interval=self._heartbeat_interval,
        )
        handle.start_heartbeat()
        return handle
