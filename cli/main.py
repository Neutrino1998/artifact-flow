"""ArtifactFlow CLI 主入口"""

import asyncio
from typing import Optional

import typer
from rich.prompt import Prompt

from .api_client import APIClient
from .config import CLIState, DEFAULT_BASE_URL
from . import ui

app = typer.Typer(
    name="artifactflow",
    help="ArtifactFlow CLI - Terminal interface for ArtifactFlow API",
    no_args_is_help=True,
)

# 全局状态
state = CLIState.load()
client: APIClient | None = None


def get_client(base_url: str = DEFAULT_BASE_URL) -> APIClient:
    """获取或创建 API 客户端"""
    global client
    if client is None:
        client = APIClient(base_url=base_url, token=state.token)
    return client


# ============================================================
# 认证命令
# ============================================================

@app.command("login")
def login(
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--url", "-u", help="API base URL"),
):
    """Login to ArtifactFlow API."""
    global client

    username = Prompt.ask("Username")
    password = Prompt.ask("Password", password=True)

    # 登录不需要 token，直接创建临时 client
    api = APIClient(base_url=base_url)

    try:
        result = asyncio.run(api.login(username, password))
        state.token = result["access_token"]
        state.save()

        # 重置 client 以使用新 token
        client = None

        user = result.get("user", {})
        ui.print_success(f"Logged in as {user.get('username', username)} (role={user.get('role', '?')})")

    except Exception as e:
        ui.print_error(f"Login failed: {e}")
        raise typer.Exit(1)


@app.command("logout")
def logout():
    """Logout and clear saved token."""
    state.logout()
    ui.print_success("Logged out")


# ============================================================
# 聊天命令
# ============================================================

