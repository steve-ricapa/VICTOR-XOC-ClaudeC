from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


@dataclass(slots=True)
class Event:
    timestamp: str
    phase: str
    message: str
    action_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.phase = str(self.phase or "").strip().upper()
        if not self.phase:
            self.phase = "UNKNOWN"
        self.message = str(self.message or "").strip()
        if not self.message:
            self.message = "No message provided"
        if not self.timestamp:
            self.timestamp = self.now_iso()
        if not isinstance(self.metadata, dict):
            self.metadata = dict(self.metadata or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "phase": self.phase,
            "message": self.message,
            "action_id": self.action_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Event:
        metadata_raw = payload.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        return cls(
            timestamp=str(payload.get("timestamp") or cls.now_iso()),
            phase=str(payload.get("phase") or "UNKNOWN"),
            message=str(payload.get("message") or "No message provided"),
            action_id=str(payload.get("action_id")) if payload.get("action_id") is not None else None,
            metadata=metadata,
        )

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
