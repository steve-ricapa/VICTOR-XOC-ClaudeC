from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import uuid4


ALLOWED_ACTION_TYPES = {"shell", "file", "http", "mcp"}


@dataclass
class Action:
    action_id: str
    type: str
    command: str | list[str] | None
    parameters: dict[str, Any]
    description: str
    risk_level: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.type = str(self.type or "").strip().lower()
        if self.type not in ALLOWED_ACTION_TYPES:
            raise ValueError(f"Unsupported action type: {self.type}")

        if not self.action_id:
            self.action_id = f"action-{uuid4()}"

        if not isinstance(self.parameters, dict):
            self.parameters = dict(self.parameters or {})

        if self.type == "shell" and self.command is None:
            self.command = self.parameters.get("command")

        if self.command is not None and self.type != "shell":
            self.parameters.setdefault("command", self.command)

        self.description = str(self.description or "")
        if not self.description:
            self.description = f"Accion propuesta de tipo {self.type}"

        if self.confidence is not None:
            value = float(self.confidence)
            if value < 0.0:
                value = 0.0
            if value > 1.0:
                value = 1.0
            self.confidence = value

    @property
    def action_type(self) -> str:
        return self.type

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "action_id": self.action_id,
            "type": self.type,
            "action_type": self.type,
            "parameters": dict(self.parameters),
            "description": self.description,
            "risk_level": self.risk_level,
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
        }
        if self.command is not None:
            payload["command"] = self.command
            if self.type == "shell":
                payload["parameters"].setdefault("command", self.command)
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Action:
        action_type = str(payload.get("type") or payload.get("action_type") or "").strip().lower()
        parameters_raw = payload.get("parameters")
        parameters = dict(parameters_raw) if isinstance(parameters_raw, Mapping) else {}

        command = payload.get("command")
        if command is None and action_type == "shell":
            command = parameters.get("command")

        description = str(payload.get("description") or "").strip()
        risk_level = payload.get("risk_level")
        confidence_raw = payload.get("confidence")

        confidence: float | None = None
        if confidence_raw is not None:
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                confidence = None

        metadata_raw = payload.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}

        return cls(
            action_id=str(payload.get("action_id") or f"action-{uuid4()}"),
            type=action_type,
            command=command,
            parameters=parameters,
            description=description,
            risk_level=str(risk_level) if risk_level is not None else None,
            confidence=confidence,
            metadata=metadata,
        )

    @classmethod
    def safe_fallback(cls, reason: str = "Respuesta del modelo malformada") -> Action:
        return cls(
            action_id=f"action-{uuid4()}",
            type="file",
            command=None,
            parameters={
                "operation": "exists",
                "path": ".",
            },
            description="Accion de respaldo segura por fallo de parseo",
            risk_level="LOW",
            confidence=0.0,
            metadata={"fallback_reason": reason},
        )
