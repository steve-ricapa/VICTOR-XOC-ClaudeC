from __future__ import annotations

from typing import Any, Mapping

from core.actions.action_gateway import ActionGateway


def test_action_gateway_success_flow() -> None:
    gateway = ActionGateway()
    action = {
        "action_id": "action-int-1",
        "type": "shell",
        "parameters": {"command": ["python", "-c", "print(99)"]},
    }
    context = {
        "run_id": "run-int-1",
        "capability_level": "C1_RESTRINGIDO",
    }

    result = gateway.handle_action(action=action, context=context)

    assert result["status"] == "ALLOWED"
    assert result["result"]["success"] is True
    assert result["run_id"] == "run-int-1"
    assert result["action_id"] == "action-int-1"
    assert result["timestamp"]


def test_action_gateway_blocked_flow() -> None:
    gateway = ActionGateway()
    action = {
        "action_id": "action-int-2",
        "type": "shell",
        "parameters": {"command": "rm -rf /"},
    }

    result = gateway.handle_action(
        action=action,
        context={"run_id": "run-int-2", "capability_level": "C3_ELEVADO_SUPERVISADO"},
    )

    assert result["status"] == "BLOCKED"
    assert result["blocked"] is True
    assert result["error_type"] == "POLICY_BLOCKED"


def test_action_gateway_decision_flow() -> None:
    gateway = ActionGateway()
    action = {
        "action_id": "action-int-3",
        "type": "shell",
        "parameters": {"command": "sudo ls /"},
    }

    result = gateway.handle_action(
        action=action,
        context={"run_id": "run-int-3", "ticket_id": "ticket-int-3", "capability_level": "C2_CONTROLADO"},
    )

    pending = result.get("pending_decision") or {}

    assert result["status"] == "WAITING_DECISION"
    assert pending.get("decision_id")
    assert pending.get("question")
    assert isinstance(pending.get("options"), list)
    assert pending.get("recommended_option")
    assert pending.get("risk_level")
    assert pending.get("created_at")
    assert pending.get("expires_at")


def test_action_gateway_handles_policy_engine_failure_without_crash() -> None:
    class BrokenPolicyEngine:
        def validate(self, action: Any, context: Mapping[str, Any]) -> dict[str, Any]:
            raise RuntimeError("simulated policy failure")

    gateway = ActionGateway(policy_engine_module=BrokenPolicyEngine())
    result = gateway.handle_action(
        action={"action_id": "action-int-4", "type": "shell", "parameters": {"command": ["python", "-c", "print(1)"]}},
        context={"run_id": "run-int-4", "capability_level": "C1_RESTRINGIDO"},
    )

    assert result["status"] == "FAILED"
    assert result["error"]
    assert result["error_type"] in {"UNKNOWN", "TOOL_ERROR"}


def test_action_gateway_classifies_execution_timeout() -> None:
    class AllowAllPolicy:
        @staticmethod
        def validate(action: Any, context: Mapping[str, Any]) -> dict[str, Any]:
            return {"status": "ALLOWED", "reason": "test"}

    class TimeoutExecutionService:
        @staticmethod
        def execute(action: Any, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
            raise TimeoutError("execution timed out")

    gateway = ActionGateway(
        policy_engine_module=AllowAllPolicy(),
        execution_service_module=TimeoutExecutionService(),
    )

    result = gateway.handle_action(
        action={"action_id": "action-int-5", "type": "shell", "parameters": {"command": ["python", "-c", "print(1)"]}},
        context={"run_id": "run-int-5", "capability_level": "C1_RESTRINGIDO"},
    )

    assert result["status"] == "FAILED"
    assert result["error_type"] == "TIMEOUT"
