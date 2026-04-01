from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping

from core.decisions import decision_builder
from core.observability import audit_logger


class TimeoutBehavior(str, Enum):
    DERIVED = "DERIVED"
    AUTO = "AUTO"
    PAUSE = "PAUSE"


class DecisionEngine:
    """Builds and manages human-in-the-loop decision payloads."""

    def __init__(
        self,
        *,
        builder_module: Any = decision_builder,
        audit_logger_module: Any = audit_logger,
        default_timeout_seconds: int = 900,
        default_timeout_behavior: str = TimeoutBehavior.PAUSE.value,
    ) -> None:
        self.builder = builder_module
        self.audit_logger = audit_logger_module
        self.default_timeout_seconds = max(1, int(default_timeout_seconds))
        self.default_timeout_behavior = self._normalize_timeout_behavior(default_timeout_behavior)
        self._fallback_logs: list[dict[str, Any]] = []

    def handle_decision(
        self,
        action: Any,
        context: Mapping[str, Any] | None,
        policy_result: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        normalized_context = dict(context or {})
        normalized_policy = dict(policy_result or {})

        timeout_seconds = self._resolve_timeout_seconds(normalized_context)
        timeout_behavior = self._resolve_timeout_behavior(normalized_context)

        self._audit(
            phase="DECISION_REQUIRED",
            message="Policy requires human decision",
            action=action,
            context=normalized_context,
            metadata={
                "policy_result": normalized_policy,
                "timeout_seconds": timeout_seconds,
                "timeout_behavior": timeout_behavior,
            },
        )

        payload = self._build_decision_payload(
            action=action,
            context=normalized_context,
            policy_result=normalized_policy,
            timeout_seconds=timeout_seconds,
            timeout_behavior=timeout_behavior,
        )

        payload.setdefault("status", "PENDING")
        payload.setdefault("timeout_behavior", timeout_behavior)
        payload.setdefault("created_at", self._ts(self._now()))
        payload.setdefault("expires_at", self._ts(self._now_with_offset(timeout_seconds)))

        self._persist_pending_decision(payload=payload, context=normalized_context)

        self._audit(
            phase="DECISION_CREATED",
            message="Decision payload created",
            action=action,
            context=normalized_context,
            metadata={
                "decision_id": payload.get("decision_id"),
                "recommended_option": payload.get("recommended_option"),
                "risk_level": payload.get("risk_level"),
                "expires_at": payload.get("expires_at"),
            },
        )

        if timeout_seconds <= 0:
            payload = self.apply_timeout(payload=payload)

        return payload

    def apply_timeout(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        decision = dict(payload)
        behavior = self._normalize_timeout_behavior(str(decision.get("timeout_behavior") or self.default_timeout_behavior))
        decision["timed_out_at"] = self._ts(self._now())

        if behavior == TimeoutBehavior.DERIVED.value:
            decision["status"] = "DERIVED"
            decision["derived_option"] = decision.get("recommended_option")
        elif behavior == TimeoutBehavior.AUTO.value:
            decision["status"] = "AUTO_RESOLVED"
            decision["auto_selected_option"] = decision.get("recommended_option")
        else:
            decision["status"] = "PAUSED"

        self._audit(
            phase="DECISION_TIMEOUT",
            message="Decision timed out and timeout behavior applied",
            action={"action_id": decision.get("action_id")},
            context={
                "run_id": decision.get("run_id"),
                "correlation_id": decision.get("correlation_id"),
            },
            metadata={
                "decision_id": decision.get("decision_id"),
                "status": decision.get("status"),
                "timeout_behavior": behavior,
            },
        )
        return decision

    def build_decision_request(
        self,
        action: Any = None,
        context: Mapping[str, Any] | None = None,
        policy_result: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        action_value, context_value, policy_value = self._coerce_inputs(action, context, policy_result, kwargs)
        return self.handle_decision(action=action_value, context=context_value, policy_result=policy_value)

    def create_decision_request(
        self,
        action: Any = None,
        context: Mapping[str, Any] | None = None,
        policy_result: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self.build_decision_request(action=action, context=context, policy_result=policy_result, **kwargs)

    def request_decision(
        self,
        action: Any = None,
        context: Mapping[str, Any] | None = None,
        policy_result: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self.build_decision_request(action=action, context=context, policy_result=policy_result, **kwargs)

    def build(self, payload: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        merged = dict(payload or {})
        merged.update(kwargs)
        return self.build_decision_request(
            action=merged.get("action"),
            context=merged.get("context") or merged,
            policy_result=merged.get("policy_result"),
        )

    def build_decision(
        self,
        action: Any = None,
        context: Mapping[str, Any] | None = None,
        policy_result: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self.build_decision_request(action=action, context=context, policy_result=policy_result, **kwargs)

    def _build_decision_payload(
        self,
        *,
        action: Any,
        context: Mapping[str, Any],
        policy_result: Mapping[str, Any],
        timeout_seconds: int,
        timeout_behavior: str,
    ) -> dict[str, Any]:
        build_candidates = (
            "build_decision_request",
            "create_decision_request",
            "build_decision",
            "build",
        )

        for method_name in build_candidates:
            method = getattr(self.builder, method_name, None)
            if not callable(method):
                continue
            for args, kwargs in (
                (
                    (action, context, policy_result),
                    {
                        "timeout_seconds": timeout_seconds,
                        "timeout_behavior": timeout_behavior,
                    },
                ),
                (
                    (),
                    {
                        "action": action,
                        "context": context,
                        "policy_result": policy_result,
                        "timeout_seconds": timeout_seconds,
                        "timeout_behavior": timeout_behavior,
                    },
                ),
            ):
                try:
                    result = method(*args, **kwargs)
                    if isinstance(result, Mapping):
                        payload = dict(result)
                        payload.setdefault("timeout_behavior", timeout_behavior)
                        return payload
                except TypeError:
                    continue

        raise RuntimeError("decision_builder does not expose a compatible build method")

    def _persist_pending_decision(self, *, payload: Mapping[str, Any], context: Mapping[str, Any]) -> None:
        store = context.get("pending_decision_store") or context.get("decision_store")
        if callable(store):
            try:
                store(dict(payload))
            except Exception:
                pass
            return

        if store is None:
            return

        for method_name in ("save", "create", "upsert", "store", "add"):
            method = getattr(store, method_name, None)
            if not callable(method):
                continue
            for args, kwargs in (((payload,), {}), ((), {"decision": payload}), ((), dict(payload))):
                try:
                    method(*args, **kwargs)
                    return
                except TypeError:
                    continue
                except Exception:
                    return

    def _coerce_inputs(
        self,
        action: Any,
        context: Mapping[str, Any] | None,
        policy_result: Mapping[str, Any] | None,
        kwargs: Mapping[str, Any],
    ) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        if isinstance(action, Mapping) and context is None and policy_result is None and "action" in action:
            action_value = action.get("action")
            context_value = dict(action.get("context") or {})
            context_value.update({k: v for k, v in action.items() if k not in {"action", "context", "policy_result"}})
            policy_value = dict(action.get("policy_result") or {})
            return action_value, context_value, policy_value

        context_value = dict(context or {})
        policy_value = dict(policy_result or {})

        if kwargs:
            if isinstance(kwargs.get("context"), Mapping):
                context_value.update(dict(kwargs["context"]))
            if isinstance(kwargs.get("policy_result"), Mapping):
                policy_value.update(dict(kwargs["policy_result"]))
            for key in ("run_id", "ticket_id", "correlation_id", "timeout_seconds", "timeout_behavior"):
                if key in kwargs and kwargs[key] is not None:
                    context_value.setdefault(key, kwargs[key])

        action_value = action if action is not None else kwargs.get("action")
        return action_value, context_value, policy_value

    def _resolve_timeout_seconds(self, context: Mapping[str, Any]) -> int:
        candidate = (
            context.get("decision_timeout_seconds")
            or context.get("timeout_seconds")
            or context.get("timeout")
            or self.default_timeout_seconds
        )
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            value = self.default_timeout_seconds
        return value

    def _resolve_timeout_behavior(self, context: Mapping[str, Any]) -> str:
        candidate = context.get("decision_timeout_behavior") or context.get("timeout_behavior") or self.default_timeout_behavior
        return self._normalize_timeout_behavior(str(candidate))

    @staticmethod
    def _normalize_timeout_behavior(value: str) -> str:
        normalized = str(value or "").upper()
        if normalized in {TimeoutBehavior.DERIVED.value, TimeoutBehavior.AUTO.value, TimeoutBehavior.PAUSE.value}:
            return normalized
        return TimeoutBehavior.PAUSE.value

    def _audit(
        self,
        *,
        phase: str,
        message: str,
        action: Any,
        context: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> None:
        action_id = None
        if isinstance(action, Mapping):
            action_id = action.get("action_id") or action.get("id")
        elif hasattr(action, "action_id"):
            action_id = getattr(action, "action_id")
        if action_id is None:
            action_id = context.get("action_id")

        event = {
            "phase": phase,
            "timestamp": self._ts(self._now()),
            "message": message,
            "run_id": context.get("run_id") or "unknown-run",
            "action_id": str(action_id) if action_id is not None else None,
            "correlation_id": context.get("correlation_id"),
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

    @staticmethod
    def _now_with_offset(seconds: int) -> datetime:
        return datetime.now(timezone.utc) + timedelta(seconds=seconds)

    @staticmethod
    def _ts(value: datetime) -> str:
        return value.isoformat()


_DEFAULT_ENGINE = DecisionEngine()


def handle_decision(
    action: Any,
    context: Mapping[str, Any] | None,
    policy_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return _DEFAULT_ENGINE.handle_decision(action=action, context=context, policy_result=policy_result)


def build_decision_request(
    action: Any = None,
    context: Mapping[str, Any] | None = None,
    policy_result: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return _DEFAULT_ENGINE.build_decision_request(action=action, context=context, policy_result=policy_result, **kwargs)


def create_decision_request(
    action: Any = None,
    context: Mapping[str, Any] | None = None,
    policy_result: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return _DEFAULT_ENGINE.create_decision_request(action=action, context=context, policy_result=policy_result, **kwargs)


def request_decision(
    action: Any = None,
    context: Mapping[str, Any] | None = None,
    policy_result: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return _DEFAULT_ENGINE.request_decision(action=action, context=context, policy_result=policy_result, **kwargs)


def build(payload: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT_ENGINE.build(payload=payload, **kwargs)


def build_decision(
    action: Any = None,
    context: Mapping[str, Any] | None = None,
    policy_result: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return _DEFAULT_ENGINE.build_decision(action=action, context=context, policy_result=policy_result, **kwargs)
