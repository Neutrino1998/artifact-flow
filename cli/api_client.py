"""API 客户端封装"""

import json
from typing import AsyncIterator
from dataclasses import dataclass

import httpx

from .config import DEFAULT_BASE_URL, DEFAULT_TIMEOUT


@dataclass
class SendMessageResponse:
    """发送消息响应"""
    conversation_id: str
    message_id: str
    thread_id: str
    stream_url: str


@dataclass
class SSEEvent:
    """SSE 事件"""
    type: str
    data: dict
    agent: str | None = None
    tool: str | None = None


class APIClient:
    """ArtifactFlow API 客户端"""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def health_check(self) -> bool:
        """健康检查"""
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
            try:
                resp = await client.get("/health")
                return resp.status_code == 200
            except Exception:
                return False

    async def send_message(
        self,
        content: str,
        conversation_id: str | None = None,
        parent_message_id: str | None = None,
    ) -> SendMessageResponse:
        """发送消息"""
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            payload = {"content": content}
            if conversation_id:
                payload["conversation_id"] = conversation_id
            if parent_message_id:
                payload["parent_message_id"] = parent_message_id

            resp = await client.post("/api/v1/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

            return SendMessageResponse(
                conversation_id=data["conversation_id"],
                message_id=data["message_id"],
                thread_id=data["thread_id"],
                stream_url=data["stream_url"],
            )

    async def stream_response(self, thread_id: str) -> AsyncIterator[SSEEvent]:
        """流式接收响应"""
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            async with client.stream("GET", f"/api/v1/stream/{thread_id}") as response:
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue

                    try:
                        event_data = json.loads(line[5:].strip())
                        yield SSEEvent(
                            type=event_data.get("type", "unknown"),
                            data=event_data.get("data", {}),
                            agent=event_data.get("agent"),
                            tool=event_data.get("tool"),
                        )

                        # 终结事件
                        if event_data.get("type") in ("complete", "error"):
                            break
                    except json.JSONDecodeError:
                        continue

    async def list_conversations(self, limit: int = 20, offset: int = 0) -> dict:
        """列出对话"""
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
            resp = await client.get(
                "/api/v1/chat",
                params={"limit": limit, "offset": offset}
            )
            resp.raise_for_status()
            return resp.json()

    async def get_conversation(self, conversation_id: str, load_messages: bool = True) -> dict:
        """获取对话详情"""
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
            params = {"load_messages": load_messages}
            resp = await client.get(f"/api/v1/chat/{conversation_id}", params=params)
            resp.raise_for_status()
            return resp.json()

    async def list_artifacts(self, session_id: str) -> dict:
        """列出 Artifacts"""
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
            resp = await client.get(f"/api/v1/artifacts/{session_id}")
            if resp.status_code == 404:
                return {"session_id": session_id, "artifacts": []}
            resp.raise_for_status()
            return resp.json()

    async def get_artifact(self, session_id: str, artifact_id: str) -> dict:
        """获取单个 Artifact"""
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
            resp = await client.get(f"/api/v1/artifacts/{session_id}/{artifact_id}")
            resp.raise_for_status()
            return resp.json()
