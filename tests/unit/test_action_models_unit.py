from __future__ import annotations

from core.actions.models import Action, ActionExecutionResult, ActionParameters


def test_action_model_normalizes_shell_payload() -> None:
    action = Action(
        action_id="action-unit-1",
        action_type="shell",
        parameters=ActionParameters({"command": ["python", "-c", "print(1)"]}),
        description="unit test shell action",
    )

    payload = action.to_dict()

    assert payload["type"] == "shell"
    assert payload["action_type"] == "shell"
    assert payload["parameters"]["command"][0] == "python"


def test_action_execution_result_to_dict_is_stable() -> None:
    result = ActionExecutionResult(
        action_id="action-unit-2",
        status="COMPLETED",
        success=True,
        output={"ok": True},
    )

    payload = result.to_dict()

    assert payload["action_id"] == "action-unit-2"
    assert payload["status"] == "COMPLETED"
    assert payload["success"] is True
