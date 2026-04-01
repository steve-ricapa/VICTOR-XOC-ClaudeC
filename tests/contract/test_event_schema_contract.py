from __future__ import annotations

from core.observability import event_schema


def test_event_schema_contains_required_contract_fields() -> None:
    payload = event_schema.validate_event(
        {
            "phase": "PLAN",
            "message": "contract-check",
            "run_id": "run-contract-1",
            "action_id": "action-contract-1",
            "metadata": {"ticket_id": "ticket-1"},
        }
    )

    assert payload["timestamp"]
    assert payload["phase"] == "PLAN"
    assert payload["message"] == "contract-check"
    assert payload["run_id"] == "run-contract-1"
    assert payload["action_id"] == "action-contract-1"
    assert payload["schema_version"] == event_schema.SCHEMA_VERSION
    assert isinstance(payload["metadata"], dict)


def test_unknown_phase_is_normalized_for_contract_stability() -> None:
    payload = event_schema.validate_event(
        {
            "phase": "CUSTOM_PHASE",
            "message": "unknown phase",
            "run_id": "run-contract-2",
        }
    )

    assert payload["phase"] == "EXECUTION_TRACE"
