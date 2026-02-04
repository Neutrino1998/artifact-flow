"""Rich UI 组件"""

import re
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.spinner import Spinner

from .api_client import SSEEvent

# 检测 XML 标签的正则：<tagname> 或 </tagname> 或 <tagname ...>
XML_TAG_PATTERN = re.compile(r'</?[a-zA-Z_][a-zA-Z0-9_]*(?:\s[^>]*)?\s*/?>')

console = Console()


def print_error(message: str):
    """打印错误信息"""
    console.print(f"[red]Error:[/red] {message}")


def print_success(message: str):
    """打印成功信息"""
    console.print(f"[green]{message}[/green]")


def print_info(message: str):
    """打印信息"""
    console.print(f"[blue]{message}[/blue]")


def print_warning(message: str):
    """打印警告"""
    console.print(f"[yellow]{message}[/yellow]")


class StreamDisplay:
    """
    流式输出显示器

    设计原则：
    - Reasoning 和 Content 分开显示，各自独立的框
    - Reasoning 用浅色框（dim），Content 用正常框（cyan）
    - 已完成的内容直接 print 出来，不放在 Live 里
    - Live 只负责渲染当前正在更新的内容
    """

    def __init__(self):
        # 当前正在流式输出的内容
        self.current_agent: Optional[str] = None
        self.current_content: str = ""
        self.current_reasoning: str = ""
        # 追踪 reasoning 是否已打印（避免重复打印）
        self.reasoning_printed: bool = False
        # 追踪当前正在渲染的是什么（"reasoning" | "content" | None）
        self.current_rendering: Optional[str] = None
        # 当前执行中的工具
        self.current_tool: Optional[str] = None
        self.current_tool_params: Optional[dict] = None
        # Live 对象
        self.live: Live | None = None

    def _print_reasoning_complete(self, name: str, reasoning: str):
        """打印已完成的 reasoning Panel（浅色框）"""
        if not reasoning.strip():
            return
        panel = Panel(
            Text(reasoning, style="dim"),
            title=f"[dim]{name} (thinking)[/dim]",
            border_style="dim",
            padding=(0, 1),
        )
        console.print(panel)

    def _print_agent_complete(self, name: str, content: str):
        """打印已完成的 agent content Panel"""
        if not content.strip():
            return
        # 检测是否包含 XML 标签，如果是就用 Text 避免 Markdown 解析产生空行
        if XML_TAG_PATTERN.search(content):
            rendered = Text(content)
        else:
            rendered = Markdown(content)
        panel = Panel(
            rendered,
            title=f"[cyan]{name}[/cyan]",
            border_style="cyan",
        )
        console.print(panel)

    def _print_tool_complete(self, name: str, content: str, success: bool):
        """打印已完成的 tool Panel"""
        status = "[green]✓[/green]" if success else "[red]✗[/red]"
        title = f"{status} Tool: {name}"
        style = "green" if success else "red"
        panel = Panel(
            Text(content, style="dim"),
            title=title,
            border_style=style,
            padding=(0, 1),
        )
        console.print(panel)

    def _render_current(self) -> Panel:
        """渲染当前正在流式输出的内容（用于 Live）"""
        # 根据 current_rendering 决定渲染什么
        # 注意：流式阶段统一用 Text()，Markdown() 会在内容变化时产生空行
        if self.current_rendering == "reasoning":
            # 渲染 reasoning（浅色框）
            return Panel(
                Text(self.current_reasoning, style="dim") if self.current_reasoning else Spinner("dots", text="Thinking..."),
                title=f"[dim]{self.current_agent or 'Agent'} (thinking)[/dim]",
                border_style="dim",
            )
        elif self.current_rendering == "content":
            # 渲染 content（正常框，但流式阶段用 Text 避免空行）
            return Panel(
                Text(self.current_content) if self.current_content else Spinner("dots", text="Responding..."),
                title=f"[cyan]{self.current_agent or 'Agent'}[/cyan]",
                border_style="blue",
            )
        else:
            # 初始状态
            return Panel(
                Spinner("dots", text="Thinking..."),
                title=f"[cyan]{self.current_agent or 'Agent'}[/cyan]",
                border_style="blue",
            )

    @staticmethod
    def _format_result_data(tool_name: str | None, result_data) -> str:
        """
        格式化工具返回数据的摘要（用于 tool_complete 显示）

        Args:
            tool_name: 工具名称
            result_data: 工具返回的 data 字段

        Returns:
            摘要字符串，空字符串表示无需显示
        """
        if result_data is None:
            return ""

        if isinstance(result_data, dict):
            # Artifact 工具：显示 message 和 version
            if tool_name in ("create_artifact", "update_artifact", "rewrite_artifact"):
                msg = result_data.get("message", "")
                version = result_data.get("version")
                if version is not None:
                    return f"{msg} (v{version})"
                return msg

            # 其他返回 dict 的工具：显示 key 列表
            keys = list(result_data.keys())
            if len(keys) <= 3:
                return ", ".join(f"{k}: {repr(result_data[k])[:30]}" for k in keys)
            return f"{len(keys)} fields returned"

        if isinstance(result_data, str):
            # 搜索/抓取工具：返回的是 XML 字符串，显示长度摘要
            if len(result_data) > 100:
                return f"{len(result_data)} chars returned"
            return result_data

        return str(result_data)[:80]

    def _render_tool_executing(self) -> Panel:
        """渲染正在执行的工具"""
        # 格式化参数显示
        if self.current_tool_params:
            params_str = ", ".join(f"{k}={repr(v)[:30]}" for k, v in self.current_tool_params.items())
            if len(params_str) > 80:
                params_str = params_str[:77] + "..."
            content = Text(f"({params_str})", style="dim")
        else:
            content = Spinner("dots", text="Executing...")

        return Panel(
            content,
            title=f"[yellow]⋯[/yellow] Tool: {self.current_tool}",
            border_style="yellow",
            padding=(0, 1),
        )

    def _render(self) -> Panel:
        """渲染当前正在执行的内容（只用于 Live）"""
        # 优先渲染工具，否则渲染 agent
        if self.current_tool:
            return self._render_tool_executing()
        elif self.current_agent:
            return self._render_current()
        else:
            return Panel(
                Spinner("dots", text="Waiting..."),
                title="[dim]ArtifactFlow[/dim]",
                border_style="dim",
            )

    def _finalize_reasoning(self):
        """标记 reasoning 为已完成（不打印，只在 Live 中显示）"""
        # reasoning 只在流式阶段显示（transient Live panel）
        # 完成后自动消失，不打印到控制台历史
        if self.current_agent and self.current_reasoning and not self.reasoning_printed:
            self.reasoning_printed = True

    def _finalize_current_agent(self):
        """将当前 agent 内容打印出来（分别处理 reasoning 和 content）"""
        if self.current_agent:
            # 先打印 reasoning（如果有且未打印）
            self._finalize_reasoning()
            # 再打印 content（如果有）
            if self.current_content.strip():
                self._print_agent_complete(self.current_agent, self.current_content)
        # 清空当前状态
        self.current_content = ""
        self.current_reasoning = ""
        self.reasoning_printed = False
        self.current_rendering = None

    def handle_event(self, event: SSEEvent):
        """处理 SSE 事件"""
        if event.type == "agent_start":
            # 保存之前的 agent 内容
            self._finalize_current_agent()
            # 开始新的 agent
            self.current_agent = event.agent
            self.current_tool = None
            self.reasoning_printed = False
            self.current_rendering = None

        elif event.type == "llm_chunk":
            # 更新内容（API 返回的是累积内容）
            new_content = event.data.get("content", "")
            new_reasoning = event.data.get("reasoning_content") or ""

            # 检测从 reasoning 切换到 content 的时机
            if new_content and not self.current_content:
                # 第一次收到 content，先把 reasoning 打印出来
                if self.current_reasoning and not self.reasoning_printed:
                    self._finalize_reasoning()
                # 切换到渲染 content
                self.current_rendering = "content"
            elif new_reasoning and not self.current_rendering:
                # 第一次收到 reasoning
                self.current_rendering = "reasoning"

            self.current_content = new_content
            self.current_reasoning = new_reasoning

        elif event.type == "tool_start":
            # 保存当前 agent 内容（如果有）
            self._finalize_current_agent()
            self.current_tool = event.tool
            self.current_tool_params = event.data.get("params", {})

        elif event.type == "tool_complete":
            # 工具完成，打印结果（不重启 Live）
            success = event.data.get("success", True)
            duration_ms = event.data.get("duration_ms", 0)
            error = event.data.get("error")
            result_data = event.data.get("result_data")

            # 构建显示内容：参数 + 耗时/错误 + 返回数据摘要
            parts = []
            if self.current_tool_params:
                params_str = ", ".join(f"{k}={repr(v)[:30]}" for k, v in self.current_tool_params.items())
                if len(params_str) > 60:
                    params_str = params_str[:57] + "..."
                parts.append(f"({params_str})")

            if success:
                parts.append(f"[{duration_ms}ms]")
            else:
                parts.append(f"Error: {error or 'unknown'}")

            # 展示 result_data 摘要
            result_summary = self._format_result_data(self.current_tool, result_data)
            if result_summary:
                parts.append(f"\n→ {result_summary}")

            content = " ".join(parts)

            # 直接打印，Live 会自动处理位置
            self._print_tool_complete(self.current_tool or "unknown", content, success)
            self.current_tool = None
            self.current_tool_params = None

        elif event.type == "agent_complete":
            # Agent 完成，保存到历史
            self._finalize_current_agent()
            self.current_agent = None

        elif event.type == "permission_request":
            # 权限请求 - 暂时用打印处理
            tool = event.tool
            level = event.data.get("permission_level", "unknown")
            # 这里可以考虑添加到 history 或特殊处理
            print_warning(f"Permission required: {tool} ({level})")

        # 更新显示
        if self.live:
            self.live.update(self._render())

    def start(self):
        """开始 Live 显示"""
        # transient=True: 停止时清除动态内容
        # vertical_overflow="visible": 允许内容高度自由变化，避免保留旧高度导致空行
        self.live = Live(
            self._render(),
            console=console,
            refresh_per_second=10,
            transient=True,
            vertical_overflow="ellipsis"
        )
        self.live.start()

    def stop(self):
        """停止 Live 显示"""
        if self.live:
            self.live.stop()
            self.live = None


