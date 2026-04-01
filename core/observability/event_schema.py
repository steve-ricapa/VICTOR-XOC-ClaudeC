from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


SCHEMA_VERSION = "1.0"


class EventPhase(str, Enum):
    PLAN = "PLAN"
    POLICY = "POLICY"
    EXEC = "EXEC"
    VERIFY = "VERIFY"
    ERROR = "ERROR"
    DECISION_REQUIRED = "DECISION_REQUIRED"
    DECISION_CREATED = "DECISION_CREATED"
    DECISION_TIMEOUT = "DECISION_TIMEOUT"
    EXECUTION_TRACE = "EXECUTION_TRACE"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTION_COMPLETED = "EXECUTION_COMPLETED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    PROMPT_SENT = "PROMPT_SENT"
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
    PARSING_RESULT = "PARSING_RESULT"
    BLOCKED = "BLOCKED"


@dataclass(slots=True)
class EventRecord:
    timestamp: str
    phase: str
    message: str
    run_id: str
    action_id: str | None = None
    correlation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.phase = str(self.phase or "ERROR").upper()
        if self.phase not in {phase.value for phase in EventPhase}:
            self.phase = EventPhase.EXECUTION_TRACE.value

        self.message = str(self.message or "")
        if not self.message:
            self.message = "Event emitted"

        self.run_id = str(self.run_id or "unknown-run")

        if not self.timestamp:
            self.timestamp = now_iso()

        if not isinstance(self.metadata, dict):
            self.metadata = dict(self.metadata or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "phase": self.phase,
            "message": self.message,
            "action_id": self.action_id,
            "run_id": self.run_id,
            "correlation_id": self.correlation_id,
            "metadata": dict(self.metadata),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> EventRecord:
        metadata_raw = payload.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        return cls(
            timestamp=str(payload.get("timestamp") or now_iso()),
            phase=str(payload.get("phase") or EventPhase.EXECUTION_TRACE.value),
            message=str(payload.get("message") or "Event emitted"),
            action_id=str(payload.get("action_id")) if payload.get("action_id") is not None else None,
            run_id=str(payload.get("run_id") or metadata.get("run_id") or "unknown-run"),
            correlation_id=(
                str(payload.get("correlation_id"))
                if payload.get("correlation_id") is not None
                else (str(metadata.get("correlation_id")) if metadata.get("correlation_id") is not None else None)
            ),
            metadata=metadata,
            schema_version=str(payload.get("schema_version") or SCHEMA_VERSION),
        )


def validate_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    return EventRecord.from_payload(payload).to_dict()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


Event = EventRecord
