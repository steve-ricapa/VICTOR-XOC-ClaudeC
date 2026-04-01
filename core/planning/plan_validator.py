from __future__ import annotations

import re
from typing import Any, Mapping

from core.policy import policy_engine


class PlanValidator:
    """Validates plan structure and policy compatibility before execution."""

    _BLOCKED_COMMAND_PATTERNS = [
        re.compile(r"\bcurl\b[^|\n]*\|\s*(bash|sh|zsh|ksh|pwsh|powershell)\b", re.IGNORECASE),
        re.compile(r"\bwget\b[^|\n]*\|\s*(bash|sh|zsh|ksh|pwsh|powershell)\b", re.IGNORECASE),
        re.compile(r"\brm\s+-rf\s+/(\s|$)", re.IGNORECASE),
        re.compile(r"\b(shutdown|reboot)\b", re.IGNORECASE),
    ]

    def __init__(self, *, policy_engine_module: Any = policy_engine) -> None:
        self.policy_engine = policy_engine_module

    def validate_plan(self, plan: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        plan_map = dict(plan)
        context_map = dict(context or {})
        steps = plan_map.get("steps")

        errors: list[str] = []
        warnings: list[str] = []
        step_results: list[dict[str, Any]] = []

        if not isinstance(steps, list) or not steps:
            return {
                "valid": False,
                "ready_for_execution": False,
                "errors": ["Plan has no executable steps"],
                "warnings": [],
                "step_results": [],
            }

        has_requires_decision = False

        for index, raw_step in enumerate(steps, start=1):
            step_map = dict(raw_step) if isinstance(raw_step, Mapping) else {}
            step_id = str(step_map.get("step_id") or f"step-{index}")
            action = self._extract_action(step_map)

            if not action:
                errors.append(f"{step_id}: missing action payload")
                step_results.append({"step_id": step_id, "status": "INVALID", "reason": "missing action"})
                continue

            structural_errors = self._validate_action_structure(action)
            if structural_errors:
                for issue in structural_errors:
                    errors.append(f"{step_id}: {issue}")
                step_results.append({"step_id": step_id, "status": "INVALID", "reason": "; ".join(structural_errors)})
                continue

            pattern_issue = self._check_forbidden_patterns(action)
            if pattern_issue:
                errors.append(f"{step_id}: {pattern_issue}")
                step_results.append({"step_id": step_id, "status": "BLOCKED", "reason": pattern_issue})
                continue

            policy_result = self.policy_engine.validate(action, context_map)
            normalized_policy = self._normalize_policy_result(policy_result)
            decision = normalized_policy.get("status") or normalized_policy.get("decision") or "UNKNOWN"

            if decision == "BLOCKED":
                reason = str(normalized_policy.get("reason") or "Policy blocked action")
                errors.append(f"{step_id}: {reason}")
                step_results.append(
                    {
                        "step_id": step_id,
                        "status": "BLOCKED",
                        "reason": reason,
                        "policy_result": normalized_policy,
                    }
                )
                continue

            if decision == "REQUIRES_DECISION":
                has_requires_decision = True
                warnings.append(
                    f"{step_id}: requires human decision ({normalized_policy.get('reason') or 'manual approval'})"
                )
                step_results.append(
                    {
                        "step_id": step_id,
                        "status": "REQUIRES_DECISION",
                        "reason": normalized_policy.get("reason"),
                        "policy_result": normalized_policy,
                    }
                )
                continue

            step_results.append(
                {
                    "step_id": step_id,
                    "status": "ALLOWED",
                    "policy_result": normalized_policy,
                }
            )

        valid = len(errors) == 0
        return {
            "valid": valid,
            "ready_for_execution": valid and not has_requires_decision,
            "errors": errors,
            "warnings": warnings,
            "step_results": step_results,
        }

    def validate(self, plan: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.validate_plan(plan=plan, context=context)

    @staticmethod
    def _extract_action(step: Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(step.get("action"), Mapping):
            action = dict(step["action"])
        else:
            action = dict(step)

        parameters = action.get("parameters")
        if isinstance(parameters, Mapping):
            for key, value in parameters.items():
                action.setdefault(str(key), value)
        return action

    @staticmethod
    def _validate_action_structure(action: Mapping[str, Any]) -> list[str]:
        issues: list[str] = []
        action_type = str(action.get("type") or action.get("action_type") or "").lower()
        if action_type not in {"shell", "file", "http", "mcp"}:
            issues.append(f"unsupported action type '{action_type or 'unknown'}'")
            return issues

        if action_type == "shell":
            if action.get("command") is None:
                issues.append("shell action requires command")
        elif action_type == "file":
            if action.get("path") is None:
                issues.append("file action requires path")
        elif action_type == "http":
            if action.get("url") is None:
                issues.append("http action requires url")
        elif action_type == "mcp":
            if action.get("tool") is None:
                issues.append("mcp action requires tool")
        return issues

    def _check_forbidden_patterns(self, action: Mapping[str, Any]) -> str | None:
        action_type = str(action.get("type") or action.get("action_type") or "").lower()
        if action_type != "shell":
            return None
        command = action.get("command")
        if command is None:
            return None
        command_text = " ".join(str(item) for item in command) if isinstance(command, list) else str(command)
        for pattern in self._BLOCKED_COMMAND_PATTERNS:
            if pattern.search(command_text):
                return "forbidden command pattern detected"
        return None

    @staticmethod
    def _normalize_policy_result(policy_result: Any) -> dict[str, Any]:
        if isinstance(policy_result, Mapping):
            result = dict(policy_result)
        else:
            result = {"status": str(policy_result)}
        if "status" not in result and "decision" in result:
            result["status"] = result["decision"]
        if "decision" not in result and "status" in result:
            result["decision"] = result["status"]
        return result


_DEFAULT_VALIDATOR = PlanValidator()


def validate_plan(plan: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return _DEFAULT_VALIDATOR.validate_plan(plan=plan, context=context)


def validate(plan: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return _DEFAULT_VALIDATOR.validate(plan=plan, context=context)
