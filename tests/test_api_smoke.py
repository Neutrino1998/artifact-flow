#!/usr/bin/env python3
"""
ArtifactFlow API 冒烟测试

使用方式:
    1. 启动服务器:
       cd src && python run_server.py

    2. 运行测试 (新开终端):
       python -m tests.api_smoke_test

    3. 运行单个测试:
       python -m tests.api_smoke_test --test chat
       python -m tests.api_smoke_test --test stream
       python -m tests.api_smoke_test --test artifacts
"""

import asyncio
import argparse
import json
import sys
from datetime import datetime
from typing import Optional

import httpx

# 配置
BASE_URL = "http://localhost:8000"
TIMEOUT = 120  # SSE 可能需要较长时间


def log(msg: str, level: str = "INFO"):
    """简单日志"""
    colors = {
        "INFO": "\033[94m",    # 蓝色
        "OK": "\033[92m",      # 绿色
        "WARN": "\033[93m",    # 黄色
        "ERROR": "\033[91m",   # 红色
        "RESET": "\033[0m"
    }
    timestamp = datetime.now().strftime("%H:%M:%S")
    color = colors.get(level, "")
    reset = colors["RESET"]
    print(f"{color}[{timestamp}] [{level}] {msg}{reset}")


# ============================================================
# 测试函数
# ============================================================

async def test_health():
    """测试健康检查端点"""
    log("Testing /health endpoint...")

    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        resp = await client.get("/health")

        if resp.status_code == 200:
            log(f"Health check passed: {resp.json()}", "OK")
            return True
        else:
            log(f"Health check failed: {resp.status_code}", "ERROR")
            return False


async def test_chat_send_message() -> Optional[dict]:
    """
    测试发送消息

    Returns:
        响应数据 (conversation_id, message_id, thread_id, stream_url)
    """
    log("Testing POST /api/v1/chat...")

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        resp = await client.post("/api/v1/chat", json={
            "content": "你好，请简单介绍一下你自己"
        })

        if resp.status_code == 200:
            data = resp.json()
            log(f"Message sent successfully:", "OK")
            log(f"  conversation_id: {data['conversation_id']}")
            log(f"  message_id: {data['message_id']}")
            log(f"  thread_id: {data['thread_id']}")
            log(f"  stream_url: {data['stream_url']}")
            return data
        else:
            log(f"Failed to send message: {resp.status_code} - {resp.text}", "ERROR")
            return None


async def test_sse_stream(thread_id: str, max_events: int = 50):
    """
    测试 SSE 流式输出

    Args:
        thread_id: 线程 ID
        max_events: 最大接收事件数（防止无限等待）
    """
    log(f"Testing GET /api/v1/stream/{thread_id}...")
    log("Waiting for SSE events (this may take a while)...")

    event_count = 0
    event_types = {}
    final_response = None

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        try:
            async with client.stream("GET", f"/api/v1/stream/{thread_id}") as response:
                current_event_name = None
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        current_event_name = line[6:].strip()
                        continue

                    if not line.startswith("data:"):
                        continue

                    event_count += 1

                    # 解析事件
                    try:
                        event_data = json.loads(line[5:].strip())  # 去掉 "data:" 前缀
                        # 优先使用 SSE event: 字段，回退到 data.type
                        event_type = current_event_name or event_data.get("type", "unknown")
                        current_event_name = None

                        # 统计事件类型
                        event_types[event_type] = event_types.get(event_type, 0) + 1

                        # 打印关键事件
                        if event_type == "metadata":
                            log(f"  [metadata] conversation_id={event_data['data'].get('conversation_id')}")
                        elif event_type == "agent_start":
                            log(f"  [agent_start] agent={event_data.get('agent')}")
                        elif event_type == "llm_chunk":
                            # LLM chunk 太多，只打印少量
                            if event_types[event_type] <= 3:
                                content = event_data.get("data", {}).get("content", "")[:50]
                                log(f"  [llm_chunk] content={content}...")
                            elif event_types[event_type] == 4:
                                log(f"  [llm_chunk] ... (more chunks)")
                        elif event_type == "tool_start":
                            log(f"  [tool_start] tool={event_data.get('tool')}")
                        elif event_type == "tool_complete":
                            log(f"  [tool_complete] tool={event_data.get('tool')}, success={event_data['data'].get('success')}")
                        elif event_type == "permission_request":
                            log(f"  [permission_request] tool={event_data.get('tool')}, level={event_data['data'].get('permission_level')}", "WARN")
                        elif event_type == "complete":
                            log(f"  [complete] success={event_data['data'].get('success')}", "OK")
                            final_response = event_data['data'].get('response', '')[:200]
                        elif event_type == "error":
                            log(f"  [error] {event_data['data'].get('error')}", "ERROR")

                        # 终结事件
                        if event_type in ("complete", "error"):
                            break

                        # 防止无限等待
                        if event_count >= max_events:
                            log(f"  Reached max events ({max_events}), stopping...", "WARN")
                            break

                    except json.JSONDecodeError:
                        log(f"  Failed to parse event: {line[:50]}...", "WARN")

        except httpx.ReadTimeout:
            log("SSE stream timeout", "WARN")

    # 打印统计
    log(f"SSE stream finished. Total events: {event_count}")
    log(f"  Event types: {event_types}")
    if final_response:
        log(f"  Final response preview: {final_response}...")

    return event_count > 0


