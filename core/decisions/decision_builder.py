from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import uuid4


def build_decision_request(
    action: Any,
    context: Mapping[str, Any] | None,
    policy_result: Mapping[str, Any] | None,
    *,
    timeout_seconds: int,
    timeout_behavior: str,
) -> dict[str, Any]:
    normalized_action = _normalize_action(action)
    normalized_context = dict(context or {})
    normalized_policy = dict(policy_result or {})

    created_at = _now()
    expires_at = created_at + timedelta(seconds=max(1, int(timeout_seconds)))

    action_summary = _action_summary(normalized_action)
    risk_level = str(
        normalized_policy.get("risk_level")
        or (normalized_policy.get("details") or {}).get("risk_level")
        or normalized_action.get("risk_level")
        or "MEDIUM"
    ).upper()

    options = _options_for_risk(risk_level)
    recommended_option = _recommended_option(risk_level, options)

    question = (
        "Do you approve this action before execution?\n"
        f"Action: {action_summary}\n"
        f"Policy reason: {normalized_policy.get('reason') or 'Manual review required.'}"
    )

    payload = {
        "decision_id": str(uuid4()),
        "run_id": str(normalized_context.get("run_id") or "unknown-run"),
        "ticket_id": str(normalized_context.get("ticket_id") or "unknown-ticket"),
        "action_id": str(
            normalized_action.get("action_id")
            or normalized_action.get("id")
            or normalized_context.get("action_id")
            or f"action-{uuid4()}"
        ),
        "question": question,
        "options": options,
        "recommended_option": recommended_option,
        "risk_level": risk_level,
        "risk_explanation": _risk_explanation(risk_level, normalized_action, normalized_policy),
        "action_summary": action_summary,
        "action_preview": normalized_action,
        "policy_result": normalized_policy,
        "timeout_behavior": timeout_behavior,
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "status": "PENDING",
    }
    return payload


def build_decision(
    action: Any,
    context: Mapping[str, Any] | None,
    policy_result: Mapping[str, Any] | None,
    *,
    timeout_seconds: int,
    timeout_behavior: str,
) -> dict[str, Any]:
    return build_decision_request(
        action=action,
        context=context,
        policy_result=policy_result,
        timeout_seconds=timeout_seconds,
        timeout_behavior=timeout_behavior,
    )


def create_decision_request(
    action: Any,
    context: Mapping[str, Any] | None,
    policy_result: Mapping[str, Any] | None,
    *,
    timeout_seconds: int,
    timeout_behavior: str,
) -> dict[str, Any]:
    return build_decision_request(
        action=action,
        context=context,
        policy_result=policy_result,
        timeout_seconds=timeout_seconds,
        timeout_behavior=timeout_behavior,
    )


def _normalize_action(action: Any) -> dict[str, Any]:
    if isinstance(action, Mapping):
        payload = dict(action)
    elif hasattr(action, "to_dict") and callable(getattr(action, "to_dict")):
        value = action.to_dict()
        payload = dict(value) if isinstance(value, Mapping) else {"raw": value}
    elif hasattr(action, "__dict__"):
        payload = {k: v for k, v in vars(action).items() if not k.startswith("_")}
    else:
        payload = {"raw": str(action)}

    parameters = payload.get("parameters")
    if isinstance(parameters, Mapping):
        for key, value in parameters.items():
            payload.setdefault(str(key), value)
    return payload


def _action_summary(action: Mapping[str, Any]) -> str:
    action_type = str(action.get("type") or action.get("action_type") or "unknown").lower()
    if action_type == "shell":
        command = action.get("command") or action.get("parameters", {}).get("command")
        return f"Shell command: {command}"
    if action_type == "file":
        operation = action.get("operation") or action.get("parameters", {}).get("operation")
        path = action.get("path") or action.get("parameters", {}).get("path")
        return f"File operation '{operation}' on {path}"
    if action_type == "http":
        method = action.get("method") or action.get("parameters", {}).get("method") or "GET"
        url = action.get("url") or action.get("parameters", {}).get("url")
        return f"HTTP {method} {url}"
    if action_type == "mcp":
        tool = action.get("tool") or action.get("parameters", {}).get("tool")
        return f"MCP tool invocation: {tool}"
    return f"Action type '{action_type}'"


def _options_for_risk(risk_level: str) -> list[dict[str, Any]]:
    approve_text = "Approve this action for immediate execution"
    deny_text = "Deny this action"
    pause_text = "Pause and request additional investigation"

    if risk_level in {"HIGH", "CRITICAL"}:
        approve_text = "Approve with elevated risk acknowledgement"

    return [
        {"id": "A", "label": "Approve", "description": approve_text},
        {"id": "B", "label": "Deny", "description": deny_text},
        {"id": "C", "label": "Pause", "description": pause_text},
    ]


def _recommended_option(risk_level: str, options: list[dict[str, Any]]) -> str:
    option_ids = {str(option.get("id")) for option in options}
    if risk_level in {"CRITICAL", "HIGH"} and "C" in option_ids:
        return "C"
    if risk_level in {"MEDIUM"} and "A" in option_ids:
        return "A"
    if "A" in option_ids:
        return "A"
    return options[0]["id"] if options else "A"


def _risk_explanation(
    risk_level: str,
    action: Mapping[str, Any],
    policy_result: Mapping[str, Any],
) -> str:
    base = {
        "LOW": "Low impact operation with limited blast radius.",
        "MEDIUM": "Moderate impact operation requiring operator awareness.",
        "HIGH": "High impact operation with potential service disruption.",
        "CRITICAL": "Critical operation with elevated operational risk.",
    }.get(risk_level, "Unclassified risk level; manual review required.")

    reason = policy_result.get("reason")
    if reason:
        return f"{base} Policy reason: {reason}"

    description = action.get("description")
    if description:
        return f"{base} Action description: {description}"
    return base


def _now() -> datetime:
    return datetime.now(timezone.utc)
