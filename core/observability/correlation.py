from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping
from uuid import uuid4


def generate_correlation_id(prefix: str = "corr") -> str:
    return f"{prefix}-{uuid4()}"


def build_run_correlation_id(run_id: str | None, ticket_id: str | None = None) -> str:
    if not run_id:
        return generate_correlation_id("corr")
    seed = f"{ticket_id or 'ticket'}:{run_id}"
    digest = sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"run-{digest}"


def extract_correlation_id(payload: Mapping[str, Any] | None) -> str | None:
    if payload is None:
        return None
    for key in ("correlation_id", "trace_id", "correlation", "trace"):
        value = payload.get(key)
        if value:
            return str(value)

    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        value = metadata.get("correlation_id") or metadata.get("trace_id")
        if value:
            return str(value)
    return None


def ensure_correlation_id(payload: Mapping[str, Any] | None, *, run_id: str | None = None, ticket_id: str | None = None) -> str:
    existing = extract_correlation_id(payload)
    if existing:
        return existing

    if run_id:
        return build_run_correlation_id(run_id=run_id, ticket_id=ticket_id)
    return generate_correlation_id("corr")


def attach_correlation(payload: Mapping[str, Any], correlation_id: str) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["correlation_id"] = correlation_id
    metadata = enriched.get("metadata")
    metadata_map = dict(metadata) if isinstance(metadata, Mapping) else {}
    metadata_map.setdefault("correlation_id", correlation_id)
    enriched["metadata"] = metadata_map
    return enriched


@dataclass(slots=True)
class CorrelationManager:
    default_prefix: str = "corr"

    def create(self, *, run_id: str | None = None, ticket_id: str | None = None) -> str:
        if run_id:
            return build_run_correlation_id(run_id=run_id, ticket_id=ticket_id)
        return generate_correlation_id(self.default_prefix)

    def ensure(self, payload: Mapping[str, Any] | None, *, run_id: str | None = None, ticket_id: str | None = None) -> str:
        return ensure_correlation_id(payload=payload, run_id=run_id, ticket_id=ticket_id)
