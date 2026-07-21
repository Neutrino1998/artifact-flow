from api.event_projection import project_event_for_user


def test_agent_start_projection_is_allowlisted_and_non_mutating():
    raw = {
        "type": "agent_start",
        "agent": "lead_agent",
        "data": {
            "agent": "lead_agent",
            "system_prompt": "internal prompt",
            "reminder": "internal reminder",
            "future_internal_field": "internal by default",
        },
        "_stream_id": "7-0",
    }

    projected = project_event_for_user(raw)

    assert projected == {
        "type": "agent_start",
        "agent": "lead_agent",
        "data": {"agent": "lead_agent"},
        "_stream_id": "7-0",
    }
    assert raw["data"] == {
        "agent": "lead_agent",
        "system_prompt": "internal prompt",
        "reminder": "internal reminder",
        "future_internal_field": "internal by default",
    }
