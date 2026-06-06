from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import uuid4

from core.llm import claude_adapter

from core.actions import action_gateway
from core.decisions import decision_engine
from core.execution import execution_service
from core.observability import audit_logger
from core.policy import policy_engine
from core.state import checkpoint_store
from core.tickets import ticket_client
from core.tickets import state_manager
from core.orchestrator import pause_resume_controller


PROJECT_ROOT = Path(__file__).resolve().parents[2]

try:
    from core.prompts.builders import prompt_builder
except ImportError:
    from core.prompts import builders as prompt_builder  # type: ignore[no-redef]


class Phase(str, Enum):
    PLAN = "PLAN"
    POLICY = "POLICY"
    EXEC = "EXEC"
    VERIFY = "VERIFY"
    ERROR = "ERROR"


class ExecutionStatus(str, Enum):
    RUNNING = "RUNNING"
    WAITING_DECISION = "WAITING_DECISION"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


class GatewayStatus(str, Enum):
    EXECUTED = "EXECUTED"
    BLOCKED = "BLOCKED"
    WAITING_DECISION = "WAITING_DECISION"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    COMPLETED = "COMPLETED"
    UNKNOWN = "UNKNOWN"


class ErrorType(str, Enum):
    TOOL_ERROR = "TOOL_ERROR"
    TIMEOUT = "TIMEOUT"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    MAX_ITERATIONS = "MAX_ITERATIONS"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class RunContext:
    run_id: str
    ticket_id: str
    started_at: datetime
    updated_at: datetime
    correlation_id: str
    execution_status: ExecutionStatus
    iteration: int = 0
    pending_decision: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