def print_conversations_table(conversations: list):
    """打印对话列表表格"""
    table = Table(title="Conversations")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title", style="white")
    table.add_column("Messages", justify="right")
    table.add_column("Created", style="dim")

    for conv in conversations:
        conv_id = conv["id"][:8] + "..."  # 截短 ID
        title = conv.get("title") or "(No title)"
        if len(title) > 40:
            title = title[:37] + "..."
        msg_count = str(conv.get("message_count", "-"))
        created = conv.get("created_at", "-")[:16]  # 只显示日期时间

        table.add_row(conv_id, title, msg_count, created)

    console.print(table)


def print_conversation_detail(conv: dict):
    """打印对话详情"""
    console.print(Panel(
        f"[cyan]ID:[/cyan] {conv['id']}\n"
        f"[cyan]Title:[/cyan] {conv.get('title') or '(No title)'}\n"
        f"[cyan]Session:[/cyan] {conv['session_id']}\n"
        f"[cyan]Created:[/cyan] {conv.get('created_at', '-')}",
        title="Conversation",
        border_style="cyan",
    ))

    # 打印消息
    messages = conv.get("messages", [])
    if messages:
        console.print(f"\n[cyan]Messages ({len(messages)}):[/cyan]")
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if len(content) > 200:
                content = content[:197] + "..."

            role_style = "green" if role == "user" else "blue"
            console.print(f"  [{role_style}]{role}:[/{role_style}] {content}")


