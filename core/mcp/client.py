from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from core.mcp import capability_mapper
from core.mcp import registry
from core.mcp import session_manager
from core.observability import audit_logger


class MCPErrorType(str, Enum):
    INVALID_TOOL = "INVALID_TOOL"
    INVALID_PARAMETERS = "INVALID_PARAMETERS"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    CONNECTION_FAILURE = "CONNECTION_FAILURE"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"


class MCPClient:
    """Controlled MCP client wrapper with registry and capability checks."""

    def __init__(
        self,
        *,
        registry_module: Any = registry,
        session_manager_module: Any = session_manager,
        capability_mapper_module: Any = capability_mapper,
        audit_logger_module: Any = audit_logger,
        default_timeout_seconds: int = 30,
    ) -> None:
        self.registry = registry_module
        self.session_manager = session_manager_module
        self.capability_mapper = capability_mapper_module
        self.audit_logger = audit_logger_module
        self.default_timeout_seconds = max(1, int(default_timeout_seconds))
        self._fallback_logs: list[dict[str, Any]] = []

    def call_tool(
        self,
        tool_name: str,
        parameters: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
        *,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        started = self._now()
        ctx = dict(context or {})
        try:
            params = dict(parameters or {})
        except Exception as exc:
            run_id = str(ctx.get("run_id") or "unknown-run")
            result = self._finalize(
                started=started,
                run_id=run_id,
                tool_name=tool_name,
                status="FAILED",
                success=False,
                error_type=MCPErrorType.INVALID_PARAMETERS.value,
                error=f"Invalid MCP parameters: {exc}",
                result=None,
            )
            self._audit(
                phase="ERROR",
                message="MCP call blocked due to invalid parameters",
                run_id=run_id,
                metadata={"tool_name": tool_name, "result": result},
            )
            return result

        timeout = max(1, int(timeout_seconds or ctx.get("timeout_seconds") or self.default_timeout_seconds))
        run_id = str(ctx.get("run_id") or "unknown-run")

        self._audit(
            phase="EXECUTION_TRACE",
            message="MCP call started",
            run_id=run_id,
            metadata={"tool_name": tool_name, "parameters": params, "timeout_seconds": timeout},
        )

        validation = self._validate_tool_and_capability(tool_name=tool_name, context=ctx)
        if validation["status"] != "ALLOWED":
            result = self._finalize(
                started=started,
                run_id=run_id,
                tool_name=tool_name,
                status="FAILED",
                success=False,
                error_type=validation["error_type"],
                error=str(validation["error"]),
                result=None,
            )
            self._audit(
                phase="ERROR",
                message="MCP call blocked before execution",
                run_id=run_id,
                metadata={"tool_name": tool_name, "validation": validation, "result": result},
            )
            return result

        adapter = validation["adapter"]
        tool_name = validation["tool_name"]

        session = self._get_or_create_session(tool_name=tool_name, context=ctx)
        call_context = dict(ctx)
        call_context["mcp_session"] = session

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self._invoke_adapter, adapter, tool_name, params, call_context, timeout)
                raw_result = future.result(timeout=timeout)

            structured_result = self._normalize_result(raw_result)
            result = self._finalize(
                started=started,
                run_id=run_id,
                tool_name=tool_name,
                status="COMPLETED" if structured_result["success"] else "FAILED",
                success=structured_result["success"],
                error_type=structured_result.get("error_type"),
                error=structured_result.get("error"),
                result=structured_result.get("result"),
            )
            result["response"] = structured_result

            self._audit(
                phase="EXECUTION_TRACE",
                message="MCP call completed",
                run_id=run_id,
                metadata={"tool_name": tool_name, "parameters": params, "result": result},
            )
            return result

        except FutureTimeoutError:
            result = self._finalize(
                started=started,
                run_id=run_id,
                tool_name=tool_name,
                status="TIMEOUT",
                success=False,
                error_type=MCPErrorType.TIMEOUT.value,
                error=f"MCP call timed out after {timeout}s",
                result=None,
            )
            self._audit(
                phase="ERROR",
                message="MCP call timeout",
                run_id=run_id,
                metadata={"tool_name": tool_name, "parameters": params, "result": result},
            )
            return result

        except Exception as exc:
            result = self._finalize(
                started=started,
                run_id=run_id,
                tool_name=tool_name,
                status="FAILED",
                success=False,
                error_type=self._classify_error(exc),
                error=str(exc),
                result=None,
            )
            self._audit(
                phase="ERROR",
                message="MCP call failed with exception",
                run_id=run_id,
                metadata={"tool_name": tool_name, "parameters": params, "result": result},
            )
            return result

    def _validate_tool_and_capability(self, *, tool_name: str, context: Mapping[str, Any]) -> dict[str, Any]:
        normalized_tool = str(tool_name or "").strip()
        if not normalized_tool:
            return {
                "status": "FAILED",
                "error_type": MCPErrorType.INVALID_TOOL.value,
                "error": "tool_name is required",
            }

        try:
            adapter = self.registry.get_adapter(normalized_tool)
        except KeyError as exc:
            return {
                "status": "FAILED",
                "error_type": MCPErrorType.INVALID_TOOL.value,
                "error": str(exc),
            }
        except PermissionError as exc:
            return {
                "status": "FAILED",
                "error_type": MCPErrorType.PERMISSION_DENIED.value,
                "error": str(exc),
            }

        capability_level = self._resolve_capability_level(context)
        validation = self.capability_mapper.validate_access(normalized_tool, capability_level)
        if not bool(validation.get("allowed")):
            return {
                "status": "FAILED",
                "error_type": MCPErrorType.PERMISSION_DENIED.value,
                "error": validation.get("reason") or "Capability not sufficient for MCP tool",
            }

        return {
            "status": "ALLOWED",
            "tool_name": normalized_tool,
            "adapter": adapter,
            "capability_validation": validation,
        }

    def _get_or_create_session(self, *, tool_name: str, context: Mapping[str, Any]) -> dict[str, Any]:
        run_id = str(context.get("run_id") or "unknown-run")
        creds = self._extract_tool_credentials(tool_name, context)

        session = self.session_manager.get_or_create_session(
            run_id=run_id,
            tool_name=tool_name,
            credentials=creds,
            ttl_seconds=context.get("session_ttl_seconds"),
            metadata={
                "correlation_id": context.get("correlation_id"),
                "ticket_id": context.get("ticket_id"),
            },
        )

        session_payload = session.to_dict() if hasattr(session, "to_dict") else dict(session)
        session_payload.pop("credentials", None)
        return session_payload

    @staticmethod
    def _extract_tool_credentials(tool_name: str, context: Mapping[str, Any]) -> dict[str, Any] | None:
        tool_creds = context.get("mcp_credentials")
        if isinstance(tool_creds, Mapping):
            candidate = tool_creds.get(tool_name)
            if isinstance(candidate, Mapping):
                return dict(candidate)

        direct = context.get("credentials")
        if isinstance(direct, Mapping):
            return dict(direct)
        return None

    def _invoke_adapter(
        self,
        adapter: Any,
        tool_name: str,
        parameters: Mapping[str, Any],
        context: Mapping[str, Any],
        timeout_seconds: int,
    ) -> Any:
        method_names = ("call_tool", "invoke", "execute", "run", "request")
        for method_name in method_names:
            method = getattr(adapter, method_name, None)
            if not callable(method):
                continue
            for args, kwargs in (
                ((tool_name, parameters), {"context": context, "timeout_seconds": timeout_seconds}),
                ((), {"tool": tool_name, "parameters": parameters, "context": context, "timeout_seconds": timeout_seconds}),
                ((parameters,), {"context": context, "timeout_seconds": timeout_seconds}),
            ):
                try:
                    return method(*args, **kwargs)
                except TypeError:
                    continue

        if callable(adapter):
            for args, kwargs in (
                ((tool_name, parameters), {"context": context, "timeout_seconds": timeout_seconds}),
                ((parameters,), {"context": context, "timeout_seconds": timeout_seconds}),
            ):
                try:
                    return adapter(*args, **kwargs)
                except TypeError:
                    continue

        raise RuntimeError(f"MCP adapter for tool '{tool_name}' is not invokable")

    @staticmethod
    def _normalize_result(raw_result: Any) -> dict[str, Any]:
        if isinstance(raw_result, Mapping):
            payload = dict(raw_result)
            payload.setdefault("success", payload.get("status") not in {"FAILED", "ERROR", "TIMEOUT"})
            if not payload["success"] and payload.get("error") is None:
                payload["error"] = payload.get("message") or "MCP tool call failed"
            return {
                "success": bool(payload["success"]),
                "result": payload.get("result", payload),
                "error": payload.get("error"),
                "error_type": payload.get("error_type"),
            }

        return {
            "success": True,
            "result": raw_result,
            "error": None,
            "error_type": None,
        }

    @staticmethod
    def _resolve_capability_level(context: Mapping[str, Any]) -> str:
        return str(
            context.get("capability_level")
            or (context.get("client_context") or {}).get("capability_level")
            or "C1_RESTRINGIDO"
        ).upper()

    def _finalize(
        self,
        *,
        started: datetime,
        run_id: str,
        tool_name: str,
        status: str,
        success: bool,
        error_type: str | None,
        error: str | None,
        result: Any,
    ) -> dict[str, Any]:
        finished = self._now()
        return {
            "status": status,
            "success": success,
            "tool_name": tool_name,
            "run_id": run_id,
            "duration_seconds": (finished - started).total_seconds(),
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "result": result,
            "error_type": error_type,
            "error": error,
        }

    def _audit(self, *, phase: str, message: str, run_id: str, metadata: Mapping[str, Any]) -> None:
        event = {
            "phase": phase,
            "message": message,
            "timestamp": self._now().isoformat(),
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
    def _classify_error(error: Exception) -> str:
        text = str(error).lower()
        if "timeout" in text:
            return MCPErrorType.TIMEOUT.value
        if "permission" in text or "allowlist" in text or "denied" in text:
            return MCPErrorType.PERMISSION_DENIED.value
        if "connect" in text or "connection" in text or "network" in text:
            return MCPErrorType.CONNECTION_FAILURE.value
        return MCPErrorType.UNKNOWN.value

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)


_DEFAULT_CLIENT = MCPClient()


def call_tool(
    tool_name: str,
    parameters: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    *,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    return _DEFAULT_CLIENT.call_tool(
        tool_name=tool_name,
        parameters=parameters,
        context=context,
        timeout_seconds=timeout_seconds,
    )