class VictorLoop:
    def __init__(
        self,
        *,
        max_iterations: int = 40,
        retry_limits: Mapping[str | ErrorType, int] | None = None,
        claude_adapter_module: Any = claude_adapter,
        action_gateway_module: Any = action_gateway,
        policy_engine_module: Any = policy_engine,
        execution_service_module: Any = execution_service,
        audit_logger_module: Any = audit_logger,
        state_manager_module: Any = state_manager,
        decision_engine_module: Any = decision_engine,
        prompt_builder_module: Any = prompt_builder,
        checkpoint_store_module: Any = checkpoint_store,
        pause_resume_controller_module: Any = pause_resume_controller,
        ticket_client_module: Any = ticket_client,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")

        self.max_iterations = max_iterations
        self.claude_adapter = claude_adapter_module
        self.action_gateway = action_gateway_module
        self.policy_engine = policy_engine_module
        self.execution_service = execution_service_module
        self.audit_logger = audit_logger_module
        self.state_manager = state_manager_module
        self.decision_engine = decision_engine_module
        self.prompt_builder = prompt_builder_module
        self.checkpoint_store = checkpoint_store_module
        self.pause_resume_controller = pause_resume_controller_module
        self.ticket_client = ticket_client_module

        self.retry_limits: dict[ErrorType, int] = {
            ErrorType.TIMEOUT: 2,
            ErrorType.TOOL_ERROR: 1,
            ErrorType.UNKNOWN: 0,
            ErrorType.VALIDATION_ERROR: 0,
            ErrorType.POLICY_BLOCKED: 0,
            ErrorType.MAX_ITERATIONS: 0,
        }
        if retry_limits:
            for key, value in retry_limits.items():
                normalized = self._error_type_from_value(key)
                self.retry_limits[normalized] = max(0, int(value))

        self._retry_counters: dict[ErrorType, int] = {}
        self._local_audit_events: list[dict[str, Any]] = []

    def run(self, ticket: Any) -> dict[str, Any]:
        self._retry_counters = {}
        self._local_audit_events = []

        resume_payload = self._extract_resume_payload(ticket)
        if resume_payload is not None:
            return self._resume_run(ticket=ticket, resume_payload=resume_payload)

        ctx = self._initialize_run_context(ticket)
        self._set_execution_status(
            ctx, ExecutionStatus.RUNNING, ticket=ticket, ticket_status="RUNNING"
        )
        self._audit(
            ctx,
            Phase.PLAN,
            "Run initialized",
            result={
                "run_id": ctx.run_id,
                "ticket_id": ctx.ticket_id,
                "correlation_id": ctx.correlation_id,
                "execution_status": ctx.execution_status.value,
            },
        )

        while ctx.iteration < self.max_iterations:
            ctx.iteration += 1
            ctx.updated_at = self._now()

            try:
                action = self._plan_action(ticket=ticket, ctx=ctx)
                gateway_raw = self._dispatch_action(ticket=ticket, ctx=ctx, action=action)
                gateway_result = self._normalize_gateway_result(gateway_raw)

                status = gateway_result["status"]

                if status == GatewayStatus.EXECUTED.value:
                    self._handle_executed(ctx=ctx, action=action, gateway_result=gateway_result)
                    if self._is_completion_signal(
                        action=action,
                        gateway_result=gateway_result,
                        ticket=ticket,
                        ctx=ctx,
                    ):
                        return self._complete_run(
                            ctx=ctx, ticket=ticket, gateway_result=gateway_result
                        )
                    continue

                if status == GatewayStatus.BLOCKED.value:
                    return self._handle_blocked(
                        ctx=ctx,
                        ticket=ticket,
                        action=action,
                        gateway_result=gateway_result,
                    )

                if status == GatewayStatus.WAITING_DECISION.value:
                    return self._handle_waiting_decision(
                        ctx=ctx,
                        ticket=ticket,
                        action=action,
                        gateway_result=gateway_result,
                    )

                if status == GatewayStatus.EXECUTION_FAILURE.value:
                    should_retry, error_type, error_message = self._handle_execution_failure(
                        ctx=ctx,
                        action=action,
                        gateway_result=gateway_result,
                    )
                    if should_retry:
                        continue
                    return self._fail_run(
                        ctx=ctx,
                        ticket=ticket,
                        error_type=error_type,
                        message=error_message,
                        action=action,
                        details=gateway_result,
                    )

                if status == GatewayStatus.COMPLETED.value:
                    return self._complete_run(ctx=ctx, ticket=ticket, gateway_result=gateway_result)

                return self._fail_run(
                    ctx=ctx,
                    ticket=ticket,
                    error_type=ErrorType.UNKNOWN,
                    message=f"Unknown gateway status: {status}",
                    action=action,
                    details=gateway_result,
                )

            except TimeoutError as exc:
                should_retry, error_type, error_message = self._handle_execution_failure(
                    ctx=ctx,
                    action=None,
                    gateway_result={
                        "status": GatewayStatus.EXECUTION_FAILURE.value,
                        "error_type": ErrorType.TIMEOUT.value,
                        "error": str(exc),
                        "result": None,
                    },
                )
                if should_retry:
                    continue
                return self._fail_run(
                    ctx=ctx,
                    ticket=ticket,
                    error_type=error_type,
                    message=error_message,
                    action=None,
                    details={"exception": str(exc)},
                )

            except Exception as exc:
                classified = self._classify_error(error=exc)
                should_retry, error_type, error_message = self._handle_execution_failure(
                    ctx=ctx,
                    action=None,
                    gateway_result={
                        "status": GatewayStatus.EXECUTION_FAILURE.value,
                        "error_type": classified.value,
                        "error": str(exc),
                        "result": None,
                    },
                )
                if should_retry:
                    continue
                return self._fail_run(
                    ctx=ctx,
                    ticket=ticket,
                    error_type=error_type,
                    message=error_message,
                    action=None,
                    details={"exception": str(exc)},
                )

        return self._fail_run(
            ctx=ctx,
            ticket=ticket,
            error_type=ErrorType.MAX_ITERATIONS,
            message=f"Max iterations exceeded ({self.max_iterations})",
            action=None,
            details={"max_iterations": self.max_iterations},
        )

    def _extract_resume_payload(self, ticket: Any) -> dict[str, Any] | None:
        if not isinstance(ticket, Mapping):
            return None
        resume = ticket.get("_resume")
        return dict(resume) if isinstance(resume, Mapping) else None

    def _resume_run(self, *, ticket: Any, resume_payload: Mapping[str, Any]) -> dict[str, Any]:
        checkpoint = resume_payload.get("checkpoint")
        decision = resume_payload.get("decision")
        if not isinstance(checkpoint, Mapping) or not isinstance(decision, Mapping):
            raise ValueError("Resume payload requires checkpoint and decision objects")

        ctx = self._run_context_from_checkpoint(checkpoint)
        selected_option = str(((decision.get("response") or {}).get("selected_option") or "")).upper()
        comment = (decision.get("response") or {}).get("comment")

        if selected_option == "A":
            action = self._resume_action_from_decision(decision)
            gateway_raw = self._dispatch_action(
                ticket=ticket,
                ctx=ctx,
                action=action,
                extra_context={"preapproved_actions": [self._extract_action_id(action)]},
            )
            gateway_result = self._normalize_gateway_result(gateway_raw)
            status = gateway_result["status"]
            if status == GatewayStatus.EXECUTED.value:
                self._handle_executed(ctx=ctx, action=action, gateway_result=gateway_result)
                return self._complete_run(ctx=ctx, ticket=ticket, gateway_result=gateway_result)
            if status == GatewayStatus.COMPLETED.value:
                return self._complete_run(ctx=ctx, ticket=ticket, gateway_result=gateway_result)
            if status == GatewayStatus.BLOCKED.value:
                return self._handle_blocked(ctx=ctx, ticket=ticket, action=action, gateway_result=gateway_result)
            if status == GatewayStatus.WAITING_DECISION.value:
                return self._handle_waiting_decision(ctx=ctx, ticket=ticket, action=action, gateway_result=gateway_result)
            resumed_error_type = self._classify_error(
                declared_error_type=gateway_result.get("error_type"),
                error=gateway_result.get("raw") if isinstance(gateway_result.get("raw"), Mapping) else gateway_result.get("error"),
            )
            resumed_error_message = str(
                gateway_result.get("message")
                or gateway_result.get("error")
                or "Approved action failed during resume"
            )
            return self._fail_run(
                ctx=ctx,
                ticket=ticket,
                error_type=resumed_error_type,
                message=resumed_error_message,
                action=action,
                details=gateway_result,
            )

        if selected_option == "B":
            self._set_execution_status(ctx, ExecutionStatus.BLOCKED, ticket=ticket, ticket_status="DENIED")
            details = {"decision": dict(decision), "comment": comment}
            failure_response = self._build_failure_response(
                ctx=ctx,
                error_type=ErrorType.POLICY_BLOCKED,
                message=str(comment or "Human denied the pending action"),
                action=self._resume_action_from_decision(decision),
                details=details,
            )
            return {
                "status": "FAILED",
                "execution_status": ctx.execution_status.value,
                "run_context": self._context_payload(ctx),
                "failure_response": failure_response,
            }

        if selected_option == "C":
            paused_decision = dict(decision)
            paused_decision["status"] = "PAUSED"
            self._set_execution_status(
                ctx,
                ExecutionStatus.WAITING_DECISION,
                ticket=ticket,
                ticket_status="WAITING_DECISION",
                pending_decision=paused_decision,
            )
            return {
                "status": "WAITING_DECISION",
                "execution_status": ctx.execution_status.value,
                "run_context": self._context_payload(ctx),
                "pending_decision": paused_decision,
            }

        raise ValueError(f"Unsupported decision option: {selected_option or 'missing'}")

    def _run_context_from_checkpoint(self, checkpoint: Mapping[str, Any]) -> RunContext:
        run_context = checkpoint.get("run_context")
        if not isinstance(run_context, Mapping):
            raise ValueError("Checkpoint is missing run_context")

        started_at = self._parse_datetime(run_context.get("started_at"))
        updated_at = self._parse_datetime(run_context.get("updated_at"))
        status = self._execution_status_from_value(run_context.get("execution_status"))
        history_raw = checkpoint.get("history")
        history = [dict(item) for item in history_raw if isinstance(item, Mapping)] if isinstance(history_raw, list) else []
        pending = checkpoint.get("pending_decision")

        return RunContext(
            run_id=str(run_context.get("run_id") or "unknown-run"),
            ticket_id=str(run_context.get("ticket_id") or "unknown-ticket"),
            started_at=started_at,
            updated_at=updated_at,
            correlation_id=str(run_context.get("correlation_id") or f"{run_context.get('ticket_id')}:{run_context.get('run_id')}"),
            execution_status=status,
            iteration=max(0, int(run_context.get("iteration") or 0)),
            pending_decision=dict(pending) if isinstance(pending, Mapping) else None,
            history=history,
        )

    def _resume_action_from_decision(self, decision: Mapping[str, Any]) -> Any:
        action_preview = decision.get("action_preview")
        if isinstance(action_preview, Mapping):
            return dict(action_preview)
        action = decision.get("action")
        if isinstance(action, Mapping):
            return dict(action)
        raise ValueError("Decision payload does not contain an actionable preview")

    def _initialize_run_context(self, ticket: Any) -> RunContext:
        ticket_id = self._extract_ticket_id(ticket)
        run_id = str(uuid4())
        now = self._now()
        correlation_id = str(
            self._get_value(ticket, "correlation_id", "trace_id", default=f"{ticket_id}:{run_id}")
        )
        return RunContext(
            run_id=run_id,
            ticket_id=ticket_id,
            started_at=now,
            updated_at=now,
            correlation_id=correlation_id,
            execution_status=ExecutionStatus.RUNNING,
        )

    def _plan_action(self, *, ticket: Any, ctx: RunContext) -> Any:
        self._audit(
            ctx,
            Phase.PLAN,
            "Building prompt",
            result={"iteration": ctx.iteration},
        )

        prompt = self._build_prompt(ticket=ticket, ctx=ctx)
        response = self._call_claude(prompt=prompt, ticket=ticket, ctx=ctx)
        action = self._parse_action(response=response, ctx=ctx)

        self._audit(
            ctx,
            Phase.PLAN,
            "Action parsed from Claude response",
            action=action,
            result={"iteration": ctx.iteration},
        )
        return action

    def _build_prompt(self, *, ticket: Any, ctx: RunContext) -> Any:
        base_prompts = self._invoke_component(
            component=self.prompt_builder,
            method_names=("get_base_prompts", "load_base_prompts", "base_prompts"),
            attempts=[((), {}), ((ticket,), {})],
            component_name="prompt_builder",
            required=False,
        )
        if base_prompts is None:
            base_prompts = []

        ticket_context = self._extract_ticket_context(ticket)
        client_context = self._extract_client_context(ticket)
        run_context = self._context_payload(ctx)

        payload = {
            "base_prompts": base_prompts,
            "ticket_context": ticket_context,
            "client_context": client_context,
            "run_context": run_context,
            "history": list(ctx.history),
        }

        return self._invoke_component(
            component=self.prompt_builder,
            method_names=("build_prompt", "build", "compile_prompt", "create_prompt"),
            attempts=[
                ((), payload),
                ((payload,), {}),
                ((base_prompts, ticket_context, client_context, run_context), {}),
            ],
            component_name="prompt_builder",
            required=True,
        )

    def _call_claude(self, *, prompt: Any, ticket: Any, ctx: RunContext) -> Any:
        model_name = self._resolve_llm_model(ticket)
        payload = {
            "prompt": prompt,
            "ticket_id": ctx.ticket_id,
            "run_id": ctx.run_id,
            "correlation_id": ctx.correlation_id,
            "iteration": ctx.iteration,
            "history": list(ctx.history),
            "ticket": ticket,
        }
        if model_name:
            payload["model"] = model_name

        return self._invoke_component(
            component=self.claude_adapter,
            method_names=("call_claude", "invoke", "generate", "complete", "run", "request"),
            attempts=[
                ((), payload),
                ((prompt,), {}),
                ((payload,), {}),
            ],
            component_name="claude_adapter",
            required=True,
        )

    def _resolve_llm_model(self, ticket: Any) -> str | None:
        if isinstance(ticket, Mapping):
            direct = ticket.get("llm_model") or ticket.get("model")
            if direct:
                return str(direct)

            llm = ticket.get("llm")
            if isinstance(llm, Mapping) and llm.get("model"):
                return str(llm.get("model"))

            client_context = ticket.get("client_context")
            if isinstance(client_context, Mapping):
                nested = client_context.get("llm_model") or client_context.get("model")
                if nested:
                    return str(nested)

        model_attr = self._get_value(ticket, "llm_model", "model", default=None)
        return str(model_attr) if model_attr is not None else None

    def _parse_action(self, *, response: Any, ctx: RunContext) -> Any:
        parsed = self._invoke_component(
            component=self.claude_adapter,
            method_names=("parse_response", "parse_action", "extract_action"),
            attempts=[
                ((response,), {}),
                ((), {"response": response}),
            ],
            component_name="claude_adapter",
            required=False,
        )
        if parsed is not None:
            return parsed

        if isinstance(response, Mapping):
            if "action" in response:
                return response["action"]

            actions = response.get("actions")
            if isinstance(actions, list) and actions:
                return actions[0]

            response_status = str(response.get("status", "")).upper()
            if response_status in {"COMPLETED", "DONE", "RESUELTO"}:
                return {"type": "COMPLETE", "status": "COMPLETED", "raw_response": response}

            return {"type": "MODEL_RESPONSE", "payload": dict(response)}

        if isinstance(response, str):
            return {"type": "MODEL_MESSAGE", "content": response}

        return {"type": "MODEL_OUTPUT", "payload": response}

    def _dispatch_action(
        self,
        *,
        ticket: Any,
        ctx: RunContext,
        action: Any,
        extra_context: Mapping[str, Any] | None = None,
    ) -> Any:
        self._audit(
            ctx,
            Phase.POLICY,
            "Dispatching action to action_gateway",
            action=action,
            result={"execution_status": ctx.execution_status.value},
        )

        prepared_action = self._normalize_action_for_ticket_context(action=action, ticket=ticket)

        payload = {
            "action": prepared_action,
            "ticket": ticket,
            "run_context": self._context_payload(ctx),
            "policy_engine": self.policy_engine,
            "execution_service": self.execution_service,
        }
        combined_context = self._build_action_context(ticket=ticket, ctx=ctx)
        if isinstance(extra_context, Mapping):
            combined_context.update(dict(extra_context))
        payload["context"] = combined_context

        gateway_result = self._invoke_component(
            component=self.action_gateway,
            method_names=("handle_action", "process_action", "route_action", "dispatch", "execute"),
            attempts=[
                ((), payload),
                (
                    (prepared_action,),
                    {
                        "ticket": ticket,
                        "run_context": self._context_payload(ctx),
                        "context": dict(extra_context or {}),
                    },
                ),
                ((payload,), {}),
            ],
            component_name="action_gateway",
            required=True,
        )

        self._audit(
            ctx,
            Phase.POLICY,
            "Gateway returned result",
            action=action,
            result=gateway_result,
        )
        return gateway_result

    def _build_action_context(self, *, ticket: Any, ctx: RunContext) -> dict[str, Any]:
        ticket_scope = self._ticket_scope_context(ticket)
        workspace_root = str(ticket_scope.get("scope_root") or PROJECT_ROOT)
        context: dict[str, Any] = {
            "workspace_root": workspace_root,
            "working_dir": workspace_root,
            "capability_level": self._extract_client_context(ticket).get("capability_level"),
        }
        context.update(ticket_scope)

        ticket_api = ticket.get("ticket_api") if isinstance(ticket, Mapping) else {}
        if isinstance(ticket_api, Mapping):
            base_url = str(ticket_api.get("base_url") or "")
            if base_url:
                parsed = urlparse(base_url)
                host = parsed.hostname
                if host:
                    existing = context.get("network_allowlist")
                    if isinstance(existing, list):
                        if host not in existing:
                            existing.append(host)
                    else:
                        context["network_allowlist"] = [str(host)]

        return context

    def _ticket_scope_context(self, ticket: Any) -> dict[str, Any]:
        if not isinstance(ticket, Mapping):
            return {}

        task = ticket.get("task")
        if not isinstance(task, Mapping):
            return {}

        allowed_files: set[str] = set()
        allowed_directories: set[str] = set()

        for key in ("target_file", "expected_artifact"):
            raw_path = task.get(key)
            if not raw_path:
                continue
            resolved = self._resolve_ticket_path(str(raw_path))
            allowed_files.add(str(resolved))
            allowed_directories.add(str(resolved.parent))

        if not allowed_files and not allowed_directories:
            return {}

        scope_root = self._derive_scope_root(allowed_files, allowed_directories)

        return {
            "allowed_paths": sorted(allowed_files),
            "allowed_directories": sorted(allowed_directories),
            "scope_root": str(scope_root),
        }

    def _resolve_ticket_path(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

    def _derive_scope_root(self, allowed_files: set[str], allowed_directories: set[str]) -> Path:
        if all(self._is_relative_to(Path(path), PROJECT_ROOT) for path in allowed_files | allowed_directories):
            return PROJECT_ROOT
        candidates = sorted(allowed_files | allowed_directories)
        return Path(candidates[0]).parent if len(candidates) == 1 else Path(os.path.commonpath(candidates))

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _normalize_action_for_ticket_context(self, *, action: Any, ticket: Any) -> Any:
        if not isinstance(ticket, Mapping):
            return action
        normalized_action = action
        if not isinstance(normalized_action, Mapping):
            to_dict = getattr(normalized_action, "to_dict", None)
            if callable(to_dict):
                value = to_dict()
                if isinstance(value, Mapping):
                    normalized_action = dict(value)
            elif hasattr(normalized_action, "__dict__"):
                normalized_action = {
                    key: value
                    for key, value in vars(normalized_action).items()
                    if not key.startswith("_")
                }
        if not isinstance(normalized_action, Mapping):
            return action
        normalizer = getattr(self.ticket_client, "normalize_http_action_for_ticket", None)
        if callable(normalizer):
            try:
                return normalizer(dict(normalized_action), dict(ticket))
            except Exception:
                return action
        return action

    def _normalize_gateway_result(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            return {
                "status": GatewayStatus.EXECUTION_FAILURE.value,
                "result": None,
                "message": "Gateway returned non-mapping result",
                "error": str(raw),
                "error_type": ErrorType.UNKNOWN.value,
                "pending_decision": None,
                "ticket_status": None,
            }

        status_raw = str(raw.get("status") or raw.get("execution_status") or "").upper()
        status_map = {
            "EXECUTED": GatewayStatus.EXECUTED.value,
            "ALLOWED": GatewayStatus.EXECUTED.value,
            "SUCCESS": GatewayStatus.EXECUTED.value,
            "OK": GatewayStatus.EXECUTED.value,
            "BLOCKED": GatewayStatus.BLOCKED.value,
            "DENIED": GatewayStatus.BLOCKED.value,
            "POLICY_BLOCKED": GatewayStatus.BLOCKED.value,
            "WAITING_DECISION": GatewayStatus.WAITING_DECISION.value,
            "PENDING_DECISION": GatewayStatus.WAITING_DECISION.value,
            "HUMAN_REVIEW_REQUIRED": GatewayStatus.WAITING_DECISION.value,
            "EXECUTION_FAILURE": GatewayStatus.EXECUTION_FAILURE.value,
            "FAILED": GatewayStatus.EXECUTION_FAILURE.value,
            "ERROR": GatewayStatus.EXECUTION_FAILURE.value,
            "TIMEOUT": GatewayStatus.EXECUTION_FAILURE.value,
            "COMPLETED": GatewayStatus.COMPLETED.value,
            "DONE": GatewayStatus.COMPLETED.value,
            "RESUELTO": GatewayStatus.COMPLETED.value,
            "DERIVED": GatewayStatus.COMPLETED.value,
        }

        normalized_status = status_map.get(status_raw, GatewayStatus.UNKNOWN.value)

        if normalized_status == GatewayStatus.UNKNOWN.value:
            if bool(raw.get("blocked")):
                normalized_status = GatewayStatus.BLOCKED.value
            elif raw.get("pending_decision") is not None:
                normalized_status = GatewayStatus.WAITING_DECISION.value
            elif raw.get("error") is not None:
                normalized_status = GatewayStatus.EXECUTION_FAILURE.value
            elif bool(raw.get("completed")):
                normalized_status = GatewayStatus.COMPLETED.value
            else:
                normalized_status = GatewayStatus.EXECUTED.value

        return {
            "status": normalized_status,
            "result": raw.get("result"),
            "message": raw.get("message") or raw.get("reason"),
            "error": raw.get("error"),
            "error_type": raw.get("error_type"),
            "pending_decision": raw.get("pending_decision"),
            "ticket_status": raw.get("ticket_status"),
            "raw": dict(raw),
        }

    def _handle_executed(
        self, *, ctx: RunContext, action: Any, gateway_result: Mapping[str, Any]
    ) -> None:
        exec_result = gateway_result.get("result")
        ctx.history.append(
            {
                "type": "EXECUTION_RESULT",
                "timestamp": self._ts(self._now()),
                "action": action,
                "result": exec_result,
            }
        )

        self._audit(
            ctx,
            Phase.EXEC,
            "Action executed successfully",
            action=action,
            result=exec_result,
        )
        self._audit(
            ctx,
            Phase.VERIFY,
            "Execution result recorded and fed back to planning context",
            action=action,
            result={"history_size": len(ctx.history)},
        )

    def _handle_blocked(
        self,
        *,
        ctx: RunContext,
        ticket: Any,
        action: Any,
        gateway_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        reason = str(
            gateway_result.get("message")
            or gateway_result.get("error")
            or "Action blocked by policy"
        )
        self._set_execution_status(
            ctx, ExecutionStatus.BLOCKED, ticket=ticket, ticket_status="BLOCKED"
        )

        failure_response = self._build_failure_response(
            ctx=ctx,
            error_type=ErrorType.POLICY_BLOCKED,
            message=reason,
            action=action,
            details=dict(gateway_result),
        )

        self._audit(
            ctx,
            Phase.ERROR,
            "Policy blocked action",
            action=action,
            result=failure_response,
        )

        return {
            "status": "FAILED",
            "execution_status": ctx.execution_status.value,
            "run_context": self._context_payload(ctx),
            "failure_response": failure_response,
        }

    def _handle_waiting_decision(
        self,
        *,
        ctx: RunContext,
        ticket: Any,
        action: Any,
        gateway_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        pending_decision = gateway_result.get("pending_decision")
        if pending_decision is None:
            pending_decision = self._request_human_decision(
                ctx=ctx,
                action=action,
                reason=str(gateway_result.get("message") or "Human decision required"),
            )

        ctx.pending_decision = (
            dict(pending_decision)
            if isinstance(pending_decision, Mapping)
            else {"value": pending_decision}
        )

        self._set_execution_status(
            ctx,
            ExecutionStatus.WAITING_DECISION,
            ticket=ticket,
            ticket_status="WAITING_DECISION",
            pending_decision=ctx.pending_decision,
        )

        self._audit(
            ctx,
            Phase.POLICY,
            "Execution paused waiting for human decision",
            action=action,
            result=ctx.pending_decision,
        )
        self._persist_pause_state(ctx=ctx, ticket=ticket)

        return {
            "status": "WAITING_DECISION",
            "execution_status": ctx.execution_status.value,
            "run_context": self._context_payload(ctx),
            "pending_decision": ctx.pending_decision,
        }

    def _handle_execution_failure(
        self,
        *,
        ctx: RunContext,
        action: Any,
        gateway_result: Mapping[str, Any],
    ) -> tuple[bool, ErrorType, str]:
        error_type = self._classify_error(
            declared_error_type=gateway_result.get("error_type"),
            error=gateway_result.get("error"),
        )
        error_message = str(
            gateway_result.get("message")
            or gateway_result.get("error")
            or "Execution failure without explicit message"
        )

        current_attempt = self._retry_counters.get(error_type, 0) + 1
        self._retry_counters[error_type] = current_attempt
        allowed_attempts = self.retry_limits.get(error_type, 0)
        should_retry = current_attempt <= allowed_attempts

        failure_payload = {
            "error_type": error_type.value,
            "error_message": error_message,
            "attempt": current_attempt,
            "max_attempts": allowed_attempts,
            "retrying": should_retry,
        }

        ctx.history.append(
            {
                "type": "EXECUTION_FAILURE",
                "timestamp": self._ts(self._now()),
                "action": action,
                "failure": failure_payload,
            }
        )

        self._audit(
            ctx,
            Phase.ERROR,
            "Execution failure detected",
            action=action,
            result=failure_payload,
        )

        return should_retry, error_type, error_message

    def _complete_run(
        self,
        *,
        ctx: RunContext,
        ticket: Any,
        gateway_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        ticket_status = str(gateway_result.get("ticket_status") or "RESUELTO")
        self._set_execution_status(
            ctx,
            ExecutionStatus.COMPLETED,
            ticket=ticket,
            ticket_status=ticket_status,
        )

        summary = self._build_execution_summary(ctx=ctx, final_result=gateway_result.get("result"))

        self._audit(
            ctx,
            Phase.VERIFY,
            "Run completed",
            result=summary,
        )

        return {
            "status": ticket_status,
            "execution_status": ctx.execution_status.value,
            "run_context": self._context_payload(ctx),
            "execution_summary": summary,
        }

    def _fail_run(
        self,
        *,
        ctx: RunContext,
        ticket: Any,
        error_type: ErrorType,
        message: str,
        action: Any,
        details: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        self._set_execution_status(
            ctx, ExecutionStatus.FAILED, ticket=ticket, ticket_status="FAILED"
        )
        failure_response = self._build_failure_response(
            ctx=ctx,
            error_type=error_type,
            message=message,
            action=action,
            details=details,
        )

        self._audit(
            ctx,
            Phase.ERROR,
            "Run failed",
            action=action,
            result=failure_response,
        )

        return {
            "status": "FAILED",
            "execution_status": ctx.execution_status.value,
            "run_context": self._context_payload(ctx),
            "failure_response": failure_response,
        }

    def _request_human_decision(
        self, *, ctx: RunContext, action: Any, reason: str
    ) -> dict[str, Any]:
        payload = {
            "ticket_id": ctx.ticket_id,
            "run_id": ctx.run_id,
            "correlation_id": ctx.correlation_id,
            "action": action,
            "reason": reason,
            "requested_at": self._ts(self._now()),
        }

        decision = self._invoke_component(
            component=self.decision_engine,
            method_names=(
                "build_decision_request",
                "create_decision_request",
                "request_decision",
                "build",
            ),
            attempts=[
                ((), payload),
                ((payload,), {}),
            ],
            component_name="decision_engine",
            required=False,
        )

        if decision is None:
            return payload
        if isinstance(decision, Mapping):
            return dict(decision)
        return {"decision_payload": decision, **payload}

    def _set_execution_status(
        self,
        ctx: RunContext,
        status: ExecutionStatus,
        *,
        ticket: Any,
        ticket_status: str | None = None,
        pending_decision: Mapping[str, Any] | None = None,
    ) -> None:
        ctx.execution_status = status
        ctx.updated_at = self._now()

        state_payload = {
            "ticket_id": ctx.ticket_id,
            "run_id": ctx.run_id,
            "correlation_id": ctx.correlation_id,
            "execution_status": status.value,
            "ticket_status": ticket_status,
            "updated_at": self._ts(ctx.updated_at),
            "pending_decision": dict(pending_decision) if pending_decision else None,
        }

        self._invoke_component(
            component=self.state_manager,
            method_names=(
                "update_execution_status",
                "set_execution_status",
                "update_run_state",
                "save_run_state",
                "persist_state",
            ),
            attempts=[
                ((), state_payload),
                ((state_payload,), {}),
                (
                    (),
                    {
                        "ticket_id": ctx.ticket_id,
                        "run_id": ctx.run_id,
                        "execution_status": status.value,
                        "ticket_status": ticket_status,
                        "pending_decision": pending_decision,
                    },
                ),
            ],
            component_name="state_manager",
            required=False,
        )

        if ticket_status is not None:
            self._invoke_component(
                component=self.state_manager,
                method_names=(
                    "set_ticket_status",
                    "update_ticket_status",
                    "set_status",
                    "update_status",
                ),
                attempts=[
                    (
                        (),
                        {"ticket_id": ctx.ticket_id, "status": ticket_status, "run_id": ctx.run_id},
                    ),
                    ((ctx.ticket_id, ticket_status), {}),
                    (
                        (
                            {
                                "ticket_id": ctx.ticket_id,
                                "status": ticket_status,
                                "run_id": ctx.run_id,
                            },
                        ),
                        {},
                    ),
                ],
                component_name="state_manager",
                required=False,
            )

    def _build_failure_response(
        self,
        *,
        ctx: RunContext,
        error_type: ErrorType,
        message: str,
        action: Any,
        details: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "ticket_id": ctx.ticket_id,
            "run_id": ctx.run_id,
            "correlation_id": ctx.correlation_id,
            "execution_status": ctx.execution_status.value,
            "error_type": error_type.value,
            "message": message,
            "action": action,
            "details": dict(details) if isinstance(details, Mapping) else details,
            "timestamp": self._ts(self._now()),
        }

    def _build_execution_summary(self, *, ctx: RunContext, final_result: Any) -> dict[str, Any]:
        ended_at = self._now()
        duration = (ended_at - ctx.started_at).total_seconds()
        return {
            "run_id": ctx.run_id,
            "ticket_id": ctx.ticket_id,
            "correlation_id": ctx.correlation_id,
            "started_at": self._ts(ctx.started_at),
            "ended_at": self._ts(ended_at),
            "duration_seconds": duration,
            "iterations": ctx.iteration,
            "execution_status": ctx.execution_status.value,
            "history_events": len(ctx.history),
            "fallback_audit_events": len(self._local_audit_events),
            "final_result": final_result,
        }

    def _persist_pause_state(self, *, ctx: RunContext, ticket: Any) -> None:
        pending_decision = ctx.pending_decision
        if isinstance(pending_decision, Mapping):
            self._invoke_component(
                component=getattr(self.pause_resume_controller, "decision_store", None),
                method_names=("save", "create", "upsert", "store", "add"),
                attempts=[((dict(pending_decision),), {}), ((), {"decision": dict(pending_decision)})],
                component_name="decision_store",
                required=False,
            )

        checkpoint_payload = {
            "run_id": ctx.run_id,
            "ticket_id": ctx.ticket_id,
            "run_context": self._context_payload(ctx),
            "history": list(ctx.history),
            "pending_decision": dict(pending_decision) if isinstance(pending_decision, Mapping) else None,
            "ticket": self._extract_ticket_context(ticket),
            "saved_at": self._ts(self._now()),
        }
        self._invoke_component(
            component=self.checkpoint_store,
            method_names=("save_checkpoint", "save_run_state", "persist_state"),
            attempts=[((checkpoint_payload,), {}), ((), checkpoint_payload)],
            component_name="checkpoint_store",
            required=False,
        )

    def _context_payload(self, ctx: RunContext) -> dict[str, Any]:
        return {
            "run_id": ctx.run_id,
            "ticket_id": ctx.ticket_id,
            "correlation_id": ctx.correlation_id,
            "started_at": self._ts(ctx.started_at),
            "updated_at": self._ts(ctx.updated_at),
            "execution_status": ctx.execution_status.value,
            "iteration": ctx.iteration,
            "pending_decision": ctx.pending_decision,
        }

    def _extract_ticket_context(self, ticket: Any) -> dict[str, Any]:
        if isinstance(ticket, Mapping):
            return dict(ticket)
        if hasattr(ticket, "__dict__"):
            return {k: v for k, v in vars(ticket).items() if not k.startswith("_")}
        return {"raw_ticket": str(ticket)}

    def _extract_client_context(self, ticket: Any) -> dict[str, Any]:
        client_context = self._get_value(
            ticket,
            "client_context",
            "client",
            "tenant_context",
            "tenant",
            "account_context",
            default=None,
        )
        if isinstance(client_context, Mapping):
            return dict(client_context)
        if client_context is None:
            return {}
        return {"value": client_context}

    def _extract_ticket_id(self, ticket: Any) -> str:
        ticket_id = self._get_value(ticket, "ticket_id", "id", "uuid", default=None)
        return str(ticket_id) if ticket_id is not None else "unknown-ticket"

    @staticmethod
    def _extract_action_id(action: Any) -> str:
        if isinstance(action, Mapping):
            value = action.get("action_id") or action.get("id")
            if value is not None:
                return str(value)
        return f"action-{uuid4()}"

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    @staticmethod
    def _execution_status_from_value(value: Any) -> ExecutionStatus:
        normalized = str(value or "RUNNING").upper()
        for item in ExecutionStatus:
            if item.value == normalized:
                return item
        return ExecutionStatus.RUNNING

    def _audit(
        self,
        ctx: RunContext,
        phase: Phase,
        message: str,
        *,
        action: Any = None,
        result: Any = None,
    ) -> None:
        event = {
            "phase": phase.value,
            "timestamp": self._ts(self._now()),
            "message": message,
            "action": action,
            "result": result,
            "run_id": ctx.run_id,
            "ticket_id": ctx.ticket_id,
            "correlation_id": ctx.correlation_id,
            "iteration": ctx.iteration,
            "execution_status": ctx.execution_status.value,
        }

        attempts = [((event,), {}), ((), {"event": event}), ((), event)]
        type_errors: list[str] = []
        emitted = False

        for method_name in ("log_event", "emit", "log", "write", "record"):
            fn = getattr(self.audit_logger, method_name, None)
            if not callable(fn):
                continue
            for args, kwargs in attempts:
                try:
                    fn(*args, **kwargs)
                    emitted = True
                    break
                except TypeError as exc:
                    type_errors.append(f"{method_name}: {exc}")
                    continue
            if emitted:
                break

        if not emitted and callable(self.audit_logger):
            for args, kwargs in attempts:
                try:
                    self.audit_logger(*args, **kwargs)
                    emitted = True
                    break
                except TypeError as exc:
                    type_errors.append(f"__call__: {exc}")
                    continue

        if not emitted:
            fallback_event = dict(event)
            if type_errors:
                fallback_event["logger_errors"] = type_errors
            self._local_audit_events.append(fallback_event)

    def _is_completion_signal(
        self,
        *,
        action: Any,
        gateway_result: Mapping[str, Any],
        ticket: Any,
        ctx: RunContext,
    ) -> bool:
        if gateway_result.get("status") == GatewayStatus.COMPLETED.value:
            return True

        result = gateway_result.get("result")
        if isinstance(result, Mapping) and bool(result.get("completed")):
            return True

        if self._ticket_goal_satisfied(ticket=ticket, ctx=ctx):
            return True

        if isinstance(action, Mapping):
            action_type = str(
                action.get("type") or action.get("action") or action.get("name") or ""
            ).upper()
            if action_type in {"COMPLETE", "COMPLETED", "DONE", "FINALIZE", "RESOLVE", "RESUELTO"}:
                return True
            if bool(action.get("completed")):
                return True

        return False

    def _ticket_goal_satisfied(self, *, ticket: Any, ctx: RunContext) -> bool:
        if not isinstance(ticket, Mapping):
            return False

        task = ticket.get("task")
        if not isinstance(task, Mapping):
            return False

        expected_artifact = task.get("expected_artifact")
        expected_content = task.get("expected_content")
        if not expected_artifact:
            return False

        artifact_path = str(self._resolve_ticket_path(str(expected_artifact)))
        normalized_expected_content = (
            str(expected_content).strip() if expected_content is not None else None
        )
        instructions = task.get("instructions")
        acceptance_criteria = task.get("acceptance_criteria")
        requires_post_write_verification = bool(normalized_expected_content is not None)
        if isinstance(instructions, list):
            instructions_text = " ".join(str(item).lower() for item in instructions)
            requires_post_write_verification = requires_post_write_verification or any(
                marker in instructions_text for marker in ("verifica", "verify", "asegura")
            )
        if isinstance(acceptance_criteria, list) and acceptance_criteria:
            requires_post_write_verification = True

        saw_matching_write = False
        saw_matching_read = not requires_post_write_verification

        for item in ctx.history:
            if not isinstance(item, Mapping) or item.get("type") != "EXECUTION_RESULT":
                continue
            exec_result = item.get("result")
            if not isinstance(exec_result, Mapping):
                continue

            result_meta = exec_result.get("result")
            if not isinstance(result_meta, Mapping):
                continue

            result_path = str(result_meta.get("path") or "")
            if result_path != artifact_path:
                continue

            operation = str(result_meta.get("operation") or "").lower()
            if operation in {"write", "overwrite", "append"}:
                saw_matching_write = True

            if normalized_expected_content is not None and operation == "read":
                stdout_value = str(exec_result.get("stdout") or "").strip()
                if stdout_value == normalized_expected_content:
                    saw_matching_read = True

        return saw_matching_write and saw_matching_read

    def _classify_error(self, *, declared_error_type: Any = None, error: Any = None) -> ErrorType:
        if declared_error_type is not None:
            normalized = self._error_type_from_value(declared_error_type)
            if normalized != ErrorType.UNKNOWN:
                return normalized

        if isinstance(error, TimeoutError):
            return ErrorType.TIMEOUT

        if isinstance(error, Mapping):
            embedded = error.get("error_type") or error.get("type") or error.get("code")
            if embedded:
                normalized = self._error_type_from_value(embedded)
                if normalized != ErrorType.UNKNOWN:
                    return normalized
            error = error.get("message") or str(error)

        message = str(error or "").lower()
        if "timeout" in message:
            return ErrorType.TIMEOUT
        if "policy" in message and ("block" in message or "deny" in message):
            return ErrorType.POLICY_BLOCKED
        if "tool" in message or "executor" in message or "command" in message:
            return ErrorType.TOOL_ERROR
        if "validation" in message or "invalid" in message:
            return ErrorType.VALIDATION_ERROR
        return ErrorType.UNKNOWN

    @staticmethod
    def _error_type_from_value(value: Any) -> ErrorType:
        if isinstance(value, ErrorType):
            return value
        normalized = str(value or "").upper()
        lookup = {
            "TOOL_ERROR": ErrorType.TOOL_ERROR,
            "TIMEOUT": ErrorType.TIMEOUT,
            "POLICY_BLOCKED": ErrorType.POLICY_BLOCKED,
            "BLOCKED": ErrorType.POLICY_BLOCKED,
            "VALIDATION_ERROR": ErrorType.VALIDATION_ERROR,
            "MAX_ITERATIONS": ErrorType.MAX_ITERATIONS,
            "UNKNOWN": ErrorType.UNKNOWN,
        }
        return lookup.get(normalized, ErrorType.UNKNOWN)

    @staticmethod
    def _get_value(obj: Any, *keys: str, default: Any = None) -> Any:
        if isinstance(obj, Mapping):
            for key in keys:
                if key in obj:
                    return obj[key]
            return default
        for key in keys:
            if hasattr(obj, key):
                return getattr(obj, key)
        return default

    @staticmethod
    def _invoke_component(
        *,
        component: Any,
        method_names: tuple[str, ...],
        attempts: list[tuple[tuple[Any, ...], dict[str, Any]]],
        component_name: str,
        required: bool,
    ) -> Any:
        discovered_callable = False
        type_errors: list[str] = []

        for method_name in method_names:
            fn = getattr(component, method_name, None)
            if not callable(fn):
                continue

            discovered_callable = True
            for args, kwargs in attempts:
                try:
                    return fn(*args, **kwargs)
                except TypeError as exc:
                    type_errors.append(f"{method_name}: {exc}")
                    continue

        if callable(component):
            discovered_callable = True
            for args, kwargs in attempts:
                try:
                    return component(*args, **kwargs)
                except TypeError as exc:
                    type_errors.append(f"__call__: {exc}")
                    continue

        if required:
            if not discovered_callable:
                raise RuntimeError(
                    f"{component_name} does not expose required callable methods: {method_names}"
                )
            details = " | ".join(type_errors) if type_errors else "no compatible signature"
            raise RuntimeError(f"{component_name} invocation failed: {details}")

        return None

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _ts(value: datetime) -> str:
        return value.isoformat()
