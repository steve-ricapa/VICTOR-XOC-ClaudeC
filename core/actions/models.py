from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from core.contracts.action import Action as ContractAction


@dataclass(slots=True)
class ActionParameters:
    values: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.values)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> ActionParameters:
        return cls(values=dict(payload or {}))


@dataclass(slots=True)
class Action:
    action_id: str
    action_type: str
    parameters: ActionParameters
    description: str
    risk_level: str | None = None
    command: str | list[str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.action_type = str(self.action_type or "").lower().strip()
        if self.action_type not in {"shell", "file", "http", "mcp"}:
            raise ValueError(f"Unsupported action_type: {self.action_type}")
        if not self.action_id:
            self.action_id = f"action-{uuid4()}"
        if not self.description:
            self.description = f"{self.action_type} action"
        if self.command is None and self.action_type == "shell":
            self.command = self.parameters.values.get("command")
        if self.command is not None:
            self.parameters.values.setdefault("command", self.command)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "action_id": self.action_id,
            "type": self.action_type,
            "action_type": self.action_type,
            "parameters": self.parameters.to_dict(),
            "description": self.description,
            "risk_level": self.risk_level,
            "metadata": dict(self.metadata),
        }
        if self.command is not None:
            payload["command"] = self.command
        return payload

    def to_contract(self) -> ContractAction:
        return ContractAction.from_payload(self.to_dict())

    @classmethod
    def from_contract(cls, contract: ContractAction) -> Action:
        payload = contract.to_dict()
        return cls.from_payload(payload)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Action:
        action_type = str(payload.get("type") or payload.get("action_type") or "").lower().strip()
        params_raw = payload.get("parameters")
        parameters = ActionParameters.from_payload(params_raw if isinstance(params_raw, Mapping) else {})
        command = payload.get("command")
        if command is None and action_type == "shell":
            command = parameters.values.get("command")

        return cls(
            action_id=str(payload.get("action_id") or f"action-{uuid4()}"),
            action_type=action_type,
            parameters=parameters,
            description=str(payload.get("description") or ""),
            risk_level=str(payload.get("risk_level")) if payload.get("risk_level") is not None else None,
            command=command,
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(slots=True)
class ActionExecutionResult:
    action_id: str
    status: str
    success: bool
    output: Any = None
    error: str | None = None
    error_type: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    duration_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "status": self.status,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "error_type": self.error_type,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "metadata": dict(self.metadata),
        }
