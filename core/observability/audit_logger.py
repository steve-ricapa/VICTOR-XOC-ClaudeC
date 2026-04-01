from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from core.contracts.event import Event
from core.observability import correlation
from core.observability import emitter
from core.observability import event_schema
from core.observability import redaction


@dataclass(slots=True)
class AuditLoggerConfig:
    enforce_schema: bool = True
    redact_sensitive_data: bool = True


class AuditLogger:
    """Central structured audit logger with redaction and correlation."""

    def __init__(
        self,
        *,
        config: AuditLoggerConfig | None = None,
        emitter_module: Any = emitter,
        redactor: redaction.Redactor | None = None,
        correlation_manager: correlation.CorrelationManager | None = None,
    ) -> None:
        self.config = config or AuditLoggerConfig()
        self.emitter = emitter_module
        self.redactor = redactor or redaction.Redactor()
        self.correlation_manager = correlation_manager or correlation.CorrelationManager()
        self._fallback_events: list[dict[str, Any]] = []

    def log_event(self, event: Event | Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        payload = self._normalize_event_payload(event, kwargs)

        run_id = str(payload.get("run_id") or payload.get("metadata", {}).get("run_id") or "unknown-run")
        ticket_id = None
        metadata = payload.get("metadata")
        if isinstance(metadata, Mapping) and metadata.get("ticket_id") is not None:
            ticket_id = str(metadata.get("ticket_id"))

        corr_id = self.correlation_manager.ensure(payload, run_id=run_id, ticket_id=ticket_id)
        payload = correlation.attach_correlation(payload, corr_id)

        if self.config.enforce_schema:
            payload = event_schema.validate_event(payload)

        if self.config.redact_sensitive_data:
            payload = self.redactor.redact_data(payload)

        emitted = self._emit(payload)
        if not emitted:
            self._fallback_events.append(payload)

        return payload

    def emit(self, event: Event | Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.log_event(event, **kwargs)

    def log(self, event: Event | Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.log_event(event, **kwargs)

    def write(self, event: Event | Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.log_event(event, **kwargs)

    def record(self, event: Event | Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.log_event(event, **kwargs)

    def _emit(self, payload: Mapping[str, Any]) -> bool:
        for method_name in ("emit", "log_event", "log", "write", "record"):
            method = getattr(self.emitter, method_name, None)
            if not callable(method):
                continue
            for args, kwargs in (((payload,), {}), ((), {"event": payload}), ((), dict(payload))):
                try:
                    method(*args, **kwargs)
                    return True
                except TypeError:
                    continue
                except Exception:
                    return False
        return False

    @staticmethod
    def _normalize_event_payload(
        event: Event | Mapping[str, Any] | None,
        kwargs: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any]
        if event is None:
            payload = {}
        elif isinstance(event, Event):
            payload = event.to_dict()
        elif isinstance(event, Mapping):
            payload = dict(event)
        else:
            payload = {
                "timestamp": event_schema.now_iso(),
                "phase": "EXECUTION_TRACE",
                "message": str(event),
                "run_id": "unknown-run",
                "metadata": {},
            }

        if kwargs:
            payload.update({k: v for k, v in kwargs.items() if k != "event"})
        payload.setdefault("timestamp", event_schema.now_iso())
        payload.setdefault("phase", "EXECUTION_TRACE")
        payload.setdefault("message", "Event emitted")
        payload.setdefault("run_id", "unknown-run")
        payload.setdefault("metadata", {})
        return payload


_DEFAULT_LOGGER = AuditLogger()


def log_event(event: Event | Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT_LOGGER.log_event(event, **kwargs)


def emit(event: Event | Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT_LOGGER.emit(event, **kwargs)


def log(event: Event | Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT_LOGGER.log(event, **kwargs)


def write(event: Event | Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT_LOGGER.write(event, **kwargs)


def record(event: Event | Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT_LOGGER.record(event, **kwargs)
