"""CLI 配置"""

from dataclasses import dataclass
from pathlib import Path

# 默认配置
DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 120  # SSE 需要较长时间

# 状态文件（保存当前会话）
STATE_FILE = Path.home() / ".artifactflow_cli_state"


@dataclass
class CLIState:
    """CLI 状态，保存当前会话信息"""
    conversation_id: str | None = None
    session_id: str | None = None
    parent_message_id: str | None = None

    def save(self):
        """保存状态到文件"""
        import json
        data = {
            "conversation_id": self.conversation_id,
            "session_id": self.session_id,
            "parent_message_id": self.parent_message_id,
        }
        STATE_FILE.write_text(json.dumps(data))

    @classmethod
    def load(cls) -> "CLIState":
        """从文件加载状态"""
        import json
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                return cls(**data)
            except Exception:
                pass
        return cls()

    def clear(self):
        """清除状态"""
        self.conversation_id = None
        self.session_id = None
        self.parent_message_id = None
        if STATE_FILE.exists():
            STATE_FILE.unlink()
