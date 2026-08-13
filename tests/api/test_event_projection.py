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

    assert projected is not None
    assert projected["data"]["content"] == (
        "Please inspect this.\n\n"
        "[The user attached 1 protected file(s) to this message.]"
    )
    assert "Payroll-Alice.xlsx" not in str(projected)
    assert "Payroll-Alice.xlsx" in raw["data"]["content"]


def test_admin_privacy_projection_suppresses_artifact_live_payloads(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PRIVACY_MODE", True)
    events = [
        {
            "type": "artifact_created",
            "data": {
                "id": "payroll-alice.xlsx",
                "title": "Payroll-Alice.xlsx",
                "source": "user_upload",
                "original_filename": "Payroll-Alice.xlsx",
                "content": "employee,salary\nAlice,100000",
            },
        },
        {
            "type": "artifact_updated",
            "data": {
                "id": "payroll-alice.xlsx",
                "content": "rewritten private payroll",
                "delta": {
                    "offset": 0,
                    "deleted_len": 0,
                    "inserted_text": "private delta",
                },
            },
        },
    ]

    assert [project_event_for_admin(event) for event in events] == [None, None]


def test_admin_privacy_projection_keeps_semantic_tool_events(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PRIVACY_MODE", True)
    raw = {
        "type": "tool_complete",
        "data": {
            "tool": "read_artifact",
            "success": True,
            "result_data": "diagnostic content remains available",
        },
    }

    assert project_event_for_admin(raw) == raw
