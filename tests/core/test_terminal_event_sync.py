"""
终态事件集合一致性守护 (P1#2 回归).

「什么算终态」散在多层:core 是权威定义(TERMINAL_EVENT_TYPES),传输/路由层各自
保留本地副本(故意不依赖执行语义)。本地副本漂移正是 TIMED_OUT 当初漏在传输/路由
层的根因 —— consumer/router 不把 timed_out 当终态退出,后续会再发一个 error 覆盖
前端超时状态。

这组测试把「沉默漂移」变成「响亮的 CI 失败」:新增/删除终态类型时,只要某个本地
副本忘了同步,就在这里红线。
"""

from core.events import TERMINAL_EVENT_TYPES, StreamEventType


def test_canonical_set_matches_expected():
    """权威集合就是 COMPLETE/CANCELLED/TIMED_OUT/ERROR —— 钉死,防误删。"""
    assert TERMINAL_EVENT_TYPES == {
        StreamEventType.COMPLETE.value,
        StreamEventType.CANCELLED.value,
        StreamEventType.TIMED_OUT.value,
        StreamEventType.ERROR.value,
    }


def test_redis_transport_terminal_set_in_sync():
    from api.services.redis_stream_transport import _TERMINAL_EVENTS
    assert set(_TERMINAL_EVENTS) == TERMINAL_EVENT_TYPES, (
        "RedisStreamTransport._TERMINAL_EVENTS 与权威集合漂移 —— "
        "consumer 不会在缺失的终态上退出 (P1#2)"
    )


def test_inmemory_transport_terminal_set_in_sync():
    from api.services.stream_transport import _TERMINAL_EVENTS
    assert set(_TERMINAL_EVENTS) == TERMINAL_EVENT_TYPES, (
        "InMemoryStreamTransport._TERMINAL_EVENTS 与权威集合漂移 (P1#2)"
    )


def test_stream_router_terminal_set_in_sync():
    from api.routers.stream import _TERMINAL_EVENTS
    assert set(_TERMINAL_EVENTS) == TERMINAL_EVENT_TYPES, (
        "stream router._TERMINAL_EVENTS 与权威集合漂移 —— "
        "SSE 不会在缺失的终态上关闭连接 (P1#2)"
    )