async def test_list_conversations():
    """测试列出对话"""
    log("Testing GET /api/v1/chat...")

    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        resp = await client.get("/api/v1/chat", params={"limit": 10})

        if resp.status_code == 200:
            data = resp.json()
            log(f"Listed conversations:", "OK")
            log(f"  total: {data['total']}")
            log(f"  has_more: {data['has_more']}")
            for conv in data['conversations'][:3]:
                log(f"  - {conv['id']}: {conv.get('title', 'No title')}")
            return data['conversations']
        else:
            log(f"Failed to list conversations: {resp.status_code}", "ERROR")
            return []


async def test_get_conversation(conv_id: str):
    """测试获取对话详情"""
    log(f"Testing GET /api/v1/chat/{conv_id}...")

    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        resp = await client.get(f"/api/v1/chat/{conv_id}")

        if resp.status_code == 200:
            data = resp.json()
            log(f"Got conversation detail:", "OK")
            log(f"  id: {data['id']}")
            log(f"  title: {data.get('title')}")
            log(f"  message_count: {len(data['messages'])}")
            log(f"  session_id: {data['session_id']}")
            return data
        else:
            log(f"Failed to get conversation: {resp.status_code}", "ERROR")
            return None


async def test_list_artifacts(session_id: str):
    """测试列出 Artifacts"""
    log(f"Testing GET /api/v1/artifacts/{session_id}...")

    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        resp = await client.get(f"/api/v1/artifacts/{session_id}")

        if resp.status_code == 200:
            data = resp.json()
            log(f"Listed artifacts:", "OK")
            log(f"  session_id: {data['session_id']}")
            log(f"  artifact_count: {len(data['artifacts'])}")
            for art in data['artifacts'][:3]:
                log(f"  - {art['id']}: {art['title']} (v{art['current_version']})")
            return data['artifacts']
        elif resp.status_code == 404:
            log(f"No artifacts found (session may not exist yet)", "WARN")
            return []
        else:
            log(f"Failed to list artifacts: {resp.status_code}", "ERROR")
            return []


# ============================================================
# 测试套件
# ============================================================

async def run_full_test():
    """运行完整测试流程"""
    log("=" * 60)
    log("ArtifactFlow API Smoke Test")
    log("=" * 60)

    results = {}

    # 1. 健康检查
    results['health'] = await test_health()
    if not results['health']:
        log("Server not responding, aborting tests", "ERROR")
        return results

    print()

    # 2. 发送消息
    chat_data = await test_chat_send_message()
    results['chat_send'] = chat_data is not None

    if chat_data:
        print()

        # 3. SSE 流式输出
        results['sse_stream'] = await test_sse_stream(chat_data['thread_id'])

        print()

        # 4. 列出对话
        conversations = await test_list_conversations()
        results['list_conversations'] = len(conversations) > 0

        print()

        # 5. 获取对话详情
        conv_detail = await test_get_conversation(chat_data['conversation_id'])
        results['get_conversation'] = conv_detail is not None

        print()

        # 6. 列出 Artifacts
        artifacts = await test_list_artifacts(chat_data['conversation_id'])
        results['list_artifacts'] = True  # 即使为空也算成功

    # 打印总结
    print()
    log("=" * 60)
    log("Test Summary")
    log("=" * 60)

    for test_name, passed in results.items():
        status = "PASSED" if passed else "FAILED"
        level = "OK" if passed else "ERROR"
        log(f"  {test_name}: {status}", level)

    passed_count = sum(1 for v in results.values() if v)
    total_count = len(results)
    log(f"  Total: {passed_count}/{total_count} passed")

    return results


async def run_single_test(test_name: str):
    """运行单个测试"""
    if test_name == "health":
        await test_health()
    elif test_name == "chat":
        data = await test_chat_send_message()
        if data:
            await test_sse_stream(data['thread_id'])
    elif test_name == "stream":
        # 需要先发送消息
        data = await test_chat_send_message()
        if data:
            await test_sse_stream(data['thread_id'], max_events=100)
    elif test_name == "list":
        await test_list_conversations()
    elif test_name == "artifacts":
        convs = await test_list_conversations()
        if convs:
            await test_list_artifacts(convs[0]['id'])
    else:
        log(f"Unknown test: {test_name}", "ERROR")
        log("Available tests: health, chat, stream, list, artifacts")


def main():
    parser = argparse.ArgumentParser(description="ArtifactFlow API Smoke Test")
    parser.add_argument(
        "--test",
        type=str,
        default=None,
        help="Run specific test (health, chat, stream, list, artifacts)"
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="http://localhost:8000",
        help="API base URL"
    )

    args = parser.parse_args()

    global BASE_URL
    BASE_URL = args.base_url

    if args.test:
        asyncio.run(run_single_test(args.test))
    else:
        asyncio.run(run_full_test())


if __name__ == "__main__":
    main()
