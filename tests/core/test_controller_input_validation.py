"""
ExecutionController.stream_execute 入口不变量测试。

核心入口自己保证前置条件：一轮执行需要非空输入（文本或附件）。空文本且无附件会让
USER_INPUT 事件正文为空 → 被 EventHistory 过滤 → 空 history → ContextManager.build
在 all_messages[-1] 崩。stream_execute 在任何 yield / DB 写之前就拒掉（router 另留
422 作为 HTTP 快速边界），不依赖调用方先校验。
"""

from unittest.mock import AsyncMock

import pytest

from core.controller import ExecutionController
from core.engine import EngineHooks


def _make_controller() -> ExecutionController:
    """最小可用 controller —— 入口校验在任何依赖被触达之前触发，故无需 DB / repo。"""
    hooks = EngineHooks(
        check_cancelled=AsyncMock(return_value=False),
        wait_for_interrupt=AsyncMock(return_value=None),
        drain_messages=AsyncMock(return_value=[]),
    )
    return ExecutionController(agents={}, tools={}, effective_toolsets={}, hooks=hooks)


class TestStreamExecuteInputValidation:

    async def test_none_input_rejected(self):
        ctrl = _make_controller()
        with pytest.raises(ValueError, match="required"):
            async for _ in ctrl.stream_execute(user_input=None):
                pass

    @pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
    async def test_blank_input_no_attachments_rejected(self, blank):
        """空 / 纯空白文本且无附件 → ValueError（核心侧不变量，不靠 router）。"""
        ctrl = _make_controller()
        with pytest.raises(ValueError, match="non-empty"):
            async for _ in ctrl.stream_execute(user_input=blank):
                pass

    async def test_blank_input_with_activate_skills_passes_guard(self):
        """空文本 + activate_skills → 不被空输入闸拒(engine 会注入 skill 正文补足正文,
        mirrors force_compact)。空输入闸在任何 yield 前 raise ValueError;能跑到产出事件
        (过闸后无 DB 依赖只会 yield error 事件、不再 raise)即证放行。"""
        ctrl = _make_controller()
        events = []
        async for e in ctrl.stream_execute(user_input="", activate_skills=["s"]):
            events.append(e)
        assert events  # 过闸(否则 ValueError 会在 yield 前抛出)
