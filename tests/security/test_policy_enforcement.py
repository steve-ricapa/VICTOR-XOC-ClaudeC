from __future__ import annotations

import json
from pathlib import Path

from core.actions.action_gateway import ActionGateway
from core.observability.audit_logger import AuditLogger
from core.observability.emitter import EmitterConfig, EventEmitter
from core.policy.policy_engine import PolicyEngine


def test_c1_blocks_sudo_and_network_commands() -> None:
    engine = PolicyEngine()

    sudo_result = engine.validate(
        {"type": "shell", "command": "sudo ls /"},
        {"capability_level": "C1_RESTRINGIDO"},
    )
    net_result = engine.validate(
        {"type": "shell", "command": "curl https://example.com"},
        {"capability_level": "C1_RESTRINGIDO"},
    )

    assert sudo_result["status"] == "BLOCKED"
    assert net_result["status"] == "BLOCKED"


def test_c2_requires_decision_for_elevation() -> None:
    engine = PolicyEngine()

    result = engine.validate(
        {"type": "shell", "command": "sudo ls /"},
        {"capability_level": "C2_CONTROLADO"},
    )

    assert result["status"] == "REQUIRES_DECISION"
    assert result["required_permission"] == "ELEVATED_SHELL"


def test_c3_allows_elevated_actions_at_policy_level() -> None:
    engine = PolicyEngine()

    result = engine.validate(
        {"type": "shell", "command": "sudo ls /"},
        {"capability_level": "C3_ELEVADO_SUPERVISADO"},
    )

    assert result["status"] == "ALLOWED"


def test_c3_gateway_execution_is_logged_with_required_fields(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    logger = AuditLogger(
        emitter_module=EventEmitter(config=EmitterConfig(runtime_dir=str(runtime_dir))),
    )

    gateway = ActionGateway(audit_logger_module=logger)
    action = {
        "action_id": "action-c3-1",
        "type": "shell",
        "parameters": {"command": ["python", "-c", "print(123)"]},
    }
    context = {
        "run_id": "run-c3-1",
        "capability_level": "C3_ELEVADO_SUPERVISADO",
    }

    result = gateway.handle_action(action=action, context=context)
    assert result["status"] == "ALLOWED"
    assert result["result"]["success"] is True

    audit_file = runtime_dir / "audit" / "events.jsonl"
    assert audit_file.exists()

    lines = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines

    phases = {event["phase"] for event in lines}
    assert "EXECUTION_STARTED" in phases
    assert "EXECUTION_COMPLETED" in phases

    for event in lines:
        assert event.get("run_id")
        assert event.get("phase")
        assert event.get("timestamp")
        assert event.get("message")


def test_sensitive_values_are_redacted_in_audit_logs(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    logger = AuditLogger(
        emitter_module=EventEmitter(config=EmitterConfig(runtime_dir=str(runtime_dir))),
    )

    payload = logger.log_event(
        {
            "phase": "EXECUTION_TRACE",
            "message": "Authorization: Bearer very-secret-token path /home/alice/project/.env",
            "run_id": "run-redact-1",
            "action_id": "action-redact-1",
            "metadata": {
                "api_key": "abc123",
                "password": "p@ssw0rd",
                "ticket_id": "ticket-1",
            },
        }
    )

    assert "[REDACTED]" in payload["message"]
    assert "[REDACTED_PATH]" in payload["message"]
    assert payload["metadata"]["api_key"] == "[REDACTED]"
    assert payload["metadata"]["password"] == "[REDACTED]"
