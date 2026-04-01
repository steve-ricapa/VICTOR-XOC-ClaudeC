from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

import yaml

from core.actions import action_gateway
from core.observability import audit_logger


@dataclass(slots=True)
class PlaybookStepResult:
    step_id: str
    status: str
    gateway_result: dict[str, Any]
    started_at: str
    finished_at: str
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status,
            "gateway_result": dict(self.gateway_result),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(slots=True)
class PlaybookExecutionResult:
    execution_id: str
    playbook_name: str
    status: str
    steps: list[PlaybookStepResult] = field(default_factory=list)
    rollback_steps: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "playbook_name": self.playbook_name,
            "status": self.status,
            "steps": [step.to_dict() for step in self.steps],
            "rollback_steps": list(self.rollback_steps),
            "errors": list(self.errors),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class PlaybookEngine:
    """Loads, validates, and executes YAML playbooks through action_gateway."""

    def __init__(
        self,
        *,
        action_gateway_module: Any = action_gateway,
        audit_logger_module: Any = audit_logger,
    ) -> None:
        self.action_gateway = action_gateway_module
        self.audit_logger = audit_logger_module
        self._fallback_logs: list[dict[str, Any]] = []

    def load_playbook(self, source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(source, Mapping):
            payload = dict(source)
        else:
            text = self._read_source(source)
            loaded = yaml.safe_load(text)
            if not isinstance(loaded, Mapping):
                raise ValueError("Playbook must deserialize to an object")
            payload = dict(loaded)

        payload.setdefault("name", "unnamed-playbook")
        payload.setdefault("version", "1")
        payload.setdefault("steps", [])
        return payload

    def validate_playbook(self, playbook: Mapping[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []

        name = str(playbook.get("name") or "")
        if not name:
            errors.append("Playbook name is required")

        steps = playbook.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append("Playbook requires at least one step")
            return {"valid": False, "errors": errors, "warnings": warnings}

        for idx, raw_step in enumerate(steps, start=1):
            if not isinstance(raw_step, Mapping):
                errors.append(f"Step {idx} must be an object")
                continue

            action = self._extract_step_action(raw_step)
            if not action:
                errors.append(f"Step {idx} has no action definition")
                continue

            action_type = str(action.get("type") or action.get("action_type") or "").lower()
            if action_type not in {"shell", "file", "http", "mcp"}:
                errors.append(f"Step {idx} has unsupported action type '{action_type or 'unknown'}'")

            if action_type == "shell":
                command = action.get("command") or action.get("parameters", {}).get("command")
                if command is None:
                    errors.append(f"Step {idx} shell action missing command")

            if raw_step.get("rollback") is not None and not isinstance(raw_step.get("rollback"), Mapping):
                errors.append(f"Step {idx} rollback must be an object")

            on_failure = str(raw_step.get("on_failure") or "stop").lower()
            if on_failure not in {"stop", "continue", "rollback"}:
                errors.append(f"Step {idx} has invalid on_failure '{on_failure}'")

        rollback = playbook.get("rollback")
        if rollback is not None and not isinstance(rollback, list):
            errors.append("Top-level rollback must be a list of actions")
        elif isinstance(rollback, list):
            for ridx, item in enumerate(rollback, start=1):
                if not isinstance(item, Mapping):
                    errors.append(f"Rollback action {ridx} must be an object")
                    continue
                action_type = str(item.get("type") or item.get("action_type") or "").lower()
                if action_type not in {"shell", "file", "http", "mcp"}:
                    errors.append(f"Rollback action {ridx} has unsupported action type '{action_type or 'unknown'}'")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }

    def execute_playbook(
        self,
        playbook: Mapping[str, Any] | str | Path,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        loaded = self.load_playbook(playbook)
        validation = self.validate_playbook(loaded)
        if not validation["valid"]:
            return {
                "status": "FAILED",
                "errors": validation["errors"],
                "warnings": validation["warnings"],
                "playbook_name": loaded.get("name"),
            }

        ctx = dict(context or {})
        execution = PlaybookExecutionResult(
            execution_id=f"playbook-run-{uuid4()}",
            playbook_name=str(loaded.get("name") or "unnamed-playbook"),
            status="RUNNING",
        )

        self._audit(
            phase="EXECUTION_TRACE",
            message="Playbook execution started",
            run_id=str(ctx.get("run_id") or execution.execution_id),
            metadata={"playbook": loaded.get("name"), "execution_id": execution.execution_id},
        )

        completed_steps: list[Mapping[str, Any]] = []
        for raw_step in loaded.get("steps", []):
            step = dict(raw_step)
            step_id = str(step.get("id") or step.get("step_id") or f"step-{len(execution.steps)+1}")
            action = self._extract_step_action(step)
            step_ctx = dict(ctx)
            step_ctx.setdefault("run_id", execution.execution_id)

            started = self._now()
            gateway_result = self._dispatch_action(action=action, context=step_ctx)
            finished = self._now()
            status = str(gateway_result.get("status") or "FAILED")

            execution.steps.append(
                PlaybookStepResult(
                    step_id=step_id,
                    status=status,
                    gateway_result=dict(gateway_result),
                    started_at=started.isoformat(),
                    finished_at=finished.isoformat(),
                    duration_seconds=(finished - started).total_seconds(),
                )
            )

            self._audit(
                phase="EXECUTION_TRACE",
                message="Playbook step executed",
                run_id=str(step_ctx.get("run_id") or execution.execution_id),
                metadata={
                    "playbook": loaded.get("name"),
                    "step_id": step_id,
                    "action": action,
                    "result": gateway_result,
                },
            )

            if status == "ALLOWED":
                completed_steps.append(step)
                continue

            if status == "WAITING_DECISION":
                execution.status = "WAITING_DECISION"
                execution.finished_at = self._now().isoformat()
                return execution.to_dict()

            on_failure = str(step.get("on_failure") or "stop").lower()
            execution.errors.append(f"Step {step_id} failed with status {status}")

            if on_failure == "continue":
                continue

            if on_failure == "rollback":
                rollback_results = self._execute_rollback(
                    completed_steps=completed_steps,
                    playbook=loaded,
                    context=ctx,
                )
                execution.rollback_steps.extend(rollback_results)

            execution.status = "FAILED"
            execution.finished_at = self._now().isoformat()
            self._audit(
                phase="ERROR",
                message="Playbook execution failed",
                run_id=str(ctx.get("run_id") or execution.execution_id),
                metadata={
                    "playbook": loaded.get("name"),
                    "step_id": step_id,
                    "errors": execution.errors,
                },
            )
            return execution.to_dict()

        execution.status = "COMPLETED"
        execution.finished_at = self._now().isoformat()
        self._audit(
            phase="EXECUTION_TRACE",
            message="Playbook execution completed",
            run_id=str(ctx.get("run_id") or execution.execution_id),
            metadata={"playbook": loaded.get("name"), "execution_id": execution.execution_id},
        )
        return execution.to_dict()

    def _execute_rollback(
        self,
        *,
        completed_steps: list[Mapping[str, Any]],
        playbook: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        rollback_results: list[dict[str, Any]] = []

        for step in reversed(completed_steps):
            rollback_action = step.get("rollback")
            if not isinstance(rollback_action, Mapping):
                continue
            result = self._dispatch_action(action=dict(rollback_action), context=context)
            rollback_results.append(
                {
                    "source": "step",
                    "step_id": step.get("id") or step.get("step_id"),
                    "action": dict(rollback_action),
                    "result": result,
                }
            )

        top_level = playbook.get("rollback")
        if isinstance(top_level, list):
            for action in top_level:
                if not isinstance(action, Mapping):
                    continue
                result = self._dispatch_action(action=dict(action), context=context)
                rollback_results.append(
                    {
                        "source": "playbook",
                        "action": dict(action),
                        "result": result,
                    }
                )

        return rollback_results

    def _dispatch_action(self, *, action: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        for method_name in ("handle_action", "process_action", "route_action", "dispatch", "execute"):
            method = getattr(self.action_gateway, method_name, None)
            if not callable(method):
                continue
            for args, kwargs in (
                ((), {"action": action, "context": context}),
                ((action,), {"context": context}),
                (({"action": action, "context": context},), {}),
            ):
                try:
                    result = method(*args, **kwargs)
                    return dict(result) if isinstance(result, Mapping) else {"status": "FAILED", "result": result}
                except TypeError:
                    continue
        return {"status": "FAILED", "error": "Action gateway not invokable"}

    @staticmethod
    def _extract_step_action(step: Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(step.get("action"), Mapping):
            action = dict(step["action"])
        else:
            action = {
                key: value
                for key, value in step.items()
                if key not in {"id", "step_id", "description", "on_failure", "rollback"}
            }

        if "parameters" not in action:
            params = {
                key: value
                for key, value in action.items()
                if key not in {"type", "action_type", "command", "description"}
            }
            action["parameters"] = params

        action_type = str(action.get("type") or action.get("action_type") or "").lower()
        if not action_type:
            if "command" in action or "command" in action.get("parameters", {}):
                action_type = "shell"
            elif "tool" in action or "tool" in action.get("parameters", {}):
                action_type = "mcp"
            elif "url" in action or "url" in action.get("parameters", {}):
                action_type = "http"
            else:
                action_type = "file"
        action["type"] = action_type
        action["action_type"] = action_type
        return action

    @staticmethod
    def _read_source(source: str | Path) -> str:
        path = Path(source)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return str(source)

    def _audit(self, *, phase: str, message: str, run_id: str, metadata: Mapping[str, Any]) -> None:
        event = {
            "phase": phase,
            "timestamp": self._now().isoformat(),
            "message": message,
            "run_id": run_id,
            "metadata": dict(metadata),
        }
        emitted = False
        for method_name in ("log_event", "emit", "log", "write", "record"):
            method = getattr(self.audit_logger, method_name, None)
            if not callable(method):
                continue
            for args, kwargs in (((event,), {}), ((), {"event": event}), ((), event)):
                try:
                    method(*args, **kwargs)
                    emitted = True
                    break
                except TypeError:
                    continue
            if emitted:
                break

        if not emitted and callable(self.audit_logger):
            for args, kwargs in (((event,), {}), ((), {"event": event}), ((), event)):
                try:
                    self.audit_logger(*args, **kwargs)
                    emitted = True
                    break
                except TypeError:
                    continue

        if not emitted:
            self._fallback_logs.append(event)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)


_DEFAULT_ENGINE = PlaybookEngine()


def load_playbook(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    return _DEFAULT_ENGINE.load_playbook(source)


def validate_playbook(playbook: Mapping[str, Any]) -> dict[str, Any]:
    return _DEFAULT_ENGINE.validate_playbook(playbook)


def execute_playbook(playbook: Mapping[str, Any] | str | Path, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return _DEFAULT_ENGINE.execute_playbook(playbook, context)
