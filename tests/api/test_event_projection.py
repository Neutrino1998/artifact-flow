from api.event_projection import project_event_for_admin, project_event_for_user
from config import config


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


def test_admin_privacy_projection_redacts_upload_hint_without_changing_user_text(
    monkeypatch,
):
    monkeypatch.setattr(config, "ADMIN_PRIVACY_MODE", True)
    raw = {
        "type": "user_input",
        "data": {
            "content": (
                "Please inspect this.\n\n"
                "[The user attached 1 file(s) to this message: Payroll-Alice.xlsx. "
                "Use read_artifact with the id for full content.]"
            )
        },
    }

    projected = project_event_for_admin(raw)

    assert projected["data"]["content"] == (
        "Please inspect this.\n\n"
        "[The user attached 1 protected file(s) to this message.]"
    )
    assert "Payroll-Alice.xlsx" not in str(projected)
    assert "Payroll-Alice.xlsx" in raw["data"]["content"]
