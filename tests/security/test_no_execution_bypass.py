from __future__ import annotations

from core.actions.action_gateway import ActionGateway
from core.execution.execution_service import ExecutionService


def test_direct_execution_service_call_is_blocked_without_policy_approval() -> None:
    service = ExecutionService()
    action = {
        "type": "shell",
        "parameters": {"command": ["python", "-c", "print(1)"]},
    }

    result = service.execute(action, context={"run_id": "run-bypass-1"})

    assert result["success"] is False
    assert result["status"] == "FAILED"
    assert result["error_type"] == "ENVIRONMENT_ERROR"
    assert "policy decision" in result["stderr"].lower()


def test_direct_execution_service_call_succeeds_with_explicit_policy_allow() -> None:
    service = ExecutionService()
    action = {
        "type": "shell",
        "parameters": {"command": ["python", "-c", "print(7)"]},
    }

    result = service.execute(
        action,
        context={
            "run_id": "run-bypass-2",
            "policy_decision": "ALLOWED",
            "capability_level": "C1_RESTRINGIDO",
        },
    )

    assert result["success"] is True
    assert result["status"] == "COMPLETED"
    assert "7" in result["stdout"]


def test_action_gateway_is_required_control_path_for_execution() -> None:
    service = ExecutionService()
    gateway = ActionGateway(execution_service_module=service)

    action = {
        "action_id": "action-bypass-3",
        "type": "shell",
        "parameters": {"command": ["python", "-c", "print(42)"]},
    }

    direct_result = service.execute(action, context={"run_id": "run-bypass-3"})
    gateway_result = gateway.handle_action(
        action=action,
        context={
            "run_id": "run-bypass-3",
            "capability_level": "C1_RESTRINGIDO",
        },
    )

    assert direct_result["success"] is False
    assert gateway_result["status"] == "ALLOWED"
    assert gateway_result["result"]["success"] is True