@app.command()
def chat(
    message: Optional[str] = typer.Argument(None, help="Message to send"),
    new: bool = typer.Option(False, "--new", "-n", help="Start a new conversation"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--url", "-u", help="API base URL"),
):
    """
    Send a message and receive streaming response.

    If no message is provided, enters interactive mode.
    """
    api = get_client(base_url)

    # 检查服务器
    if not asyncio.run(api.health_check()):
        ui.print_error(f"Cannot connect to server at {base_url}")
        ui.print_info("Make sure the server is running: python run_server.py")
        raise typer.Exit(1)

    # 新对话
    if new:
        state.clear()
        ui.print_info("Starting new conversation")

    # 交互模式
    if message is None:
        interactive_chat(api)
    else:
        send_single_message(api, message)


def send_single_message(api: APIClient, message: str):
    """发送单条消息"""
    asyncio.run(_send_message_async(api, message))


async def _stream_events(api: APIClient, display: ui.StreamDisplay, thread_id: str) -> dict:
    """
    消费 SSE 事件流，返回结果信息。

    Returns:
        dict with keys:
        - success: bool
        - interrupted: bool (权限中断)
        - permission_event: SSEEvent | None (权限请求事件)
        - message_id: str | None
    """
    result = {"success": False, "interrupted": False, "permission_event": None, "message_id": None}

    async for event in api.stream_response(thread_id):
        display.handle_event(event)

        if event.type == "metadata":
            # 保存 metadata 中的 thread_id/message_id 用于 resume
            if "thread_id" in event.data:
                result["thread_id"] = event.data["thread_id"]
            if "message_id" in event.data:
                result["message_id"] = event.data["message_id"]

        elif event.type == "permission_request":
            result["permission_event"] = event

        elif event.type == "complete":
            result["success"] = event.data.get("success", False)
            if event.data.get("interrupted"):
                result["interrupted"] = True
            if "message_id" in event.data:
                result["message_id"] = event.data["message_id"]

        elif event.type == "error":
            result["success"] = False
            ui.print_error(event.data.get("error", "Unknown error"))

    return result


async def _send_message_async(api: APIClient, message: str):
    """异步发送消息并显示流式响应"""
    global state

    # 显示用户消息
    ui.console.print(f"\n[green]You:[/green] {message}\n")

    try:
        # 发送消息
        resp = await api.send_message(
            content=message,
            conversation_id=state.conversation_id,
            parent_message_id=state.parent_message_id,
        )

        # 更新状态
        state.conversation_id = resp.conversation_id
        state.parent_message_id = resp.message_id

        thread_id = resp.thread_id
        conversation_id = resp.conversation_id
        message_id = resp.message_id

        # 流式接收响应（可能因权限中断而多次循环）
        while True:
            display = ui.StreamDisplay()
            display.start()

            try:
                result = await _stream_events(api, display, thread_id)
            finally:
                display.stop()

            # 更新 message_id
            if result.get("message_id"):
                message_id = result["message_id"]
                state.parent_message_id = message_id

            # 如果被权限中断，提示用户做决定，然后 resume
            if result["interrupted"] and result["permission_event"]:
                perm = result["permission_event"]
                tool_name = perm.tool or "unknown"
                level = perm.data.get("permission_level", "unknown")
                params = perm.data.get("params", {})

                # 显示权限请求详情
                ui.print_permission_request(tool_name, level, params)

                # 提示用户
                answer = Prompt.ask(
                    "[yellow]Approve?[/yellow]",
                    choices=["y", "n"],
                    default="y",
                )
                approved = answer.lower() == "y"

                # 调用 resume API，获取新的 stream thread_id
                thread_id = await api.resume_execution(
                    conversation_id=conversation_id,
                    thread_id=thread_id,
                    message_id=message_id,
                    approved=approved,
                )
                # 继续循环，消费 resume 后的事件流
                continue

            # 正常完成或失败
            if not result["success"]:
                ui.print_error("Execution failed")
            break

        # 保存状态
        state.save()

    except Exception as e:
        ui.print_error(f"Failed to send message: {e}")
        raise typer.Exit(1)


def interactive_chat(api: APIClient):
    """交互式聊天模式"""
    ui.console.print("[cyan]Interactive mode. Type 'quit' or 'exit' to leave.[/cyan]")
    ui.console.print("[dim]Commands: /new (new conversation), /status (show state)[/dim]\n")

    while True:
        try:
            message = Prompt.ask("[green]You[/green]")

            if not message.strip():
                continue

            # 特殊命令
            if message.lower() in ("quit", "exit", "/quit", "/exit"):
                ui.print_info("Goodbye!")
                break
            elif message.lower() == "/new":
                state.clear()
                ui.print_info("Started new conversation")
                continue
            elif message.lower() == "/status":
                ui.console.print(f"  conversation_id: {state.conversation_id or '(none)'}")
                ui.console.print(f"  parent_message_id: {state.parent_message_id or '(none)'}")
                continue

            # 发送消息
            asyncio.run(_send_message_async(api, message))
            ui.console.print()  # 空行分隔

        except KeyboardInterrupt:
            ui.console.print()
            ui.print_info("Interrupted. Type 'quit' to exit.")
        except EOFError:
            break


# ============================================================
# 对话管理命令
# ============================================================

@app.command("list")
def list_conversations(
    limit: int = typer.Option(10, "--limit", "-l", help="Number of conversations to show"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--url", "-u", help="API base URL"),
):
    """List recent conversations."""
    api = get_client(base_url)

    try:
        result = asyncio.run(api.list_conversations(limit=limit))
        conversations = result.get("conversations", [])

        if not conversations:
            ui.print_info("No conversations found")
            return

        ui.print_conversations_table(conversations)
        ui.console.print(f"\n[dim]Total: {result.get('total', len(conversations))}[/dim]")

    except Exception as e:
        ui.print_error(f"Failed to list conversations: {e}")
        raise typer.Exit(1)


@app.command("show")
def show_conversation(
    conversation_id: str = typer.Argument(..., help="Conversation ID (can be partial)"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--url", "-u", help="API base URL"),
):
    """Show conversation details."""
    api = get_client(base_url)

    try:
        conv = asyncio.run(api.get_conversation(conversation_id))
        ui.print_conversation_detail(conv)

    except Exception as e:
        ui.print_error(f"Failed to get conversation: {e}")
        raise typer.Exit(1)


@app.command("use")
def use_conversation(
    conversation_id: str = typer.Argument(..., help="Conversation ID to continue"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--url", "-u", help="API base URL"),
):
    """Switch to an existing conversation."""
    api = get_client(base_url)

    try:
        # 验证对话存在
        conv = asyncio.run(api.get_conversation(conversation_id, load_messages=False))

        # 更新状态
        state.conversation_id = conv["id"]
        state.session_id = conv.get("session_id")
        # 获取最后一条消息作为 parent
        messages = conv.get("messages", [])
        if messages:
            state.parent_message_id = messages[-1].get("id")
        else:
            state.parent_message_id = None

        state.save()

        ui.print_success(f"Switched to conversation: {conv['id'][:8]}...")
        ui.console.print(f"[dim]Title: {conv.get('title') or '(No title)'}[/dim]")

    except Exception as e:
        ui.print_error(f"Failed to switch conversation: {e}")
        raise typer.Exit(1)


# ============================================================
# Artifacts 命令
# ============================================================

@app.command("artifacts")
def list_artifacts(
    session_id: Optional[str] = typer.Argument(None, help="Session ID (defaults to current)"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--url", "-u", help="API base URL"),
):
    """List artifacts for a session."""
    api = get_client(base_url)

    # 使用当前会话或指定的 session_id
    sid = session_id or state.conversation_id
    if not sid:
        ui.print_error("No session specified. Start a chat first or provide session_id.")
        raise typer.Exit(1)

    try:
        result = asyncio.run(api.list_artifacts(sid))
        artifacts = result.get("artifacts", [])
        ui.print_artifacts_table(artifacts)

    except Exception as e:
        ui.print_error(f"Failed to list artifacts: {e}")
        raise typer.Exit(1)


@app.command("artifact")
def show_artifact(
    artifact_id: str = typer.Argument(..., help="Artifact ID"),
    session_id: Optional[str] = typer.Option(None, "--session", "-s", help="Session ID"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--url", "-u", help="API base URL"),
):
    """Show artifact content."""
    api = get_client(base_url)

    sid = session_id or state.conversation_id
    if not sid:
        ui.print_error("No session specified. Start a chat first or provide --session.")
        raise typer.Exit(1)

    try:
        artifact = asyncio.run(api.get_artifact(sid, artifact_id))
        ui.print_artifact_content(artifact)

    except Exception as e:
        ui.print_error(f"Failed to get artifact: {e}")
        raise typer.Exit(1)


# ============================================================
# 状态命令
# ============================================================

@app.command("status")
def show_status():
    """Show current CLI state."""
    ui.console.print("[cyan]Current State:[/cyan]")
    ui.console.print(f"  conversation_id: {state.conversation_id or '[dim](none)[/dim]'}")
    ui.console.print(f"  session_id: {state.session_id or '[dim](none)[/dim]'}")
    ui.console.print(f"  parent_message_id: {state.parent_message_id or '[dim](none)[/dim]'}")


@app.command("clear")
def clear_state():
    """Clear current session state."""
    state.clear()
    ui.print_success("State cleared")


def main():
    """CLI 入口点"""
    app()


if __name__ == "__main__":
    main()