def print_artifacts_table(artifacts: list):
    """打印 Artifacts 表格"""
    if not artifacts:
        print_info("No artifacts found")
        return

    table = Table(title="Artifacts")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title", style="white")
    table.add_column("Type", style="yellow")
    table.add_column("Version", justify="right")
    table.add_column("Updated", style="dim")

    for art in artifacts:
        art_id = art["id"]  # Show full ID for copying
        title = art.get("title", "(No title)")
        if len(title) > 30:
            title = title[:27] + "..."
        art_type = art.get("content_type", "-")  # API uses content_type, not artifact_type
        version = f"v{art.get('current_version', 1)}"
        updated = art.get("updated_at", "-")[:16]

        table.add_row(art_id, title, art_type, version, updated)

    console.print(table)


def print_artifact_content(artifact: dict):
    """打印 Artifact 内容"""
    console.print(Panel(
        f"[cyan]ID:[/cyan] {artifact['id']}\n"
        f"[cyan]Title:[/cyan] {artifact.get('title', '(No title)')}\n"
        f"[cyan]Type:[/cyan] {artifact.get('content_type', '-')}\n"
        f"[cyan]Version:[/cyan] v{artifact.get('current_version', 1)}",
        title="Artifact",
        border_style="cyan",
    ))

    content = artifact.get("content", "")
    if content:
        console.print("\n[cyan]Content:[/cyan]")
        console.print(Panel(Markdown(content), border_style="dim"))
