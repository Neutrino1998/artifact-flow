"""Narrow read-only facade over conversation runtime status."""

from __future__ import annotations

from typing import Optional

from api.services.runtime_store import ConversationLeaseReader, InteractionReader
from api.services.stream_transport import StreamTransport


class RuntimeStatusReader:
    def __init__(
        self,
        lease_reader: ConversationLeaseReader,
        interaction_reader: InteractionReader,
        stream_transport: StreamTransport,
    ) -> None:
        self._leases = lease_reader
        self._interactions = interaction_reader
        self._streams = stream_transport

    @property
    def is_shared(self) -> bool:
        return self._leases.is_shared

    async def get_active_message_id(self, conversation_id: str) -> Optional[str]:
        return await self._leases.get_leased_message_id(conversation_id)

    async def get_interactive_message_id(
        self, conversation_id: str
    ) -> Optional[str]:
        return await self._interactions.get_interactive_message_id(conversation_id)

    async def list_active_conversations(self) -> list[str]:
        return await self._leases.list_active_conversations()

    async def list_active_executions(self) -> dict[str, str]:
        return await self._leases.list_active_executions()

    async def get_active_stream_message_id(
        self, conversation_id: str
    ) -> Optional[str]:
        message_id = await self._leases.get_leased_message_id(conversation_id)
        if message_id is None:
            return None
        if not await self._streams.is_stream_alive(message_id):
            return None
        return message_id
