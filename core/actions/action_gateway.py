from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from core.decisions import decision_engine
from core.execution import execution_service
from core.observability import audit_logger
from core.policy import policy_engine

try:
    from core.contracts import action as action_contract
except ImportError:
    from core.contracts import action_models as action_contract

try:
    from core.contracts import policy as policy_contract
except ImportError:
    from core.contracts import policy_models as policy_contract

try:
    from core.contracts import event as event_contract
except ImportError:
    from core.contracts import event_models as event_contract


class GatewayStatus(str, Enum):
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"
    WAITING_DECISION = "WAITING_DECISION"
    FAILED = "FAILED"


class PolicyDecisionStatus(str, Enum):
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"
    REQUIRES_DECISION = "REQUIRES_DECISION"


class ErrorType(str, Enum):
    POLICY_BLOCKED = "POLICY_BLOCKED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    TOOL_ERROR = "TOOL_ERROR"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class ActionResult:
    status: GatewayStatus
    action_id: str
    run_id: str
    timestamp: str
    execution_result: Any = None
    decision_payload: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None
    policy_result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "action_id": self.action_id,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "execution_result": self.execution_result,
            "decision_payload": self.decision_payload,
            "failure": self.failure,
            "policy_result": self.policy_result,
        }


class ActionGateway:
    """Single enforced boundary for all runtime action execution."""

    def __init__(
        self,
        *,
        policy_engine_module: Any = policy_engine,
        execution_service_module: Any = execution_service,
        audit_logger_module: Any = audit_logger,
        decision_engine_module: Any = decision_engine,
    ) -> None:
        self.policy_engine = policy_engine_module
        self.execution_service = execution_service_module
        self.audit_logger = audit_logger_module
        self.decision_engine = decision_engine_module
        self._contract_modules = (action_contract, policy_contract, event_contract)
        self._fallback_logs: list[dict[str, Any]] = []

    def execute_action(self, action: Any, context: Mapping[str, Any] | None) -> ActionResult:
        ctx = self._normalize_context(context)
        action_id = self._extract_action_id(action)
        run_id = self._extract_run_id(ctx)
        timestamp = self._ts(self._now())

        self._log_event(
            phase="POLICY_CHECK",
            message="Validating action against policy",
            action_id=action_id,
            run_id=run_id,
            context=ctx,
            result={"contracts_loaded": len(self._contract_modules)},
        )

        try:
            raw_policy_result = self._validate_policy(action=action, context=ctx)
            policy_result = self._normalize_policy_result(raw_policy_result)
        except Exception as exc:
            error_type = self._classify_error(exc)
            failure = self._failure_payload(
                error_type=error_type,
                message=f"Policy validation failed: {exc}",
                details={"stage": "policy.validate"},
            )
            self._log_event(
                phase="EXECUTION_FAILED",
                message="Policy validation raised exception",
                action_id=action_id,
                run_id=run_id,
                context=ctx,
                result=failure,
            )
            return ActionResult(
                status=GatewayStatus.FAILED,
                action_id=action_id,
                run_id=run_id,
                timestamp=timestamp,
                failure=failure,
            )

        decision_status = policy_result["status"]

        if decision_status == PolicyDecisionStatus.BLOCKED.value:
            failure = self._failure_payload(
                error_type=ErrorType.POLICY_BLOCKED,
                message=str(policy_result.get("reason") or "Action blocked by policy"),
                details={"policy_result": policy_result},
            )
            self._log_event(
                phase="BLOCKED",
                message="Action blocked by policy decision",
                action_id=action_id,
                run_id=run_id,
                context=ctx,
                result=failure,
            )
            return ActionResult(
                status=GatewayStatus.BLOCKED,
                action_id=action_id,
                run_id=run_id,
                timestamp=timestamp,
                failure=failure,
                policy_result=policy_result,
            )

        if decision_status == PolicyDecisionStatus.REQUIRES_DECISION.value:
            decision_context = dict(ctx)
            decision_context.setdefault("action_id", action_id)
            decision_payload = self._build_decision_payload(
                action=action,
                context=decision_context,
                policy_result=policy_result,
            )
            self._log_event(
                phase="DECISION_REQUIRED",
                message="Action requires human decision before execution",
                action_id=action_id,
                run_id=run_id,
                context=ctx,
                result=decision_payload,
            )
            return ActionResult(
                status=GatewayStatus.WAITING_DECISION,
                action_id=action_id,
                run_id=run_id,
                timestamp=timestamp,
                decision_payload=decision_payload,
                policy_result=policy_result,
            )

        if decision_status != PolicyDecisionStatus.ALLOWED.value:
            failure = self._failure_payload(
                error_type=ErrorType.UNKNOWN,
                message=f"Unsupported policy decision status: {decision_status}",
                details={"policy_result": policy_result},
            )
            self._log_event(
                phase="EXECUTION_FAILED",
                message="Invalid policy decision status",
                action_id=action_id,
                run_id=run_id,
                context=ctx,
                result=failure,
            )
            return ActionResult(
                status=GatewayStatus.FAILED,
                action_id=action_id,
                run_id=run_id,
                timestamp=timestamp,
                failure=failure,
                policy_result=policy_result,
            )

        self._log_event(
            phase="EXECUTION_STARTED",
            message="Policy allowed action; execution starting",
            action_id=action_id,
            run_id=run_id,
            context=ctx,
            result={"policy_result": policy_result},
        )

        execution_context = dict(ctx)
        execution_context["policy_result"] = dict(policy_result)
        execution_context["policy_decision"] = PolicyDecisionStatus.ALLOWED.value

        try:
            raw_execution_result = self._execute_allowed_action(action=action, context=execution_context)
            execution_result = self._normalize_execution_result(raw_execution_result)
        except Exception as exc:
            error_type = self._classify_error(exc)
            failure = self._failure_payload(
                error_type=error_type,
                message=f"Execution failed: {exc}",
                details={"stage": "execution.execute"},
            )
            self._log_event(
                phase="EXECUTION_FAILED",
                message="Execution service raised exception",
                action_id=action_id,
                run_id=run_id,
                context=ctx,
                result=failure,
            )
            return ActionResult(
                status=GatewayStatus.FAILED,
                action_id=action_id,
                run_id=run_id,
                timestamp=timestamp,
                failure=failure,
                policy_result=policy_result,
            )

        execution_failure = self._execution_failure_from_result(execution_result)
        if execution_failure is not None:
            self._log_event(
                phase="EXECUTION_FAILED",
                message="Execution service returned failure result",
                action_id=action_id,
                run_id=run_id,
                context=ctx,
                result=execution_failure,
            )
            return ActionResult(
                status=GatewayStatus.FAILED,
                action_id=action_id,
                run_id=run_id,
                timestamp=timestamp,
                failure=execution_failure,
                execution_result=execution_result,
                policy_result=policy_result,
            )

        self._log_event(
            phase="EXECUTION_COMPLETED",
            message="Action executed successfully",
            action_id=action_id,
            run_id=run_id,
            context=ctx,
            result=execution_result,
        )
        return ActionResult(
            status=GatewayStatus.ALLOWED,
            action_id=action_id,
            run_id=run_id,
            timestamp=timestamp,
            execution_result=execution_result,
            policy_result=policy_result,
        )

    def handle_action(self, action: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        parsed_action, parsed_context, overrides = self._parse_inputs(action, context, kwargs)
        gateway: ActionGateway = self
        if overrides:
            gateway = ActionGateway(
                policy_engine_module=overrides.get("policy_engine", self.policy_engine),
                execution_service_module=overrides.get("execution_service", self.execution_service),
                audit_logger_module=overrides.get("audit_logger", self.audit_logger),
                decision_engine_module=overrides.get("decision_engine", self.decision_engine),
            )

        result = gateway.execute_action(parsed_action, parsed_context)
        payload = result.to_dict()
        payload["execution_result"] = payload.pop("execution_result")
        payload["decision_payload"] = payload.pop("decision_payload")
        payload["pending_decision"] = payload["decision_payload"]
        if result.status == GatewayStatus.BLOCKED:
            payload["blocked"] = True
            payload["message"] = self._failure_message(result.failure)
            payload["error"] = payload["message"]
            payload["error_type"] = ErrorType.POLICY_BLOCKED.value
        elif result.status == GatewayStatus.WAITING_DECISION:
            payload["message"] = "Decision required"
        elif result.status == GatewayStatus.FAILED:
            payload["error"] = self._failure_message(result.failure)
            payload["message"] = payload["error"]
            payload["error_type"] = str((result.failure or {}).get("error_type", ErrorType.UNKNOWN.value))
        else:
            payload["result"] = result.execution_result
            payload["message"] = "Action executed"
        return payload

    def process_action(self, action: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.handle_action(action=action, context=context, **kwargs)

    def route_action(self, action: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.handle_action(action=action, context=context, **kwargs)

    def dispatch(self, action: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.handle_action(action=action, context=context, **kwargs)

    def execute(self, action: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.handle_action(action=action, context=context, **kwargs)

    def _parse_inputs(
        self,
        action: Any,
        context: Mapping[str, Any] | None,
        kwargs: Mapping[str, Any],
    ) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        parsed_context: dict[str, Any] = dict(context or {})
        parsed_action = action
        overrides: dict[str, Any] = {}

        if isinstance(action, Mapping) and "action" in action and context is None:
            parsed_action = action.get("action")
            if isinstance(action.get("context"), Mapping):
                parsed_context.update(dict(action["context"]))
            if isinstance(action.get("run_context"), Mapping):
                parsed_context.update(dict(action["run_context"]))
            if isinstance(action.get("ticket"), Mapping):
                parsed_context.setdefault("ticket", dict(action["ticket"]))
            for key in ("policy_engine", "execution_service", "audit_logger", "decision_engine"):
                if key in action and action[key] is not None:
                    overrides[key] = action[key]

        if isinstance(kwargs.get("run_context"), Mapping):
            parsed_context.update(dict(kwargs["run_context"]))
        if isinstance(kwargs.get("context"), Mapping):
            parsed_context.update(dict(kwargs["context"]))
        if isinstance(kwargs.get("ticket"), Mapping):
            parsed_context.setdefault("ticket", dict(kwargs["ticket"]))

        for key in ("policy_engine", "execution_service", "audit_logger", "decision_engine"):
            if kwargs.get(key) is not None:
                overrides[key] = kwargs[key]

        if parsed_action is None:
            parsed_action = kwargs.get("action")

        return parsed_action, parsed_context, overrides

    def _validate_policy(self, *, action: Any, context: Mapping[str, Any]) -> Any:
        validate_fn = getattr(self.policy_engine, "validate", None)
        if not callable(validate_fn):
            raise RuntimeError("policy_engine.validate is required")

        try:
            return validate_fn(action, context)
        except TypeError:
            try:
                return validate_fn(action=action, context=context)
            except TypeError:
                return validate_fn({"action": action, "context": dict(context)})

    def _normalize_policy_result(self, policy_result: Any) -> dict[str, Any]:
        if isinstance(policy_result, Mapping):
            raw_status = str(
                policy_result.get("status")
                or policy_result.get("decision")
                or policy_result.get("result")
                or ""
            ).upper()
            reason = policy_result.get("reason") or policy_result.get("message")
            details = dict(policy_result)
        else:
            raw_status = str(getattr(policy_result, "status", policy_result)).upper()
            reason = getattr(policy_result, "reason", None)
            details = {"value": policy_result}

        status_map = {
            "ALLOWED": PolicyDecisionStatus.ALLOWED.value,
            "ALLOW": PolicyDecisionStatus.ALLOWED.value,
            "PERMITTED": PolicyDecisionStatus.ALLOWED.value,
            "PERMIT": PolicyDecisionStatus.ALLOWED.value,
            "BLOCKED": PolicyDecisionStatus.BLOCKED.value,
            "BLOCK": PolicyDecisionStatus.BLOCKED.value,
            "DENIED": PolicyDecisionStatus.BLOCKED.value,
            "DENY": PolicyDecisionStatus.BLOCKED.value,
            "REQUIRES_DECISION": PolicyDecisionStatus.REQUIRES_DECISION.value,
            "WAITING_DECISION": PolicyDecisionStatus.REQUIRES_DECISION.value,
            "DECISION_REQUIRED": PolicyDecisionStatus.REQUIRES_DECISION.value,
            "PENDING_DECISION": PolicyDecisionStatus.REQUIRES_DECISION.value,
        }
        normalized_status = status_map.get(raw_status)

        if normalized_status is None and isinstance(policy_result, Mapping):
            if bool(policy_result.get("blocked")):
                normalized_status = PolicyDecisionStatus.BLOCKED.value
            elif bool(policy_result.get("requires_decision")):
                normalized_status = PolicyDecisionStatus.REQUIRES_DECISION.value
            else:
                normalized_status = PolicyDecisionStatus.ALLOWED.value
        elif normalized_status is None:
            normalized_status = PolicyDecisionStatus.ALLOWED.value

        return {
            "status": normalized_status,
            "reason": reason,
            "details": details,
        }

    def _build_decision_payload(
        self,
        *,
        action: Any,
        context: Mapping[str, Any],
        policy_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "decision_id": str(uuid4()),
            "action": action,
            "context": dict(context),
            "policy_result": dict(policy_result),
            "requested_at": self._ts(self._now()),
        }

        build_candidates = (
            "handle_decision",
            "build_decision",
            "build_decision_request",
            "create_decision_request",
            "request_decision",
            "build",
        )
        for method_name in build_candidates:
            method = getattr(self.decision_engine, method_name, None)
            if not callable(method):
                continue
            for args, kwargs in (
                ((action, context, policy_result), {}),
                ((), payload),
                ((payload,), {}),
            ):
                try:
                    result = method(*args, **kwargs)
                    if isinstance(result, Mapping):
                        merged = dict(payload)
                        merged.update(dict(result))
                        return merged
                    return {"decision_payload": result, **payload}
                except TypeError:
                    continue

        return payload

    def _execute_allowed_action(self, *, action: Any, context: Mapping[str, Any]) -> Any:
        execute_fn = getattr(self.execution_service, "execute", None)
        if not callable(execute_fn):
            raise RuntimeError("execution_service.execute is required")

        for args, kwargs in (
            ((action, context), {}),
            ((), {"action": action, "context": context}),
            ((action,), {}),
        ):
            try:
                return execute_fn(*args, **kwargs)
            except TypeError:
                continue
        raise RuntimeError("execution_service.execute has no supported signature")

    def _normalize_execution_result(self, execution_result: Any) -> Any:
        if isinstance(execution_result, Mapping):
            return dict(execution_result)
        return execution_result

    def _execution_failure_from_result(self, execution_result: Any) -> dict[str, Any] | None:
        if not isinstance(execution_result, Mapping):
            return None

        raw_status = str(execution_result.get("status") or execution_result.get("result") or "").upper()
        if raw_status in {"FAILED", "ERROR", "EXECUTION_FAILURE", "TIMEOUT"}:
            error_text = str(execution_result.get("error") or execution_result.get("message") or "Execution failed")
            error_type = self._classify_error(error_text)
            return self._failure_payload(
                error_type=error_type,
                message=error_text,
                details=dict(execution_result),
            )

        if execution_result.get("success") is False:
            error_text = str(execution_result.get("error") or execution_result.get("message") or "Execution failed")
            error_type = self._classify_error(error_text)
            return self._failure_payload(
                error_type=error_type,
                message=error_text,
                details=dict(execution_result),
            )

        return None

    def _failure_payload(
        self,
        *,
        error_type: ErrorType,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "error_type": error_type.value,
            "message": message,
            "timestamp": self._ts(self._now()),
            "details": dict(details) if isinstance(details, Mapping) else details,
        }

    def _log_event(
        self,
        *,
        phase: str,
        message: str,
        action_id: str,
        run_id: str,
        context: Mapping[str, Any],
        result: Any = None,
    ) -> None:
        event = {
            "phase": phase,
            "timestamp": self._ts(self._now()),
            "message": message,
            "action_id": action_id,
            "run_id": run_id,
            "correlation_id": context.get("correlation_id"),
            "result": result,
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

    def _normalize_context(self, context: Mapping[str, Any] | None) -> dict[str, Any]:
        return dict(context or {})

    def _extract_action_id(self, action: Any) -> str:
        if isinstance(action, Mapping):
            action_id = action.get("action_id") or action.get("id") or action.get("uuid")
            if action_id is not None:
                return str(action_id)
        for attr_name in ("action_id", "id", "uuid"):
            if hasattr(action, attr_name):
                value = getattr(action, attr_name)
                if value is not None:
                    return str(value)
        return f"action-{uuid4()}"

    def _extract_run_id(self, context: Mapping[str, Any]) -> str:
        for key in ("run_id", "execution_id", "trace_run_id"):
            if key in context and context[key] is not None:
                return str(context[key])
        return "unknown-run"

    def _classify_error(self, error: Any) -> ErrorType:
        if isinstance(error, TimeoutError):
            return ErrorType.TIMEOUT
        if isinstance(error, PermissionError):
            return ErrorType.PERMISSION_DENIED

        text = str(error).lower()
        if "timeout" in text:
            return ErrorType.TIMEOUT
        if "permission" in text or "denied" in text or "forbidden" in text:
            return ErrorType.PERMISSION_DENIED
        if "tool" in text or "executor" in text or "command" in text or "process" in text:
            return ErrorType.TOOL_ERROR
        if "policy" in text and ("block" in text or "deny" in text):
            return ErrorType.POLICY_BLOCKED
        return ErrorType.UNKNOWN

    @staticmethod
    def _failure_message(failure: Mapping[str, Any] | None) -> str:
        if not failure:
            return "Unknown failure"
        message = failure.get("message")
        return str(message) if message else "Unknown failure"

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _ts(value: datetime) -> str:
        return value.isoformat()


_DEFAULT_GATEWAY = ActionGateway()


def execute_action(action: Any, context: Mapping[str, Any] | None = None) -> ActionResult:
    return _DEFAULT_GATEWAY.execute_action(action=action, context=context)


def handle_action(action: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT_GATEWAY.handle_action(action=action, context=context, **kwargs)


def process_action(action: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT_GATEWAY.process_action(action=action, context=context, **kwargs)


def route_action(action: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT_GATEWAY.route_action(action=action, context=context, **kwargs)


def dispatch(action: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT_GATEWAY.dispatch(action=action, context=context, **kwargs)


def execute(action: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT_GATEWAY.execute(action=action, context=context, **kwargs)
