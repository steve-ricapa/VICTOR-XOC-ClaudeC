from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import shutil
from typing import Any, Mapping
from urllib import error as url_error
from urllib import request as url_request

from core.execution import command_executor
from core.mcp import client as mcp_client
from core.observability import audit_logger


class ExecutionErrorType(str, Enum):
    TOOL_ERROR = "TOOL_ERROR"
    TIMEOUT = "TIMEOUT"
    ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class ExecutionResult:
    status: str
    success: bool
    executor: str
    stdout: str
    stderr: str
    exit_code: int | None
    duration_seconds: float
    started_at: str
    finished_at: str
    result: Any = None
    error_type: str | None = None
    command: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "success": self.success,
            "executor": self.executor,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error_type": self.error_type,
            "command": self.command,
        }


class ExecutionService:
    """Routes policy-approved actions to controlled executors."""

    def __init__(
        self,
        *,
        command_executor_module: Any = command_executor,
        mcp_client_module: Any = mcp_client,
        audit_logger_module: Any = audit_logger,
        default_timeout_seconds: int = 30,
        max_http_body_chars: int = 40_000,
    ) -> None:
        self.command_executor = command_executor_module
        self.mcp_client = mcp_client_module
        self.audit_logger = audit_logger_module
        self.default_timeout_seconds = max(1, int(default_timeout_seconds))
        self.max_http_body_chars = max(1_024, int(max_http_body_chars))
        self._fallback_logs: list[dict[str, Any]] = []

    def execute(self, action: Any, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        started_at_dt = self._now()
        started_at = self._ts(started_at_dt)

        normalized_action = self._normalize_action(action)
        normalized_context = self._normalize_context(context)
        executor = self._resolve_executor_type(normalized_action)
        command_preview = self._command_preview(normalized_action)

        if not self._is_policy_allowed(normalized_action, normalized_context):
            finished_at = self._now()
            result = ExecutionResult(
                status="FAILED",
                success=False,
                executor=executor,
                stdout="",
                stderr="Execution denied: policy decision is not ALLOWED",
                exit_code=None,
                duration_seconds=(finished_at - started_at_dt).total_seconds(),
                started_at=started_at,
                finished_at=self._ts(finished_at),
                result=None,
                error_type=ExecutionErrorType.ENVIRONMENT_ERROR.value,
                command=command_preview,
            )
            self._audit(
                phase="EXECUTION_FAILED",
                message="Execution blocked due to missing ALLOWED policy decision",
                action=normalized_action,
                context=normalized_context,
                result=result.to_dict(),
            )
            return result.to_dict()

        self._audit(
            phase="EXECUTION_STARTED",
            message="Execution started",
            action=normalized_action,
            context=normalized_context,
            result={"executor": executor, "command": command_preview},
        )

        try:
            if executor == "shell":
                route_result = self._execute_shell(normalized_action, normalized_context)
            elif executor == "file":
                route_result = self._execute_file(normalized_action, normalized_context)
            elif executor == "http":
                route_result = self._execute_http(normalized_action, normalized_context)
            elif executor == "mcp":
                route_result = self._execute_mcp(normalized_action, normalized_context)
            else:
                raise ValueError(f"Unsupported executor type: {executor}")

            normalized_route = self._normalize_route_result(route_result)
            finished_at = self._now()
            response = ExecutionResult(
                status="COMPLETED" if normalized_route["success"] else normalized_route.get("status", "FAILED"),
                success=bool(normalized_route["success"]),
                executor=executor,
                stdout=str(normalized_route.get("stdout", "")),
                stderr=str(normalized_route.get("stderr", "")),
                exit_code=self._normalize_exit_code(normalized_route.get("exit_code")),
                duration_seconds=(finished_at - started_at_dt).total_seconds(),
                started_at=started_at,
                finished_at=self._ts(finished_at),
                result=normalized_route.get("result"),
                error_type=normalized_route.get("error_type"),
                command=command_preview,
            )
            payload = response.to_dict()

            self._audit(
                phase="EXECUTION_COMPLETED" if response.success else "EXECUTION_FAILED",
                message="Execution completed" if response.success else "Execution finished with failure",
                action=normalized_action,
                context=normalized_context,
                result=payload,
            )
            return payload

        except Exception as exc:
            finished_at = self._now()
            error_type = self._classify_error(exc)
            result = ExecutionResult(
                status="TIMEOUT" if error_type == ExecutionErrorType.TIMEOUT else "FAILED",
                success=False,
                executor=executor,
                stdout="",
                stderr=str(exc),
                exit_code=None,
                duration_seconds=(finished_at - started_at_dt).total_seconds(),
                started_at=started_at,
                finished_at=self._ts(finished_at),
                result=None,
                error_type=error_type.value,
                command=command_preview,
            )
            payload = result.to_dict()
            self._audit(
                phase="EXECUTION_FAILED",
                message="Execution raised an exception",
                action=normalized_action,
                context=normalized_context,
                result=payload,
            )
            return payload

    def _execute_shell(self, action: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        command = action.get("command") or action.get("cmd") or action.get("script")
        if command is None:
            raise ValueError("Shell action missing command")

        timeout = self._resolve_timeout(action, context)
        cwd = self._resolve_cwd(action, context)
        env = action.get("env") if isinstance(action.get("env"), Mapping) else None

        return self._invoke_command_executor(
            command=command,
            timeout_seconds=timeout,
            cwd=cwd,
            env=env,
        )

    def _execute_file(self, action: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        operation = str(action.get("operation") or action.get("op") or "read").lower()
        raw_path = action.get("path") or action.get("target")
        if not raw_path:
            raise ValueError("File action missing path")

        path = self._resolve_safe_path(str(raw_path), context)

        if operation == "read":
            content = path.read_text(encoding="utf-8", errors="replace")
            return {
                "status": "COMPLETED",
                "success": True,
                "stdout": self._truncate(content),
                "stderr": "",
                "exit_code": 0,
                "result": {"path": str(path), "operation": operation},
            }

        if operation in {"write", "overwrite"}:
            content = str(action.get("content") or "")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return {
                "status": "COMPLETED",
                "success": True,
                "stdout": f"Wrote {len(content)} bytes",
                "stderr": "",
                "exit_code": 0,
                "result": {"path": str(path), "operation": operation, "bytes": len(content)},
            }

        if operation == "append":
            content = str(action.get("content") or "")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(content)
            return {
                "status": "COMPLETED",
                "success": True,
                "stdout": f"Appended {len(content)} bytes",
                "stderr": "",
                "exit_code": 0,
                "result": {"path": str(path), "operation": operation, "bytes": len(content)},
            }

        if operation in {"delete", "remove"}:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            return {
                "status": "COMPLETED",
                "success": True,
                "stdout": "Deleted target",
                "stderr": "",
                "exit_code": 0,
                "result": {"path": str(path), "operation": operation},
            }

        if operation == "exists":
            exists = path.exists()
            return {
                "status": "COMPLETED",
                "success": True,
                "stdout": str(exists).lower(),
                "stderr": "",
                "exit_code": 0,
                "result": {"path": str(path), "operation": operation, "exists": exists},
            }

        if operation == "list":
            if not path.is_dir():
                raise ValueError("List operation requires a directory path")
            entries = sorted(child.name for child in path.iterdir())
            return {
                "status": "COMPLETED",
                "success": True,
                "stdout": "\n".join(entries),
                "stderr": "",
                "exit_code": 0,
                "result": {"path": str(path), "operation": operation, "entries": entries},
            }

        raise ValueError(f"Unsupported file operation: {operation}")

    def _execute_http(self, action: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        url = str(action.get("url") or action.get("uri") or action.get("endpoint") or "")
        if not url:
            raise ValueError("HTTP action missing url")

        method = str(action.get("method") or "GET").upper()
        headers_raw = action.get("headers") if isinstance(action.get("headers"), Mapping) else {}
        headers = {str(k): str(v) for k, v in headers_raw.items()}

        data: bytes | None = None
        if action.get("json") is not None:
            data = json.dumps(action["json"]).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif action.get("data") is not None:
            raw_data = action["data"]
            data = raw_data if isinstance(raw_data, bytes) else str(raw_data).encode("utf-8")

        timeout = self._resolve_timeout(action, context)
        request_obj = url_request.Request(url=url, method=method, headers=headers, data=data)

        try:
            with url_request.urlopen(request_obj, timeout=timeout) as response:
                status_code = int(response.getcode() or 0)
                body = response.read(self.max_http_body_chars)
                text = body.decode("utf-8", errors="replace")
                return {
                    "status": "COMPLETED" if status_code < 400 else "FAILED",
                    "success": status_code < 400,
                    "stdout": self._truncate(text),
                    "stderr": "",
                    "exit_code": status_code,
                    "result": {"status_code": status_code, "url": url, "method": method},
                }
        except url_error.HTTPError as exc:
            body = exc.read(self.max_http_body_chars).decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            return {
                "status": "FAILED",
                "success": False,
                "stdout": self._truncate(body),
                "stderr": f"HTTPError: {exc}",
                "exit_code": int(getattr(exc, "code", 0) or 0),
                "error_type": ExecutionErrorType.TOOL_ERROR.value,
                "result": {"status_code": int(getattr(exc, "code", 0) or 0), "url": url, "method": method},
            }

    def _execute_mcp(self, action: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        tool = action.get("tool") or action.get("tool_name") or action.get("name")
        if not tool:
            raise ValueError("MCP action missing tool name")

        arguments = action.get("arguments")
        if arguments is None:
            arguments = action.get("params") or action.get("input") or {}

        handler = context.get("mcp_handler")
        if callable(handler):
            result = self._call_mcp_handler(handler, tool, arguments, context)
        else:
            client = context.get("mcp_client") or self.mcp_client
            result = self._call_mcp_client(client, tool, arguments, context)

        return {
            "status": "COMPLETED",
            "success": True,
            "stdout": self._truncate(json.dumps(result, default=str)),
            "stderr": "",
            "exit_code": 0,
            "result": {"tool": str(tool), "response": result},
        }

    def _call_mcp_handler(
        self,
        handler: Any,
        tool: Any,
        arguments: Any,
        context: Mapping[str, Any],
    ) -> Any:
        for args, kwargs in (
            ((tool, arguments), {}),
            ((tool, arguments, context), {}),
            ((), {"tool": tool, "arguments": arguments, "context": context}),
        ):
            try:
                return handler(*args, **kwargs)
            except TypeError:
                continue
        raise RuntimeError("Unsupported MCP handler signature")

    def _call_mcp_client(
        self,
        client: Any,
        tool: Any,
        arguments: Any,
        context: Mapping[str, Any],
    ) -> Any:
        for method_name in ("call_tool", "invoke", "execute", "run_tool", "request"):
            method = getattr(client, method_name, None)
            if not callable(method):
                continue
            for args, kwargs in (
                ((tool, arguments, context), {}),
                ((tool, arguments), {}),
                ((tool, arguments), {"context": context}),
                ((), {"tool_name": tool, "parameters": arguments, "context": context}),
                ((), {"tool": tool, "arguments": arguments, "context": context}),
            ):
                try:
                    return method(*args, **kwargs)
                except TypeError:
                    continue
        raise RuntimeError("Unsupported MCP client interface")

    def _invoke_command_executor(
        self,
        *,
        command: Any,
        timeout_seconds: int,
        cwd: str | None,
        env: Mapping[str, str] | None,
    ) -> dict[str, Any]:
        module = self.command_executor

        execute_method = getattr(module, "execute", None)
        if callable(execute_method):
            for args, kwargs in (
                ((command,), {"timeout_seconds": timeout_seconds, "cwd": cwd, "env": env}),
                ((command,), {"timeout": timeout_seconds, "cwd": cwd, "env": env}),
                ((command, timeout_seconds), {"cwd": cwd, "env": env}),
            ):
                try:
                    return dict(execute_method(*args, **kwargs))
                except TypeError:
                    continue

        executor_cls = getattr(module, "CommandExecutor", None)
        if executor_cls is not None:
            instance = executor_cls(default_timeout_seconds=timeout_seconds)
            execute = getattr(instance, "execute", None)
            if callable(execute):
                return dict(execute(command=command, timeout_seconds=timeout_seconds, cwd=cwd, env=env))

        raise RuntimeError("command_executor does not expose a supported execute interface")

    @staticmethod
    def _normalize_action(action: Any) -> dict[str, Any]:
        if isinstance(action, Mapping):
            payload = dict(action)
        elif hasattr(action, "__dict__"):
            payload = {k: v for k, v in vars(action).items() if not k.startswith("_")}
        else:
            payload = {"type": "unknown", "raw": action}

        parameters = payload.get("parameters")
        if isinstance(parameters, Mapping):
            for key, value in parameters.items():
                payload.setdefault(str(key), value)

        if "type" not in payload and "action_type" in payload:
            payload["type"] = payload["action_type"]

        return payload

    @staticmethod
    def _normalize_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
        return dict(context or {})

    def _normalize_route_result(self, route_result: Any) -> dict[str, Any]:
        if isinstance(route_result, Mapping):
            payload = dict(route_result)
            payload.setdefault("success", payload.get("status") == "COMPLETED")
            payload.setdefault("stdout", "")
            payload.setdefault("stderr", "")
            payload.setdefault("exit_code", 0 if payload["success"] else None)
            if not payload["success"] and payload.get("error_type") is None:
                payload["error_type"] = ExecutionErrorType.TOOL_ERROR.value
            return payload

        return {
            "status": "COMPLETED",
            "success": True,
            "stdout": str(route_result),
            "stderr": "",
            "exit_code": 0,
            "result": route_result,
        }

    def _resolve_executor_type(self, action: Mapping[str, Any]) -> str:
        raw = str(action.get("type") or action.get("action_type") or action.get("kind") or "").lower()
        if raw in {"shell", "command", "cmd", "bash", "terminal"}:
            return "shell"
        if raw in {"file", "filesystem"}:
            return "file"
        if raw in {"http", "https", "web"}:
            return "http"
        if raw in {"mcp", "tool"}:
            return "mcp"

        if "command" in action or "cmd" in action:
            return "shell"
        if "path" in action:
            return "file"
        if "url" in action or "uri" in action:
            return "http"
        if "tool" in action or "tool_name" in action:
            return "mcp"
        return "unknown"

    @staticmethod
    def _extract_policy_decision(action: Mapping[str, Any], context: Mapping[str, Any]) -> str:
        policy_result = context.get("policy_result")
        if isinstance(policy_result, Mapping):
            candidate = policy_result.get("decision") or policy_result.get("status")
            if candidate is not None:
                return str(candidate).upper()

        context_candidate = context.get("policy_decision") or context.get("decision")
        if context_candidate is not None:
            return str(context_candidate).upper()

        action_candidate = action.get("policy_decision") or action.get("policy_status")
        if action_candidate is not None:
            return str(action_candidate).upper()

        return ""

    def _is_policy_allowed(self, action: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
        return self._extract_policy_decision(action, context) == "ALLOWED"

    def _resolve_timeout(self, action: Mapping[str, Any], context: Mapping[str, Any]) -> int:
        candidate = (
            action.get("timeout_seconds")
            or action.get("timeout")
            or context.get("timeout_seconds")
            or context.get("default_timeout_seconds")
            or self.default_timeout_seconds
        )
        try:
            timeout = int(candidate)
        except (TypeError, ValueError):
            timeout = self.default_timeout_seconds
        return max(1, timeout)

    @staticmethod
    def _resolve_cwd(action: Mapping[str, Any], context: Mapping[str, Any]) -> str | None:
        for key in ("cwd", "working_dir", "working_directory"):
            value = action.get(key)
            if value is not None:
                return str(value)
        for key in ("workspace", "workspace_root", "working_dir"):
            value = context.get(key)
            if value is not None:
                return str(value)
        return None

    def _resolve_safe_path(self, raw_path: str, context: Mapping[str, Any]) -> Path:
        workspace_root = context.get("workspace_root") or context.get("workspace")
        path = Path(raw_path).expanduser()
        if workspace_root:
            root = Path(str(workspace_root)).expanduser().resolve()
            if not path.is_absolute():
                path = root / path
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise PermissionError(f"Path escapes workspace root: {resolved}")
            return resolved
        return path.resolve()

    def _audit(
        self,
        *,
        phase: str,
        message: str,
        action: Mapping[str, Any],
        context: Mapping[str, Any],
        result: Any,
    ) -> None:
        event = {
            "phase": phase,
            "timestamp": self._ts(self._now()),
            "message": message,
            "action": {
                "id": action.get("action_id") or action.get("id"),
                "type": action.get("type") or action.get("action_type") or action.get("kind"),
                "command": self._command_preview(action),
            },
            "run_id": context.get("run_id"),
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

    def _classify_error(self, error: Exception) -> ExecutionErrorType:
        text = str(error).lower()
        if isinstance(error, TimeoutError) or "timed out" in text or "timeout" in text:
            return ExecutionErrorType.TIMEOUT
        if isinstance(error, OSError) or "no such file" in text or "not found" in text:
            return ExecutionErrorType.ENVIRONMENT_ERROR
        if "tool" in text or "command" in text or "executor" in text:
            return ExecutionErrorType.TOOL_ERROR
        return ExecutionErrorType.UNKNOWN

    @staticmethod
    def _normalize_exit_code(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _truncate(self, value: str) -> str:
        if len(value) <= self.max_http_body_chars:
            return value
        return value[: self.max_http_body_chars] + "\n...[truncated]"

    @staticmethod
    def _command_preview(action: Mapping[str, Any]) -> str | None:
        command = action.get("command") or action.get("cmd") or action.get("script")
        if command is None:
            return None
        if isinstance(command, list):
            return " ".join(str(part) for part in command)
        return str(command)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _ts(value: datetime) -> str:
        return value.isoformat()


_DEFAULT_EXECUTION_SERVICE = ExecutionService()


def execute(action: Any, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return _DEFAULT_EXECUTION_SERVICE.execute(action=action, context=context)
