from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import inspect
import json
import re
from typing import Any, Mapping

from core.contracts.action import Action
from core.contracts.event import Event
from core.observability import audit_logger


@dataclass(slots=True)
class _ParseResult:
    action: Action
    recovered: bool
    reason: str


class ClaudeAdapter:
    """Safe adapter between VICTOR and Claude Agent SDK."""

    DEFAULT_MODEL = "claude-sonnet-4-6"

    def __init__(
        self,
        *,
        sdk_client: Any = None,
        audit_logger_module: Any = audit_logger,
        max_logged_text_chars: int = 4000,
        default_model: str = DEFAULT_MODEL,
    ) -> None:
        self.sdk_client = sdk_client
        self.audit_logger = audit_logger_module
        self.max_logged_text_chars = max(256, int(max_logged_text_chars))
        self.default_model = str(default_model or self.DEFAULT_MODEL)
        self._fallback_events: list[dict[str, Any]] = []

    def generate_action(self, prompt: str, context: Mapping[str, Any] | None = None) -> Action:
        raw_response = self.call_claude(prompt=prompt, context=context)
        return self.parse_response(raw_response)

    def call_claude(self, prompt: str, context: Mapping[str, Any] | None = None) -> Any:
        normalized_context = self._normalize_context(context)
        model_name = self._resolve_model(normalized_context)
        self._log_event(
            phase="PROMPT_SENT",
            message="Prompt sent to Claude adapter",
            action_id=None,
            metadata={
                "run_id": normalized_context.get("run_id"),
                "ticket_id": normalized_context.get("ticket_id"),
                "model": model_name,
                "prompt_preview": self._truncate(str(prompt)),
            },
        )

        raw_response = self._invoke_sdk(prompt=prompt, context=normalized_context)

        self._log_event(
            phase="RESPONSE_RECEIVED",
            message="Response received from Claude adapter",
            action_id=None,
            metadata={
                "run_id": normalized_context.get("run_id"),
                "ticket_id": normalized_context.get("ticket_id"),
                "model": model_name,
                "response_preview": self._truncate(self._response_preview(raw_response)),
            },
        )
        return raw_response

    def parse_response(self, raw_response: Any) -> Action:
        try:
            parsed = self._parse_response_internal(raw_response)
            self._log_event(
                phase="PARSING_RESULT",
                message="Model response parsed into structured action",
                action_id=parsed.action.action_id,
                metadata={
                    "action_type": parsed.action.type,
                    "recovered": parsed.recovered,
                    "reason": parsed.reason,
                },
            )
            return parsed.action
        except Exception as exc:
            fallback = Action.safe_fallback(reason=f"parse_exception:{exc}")
            self._log_event(
                phase="PARSING_RESULT",
                message="Parser failed; using safe fallback action",
                action_id=fallback.action_id,
                metadata={
                    "action_type": fallback.type,
                    "recovered": True,
                    "reason": str(exc),
                },
            )
            return fallback

    def parse_action(self, response: Any) -> Action:
        return self.parse_response(response)

    def extract_action(self, response: Any) -> Action:
        return self.parse_response(response)

    def invoke(
        self, prompt: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        effective_prompt, effective_context = self._resolve_call_inputs(prompt, context, kwargs)
        return self.call_claude(prompt=effective_prompt, context=effective_context)

    def generate(
        self, prompt: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        return self.invoke(prompt=prompt, context=context, **kwargs)

    def complete(
        self, prompt: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        return self.invoke(prompt=prompt, context=context, **kwargs)

    def run(
        self, prompt: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        return self.invoke(prompt=prompt, context=context, **kwargs)

    def request(
        self, prompt: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        return self.invoke(prompt=prompt, context=context, **kwargs)

    def _parse_response_internal(self, raw_response: Any) -> _ParseResult:
        payload, recovered_reason = self._extract_payload(raw_response)
        normalized_payload = self._normalize_payload(payload)
        action = self._build_action(normalized_payload)
        return _ParseResult(
            action=action, recovered=bool(recovered_reason), reason=recovered_reason
        )

    def _extract_payload(self, raw_response: Any) -> tuple[dict[str, Any], str]:
        if isinstance(raw_response, Action):
            return raw_response.to_dict(), ""

        if isinstance(raw_response, Mapping):
            if isinstance(raw_response.get("action"), Mapping):
                return dict(raw_response["action"]), "nested_action"
            if self._looks_like_action_payload(raw_response):
                return dict(raw_response), ""

            text_candidate = self._extract_text(raw_response)
            if text_candidate:
                payload = self._parse_json_payload(text_candidate)
                return payload, "from_text"

        if isinstance(raw_response, str):
            payload = self._parse_json_payload(raw_response)
            return payload, "from_string"

        if hasattr(raw_response, "model_dump") and callable(getattr(raw_response, "model_dump")):
            dumped = raw_response.model_dump()
            if isinstance(dumped, Mapping):
                return self._extract_payload(dict(dumped))

        if hasattr(raw_response, "__dict__"):
            return self._extract_payload(vars(raw_response))

        raise ValueError("Unsupported Claude response format")

    def _build_action(self, payload: Mapping[str, Any]) -> Action:
        action_type = str(payload.get("action_type") or payload.get("type") or "").lower()
        parameters = payload.get("parameters")
        if not isinstance(parameters, Mapping):
            raise ValueError("Response parameters must be an object")

        if action_type not in {"shell", "file", "http", "mcp"}:
            raise ValueError(f"Invalid action_type: {action_type}")

        command = payload.get("command")
        if action_type == "shell" and command is None:
            command = parameters.get("command")
        if action_type == "shell" and command is None:
            raise ValueError("Shell action requires parameters.command")

        return Action.from_payload(
            {
                "action_id": payload.get("action_id"),
                "type": action_type,
                "command": command,
                "parameters": dict(parameters),
                "description": payload.get("description"),
                "risk_level": payload.get("risk_level"),
                "confidence": payload.get("confidence"),
                "metadata": payload.get("metadata")
                if isinstance(payload.get("metadata"), Mapping)
                else {},
            }
        )

    def _normalize_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = dict(payload)

        action_type = (
            str(normalized.get("action_type") or normalized.get("type") or "").strip().lower()
        )
        if not action_type:
            if "command" in normalized or (
                isinstance(normalized.get("parameters"), Mapping)
                and "command" in normalized["parameters"]
            ):
                action_type = "shell"
            elif "url" in normalized:
                action_type = "http"
            elif "tool" in normalized:
                action_type = "mcp"
            elif "path" in normalized:
                action_type = "file"
            else:
                action_type = "file"

        parameters_raw = normalized.get("parameters")
        if not isinstance(parameters_raw, Mapping):
            parameters: dict[str, Any] = {}
        else:
            parameters = dict(parameters_raw)

        if action_type == "shell":
            command = normalized.get("command")
            if command is None:
                command = parameters.get("command")
            if command is not None:
                parameters.setdefault("command", command)

        if action_type == "file":
            if "operation" not in parameters:
                if "operation" in normalized:
                    parameters["operation"] = normalized["operation"]
                else:
                    parameters["operation"] = "read"
            if "path" not in parameters and "path" in normalized:
                parameters["path"] = normalized["path"]

        if action_type == "http":
            if "method" not in parameters and "method" in normalized:
                parameters["method"] = normalized["method"]
            if "url" not in parameters and "url" in normalized:
                parameters["url"] = normalized["url"]

        if action_type == "mcp":
            if "tool" not in parameters:
                if "tool" in normalized:
                    parameters["tool"] = normalized["tool"]
                elif "tool_name" in normalized:
                    parameters["tool"] = normalized["tool_name"]
            if "arguments" not in parameters and "arguments" in normalized:
                parameters["arguments"] = normalized["arguments"]

        confidence = normalized.get("confidence")
        if confidence is None:
            confidence = 0.5

        description = str(normalized.get("description") or "").strip()
        if not description:
            description = f"Accion propuesta de tipo {action_type}"

        result = {
            "action_id": normalized.get("action_id"),
            "action_type": action_type,
            "parameters": parameters,
            "command": normalized.get("command") or parameters.get("command"),
            "description": description,
            "risk_level": normalized.get("risk_level"),
            "confidence": confidence,
            "metadata": normalized.get("metadata")
            if isinstance(normalized.get("metadata"), Mapping)
            else {},
        }
        return result

    @staticmethod
    def _looks_like_action_payload(payload: Mapping[str, Any]) -> bool:
        if "action_type" in payload and "parameters" in payload:
            return True
        if "type" in payload and "parameters" in payload:
            return True
        return False

    def _parse_json_payload(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Empty model response")

        fence_match = re.search(
            r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.IGNORECASE | re.DOTALL
        )
        if fence_match:
            cleaned = fence_match.group(1).strip()

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, Mapping):
                return dict(parsed)
        except json.JSONDecodeError:
            pass

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = cleaned[start : end + 1]
            parsed = json.loads(snippet)
            if isinstance(parsed, Mapping):
                return dict(parsed)

        raise ValueError("Could not parse JSON action payload")

    def _extract_text(self, response: Mapping[str, Any]) -> str:
        for key in ("text", "output_text", "completion", "content"):
            value = response.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                parts: list[str] = []
                for item in value:
                    if isinstance(item, Mapping):
                        text = item.get("text")
                        if isinstance(text, str):
                            parts.append(text)
                if parts:
                    return "\n".join(parts)
        return ""

    def _invoke_native_agent_sdk(self, *, prompt: str, context: Mapping[str, Any]) -> Any:
        try:
            from claude_agent_sdk import ClaudeAgentOptions, query
        except Exception as exc:  # pragma: no cover - depends on optional runtime package
            return self._safe_raw_fallback(f"sdk_import_error:{exc}")

        options = self._build_agent_options(
            claude_agent_options_type=ClaudeAgentOptions,
            context=context,
        )

        async def _run_query() -> list[Any]:
            messages: list[Any] = []
            async for message in query(prompt=prompt, options=options):
                messages.append(message)
            return messages

        try:
            messages = self._run_coroutine_sync(_run_query())
        except Exception as exc:  # pragma: no cover - network/process dependent
            return self._safe_raw_fallback(f"sdk_runtime_error:{exc}")

        return self._messages_to_payload(messages)

    def _build_agent_options(
        self, *, claude_agent_options_type: Any, context: Mapping[str, Any]
    ) -> Any:
        merged = self._collect_llm_settings(context)
        supported = self._option_parameter_names(claude_agent_options_type)

        full_payload: dict[str, Any] = {}
        for key in ("model", "system_prompt", "setting_sources", "permission_mode"):
            value = merged.get(key)
            if value is not None:
                full_payload[key] = value

        sanitized_payload = {
            k: v for k, v in full_payload.items() if not supported or k in supported
        }

        candidate_payloads = [
            sanitized_payload,
            {k: sanitized_payload[k] for k in ("model",) if k in sanitized_payload},
            {},
        ]

        for payload in candidate_payloads:
            try:
                return claude_agent_options_type(**payload)
            except TypeError:
                continue

        return claude_agent_options_type()

    @staticmethod
    def _option_parameter_names(option_type: Any) -> set[str]:
        try:
            return set(inspect.signature(option_type).parameters.keys())
        except Exception:
            return set()

    @staticmethod
    def _run_coroutine_sync(coro: Any) -> Any:
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop and running_loop.is_running():
            raise RuntimeError("Cannot run Claude Agent SDK query inside an active event loop")

        return asyncio.run(coro)

    @staticmethod
    def _collect_llm_settings(context: Mapping[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {}

        llm = context.get("llm")
        if isinstance(llm, Mapping):
            merged.update(dict(llm))

        ticket = context.get("ticket")
        if isinstance(ticket, Mapping):
            nested_llm = ticket.get("llm")
            if isinstance(nested_llm, Mapping):
                for key, value in nested_llm.items():
                    merged.setdefault(str(key), value)
            ticket_model = ticket.get("llm_model")
            if ticket_model and "model" not in merged:
                merged["model"] = ticket_model

        for key in (
            "model",
            "llm_model",
            "max_tokens",
            "system_prompt",
            "setting_sources",
            "permission_mode",
        ):
            if key in context and context.get(key) is not None:
                if key == "llm_model":
                    merged["model"] = context[key]
                else:
                    merged[key] = context[key]

        return merged

    def _messages_to_payload(self, messages: list[Any]) -> dict[str, Any]:
        last_result_text = ""
        assistant_text_chunks: list[str] = []

        for message in messages:
            structured = getattr(message, "structured_output", None)
            if isinstance(structured, Mapping):
                return dict(structured)

            result_text = getattr(message, "result", None)
            if isinstance(result_text, str) and result_text.strip():
                last_result_text = result_text.strip()

            content = getattr(message, "content", None)
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, Mapping):
                        text = block.get("text")
                    else:
                        text = getattr(block, "text", None)
                    if isinstance(text, str) and text.strip():
                        assistant_text_chunks.append(text.strip())

        preferred_text = last_result_text or "\n".join(assistant_text_chunks).strip()
        if not preferred_text:
            return self._safe_raw_fallback("sdk_empty_response")

        try:
            return self._parse_json_payload(preferred_text)
        except Exception:
            return {"output_text": preferred_text}

    def _invoke_sdk(self, *, prompt: str, context: Mapping[str, Any]) -> Any:
        client = self._resolve_client(context)
        if client is None:
            return self._invoke_native_agent_sdk(prompt=prompt, context=context)

        model_name = self._resolve_model(context)

        method_names = (
            "generate_action",
            "generate",
            "complete",
            "invoke",
            "run",
            "request",
        )

        for method_name in method_names:
            method = getattr(client, method_name, None)
            if not callable(method):
                continue
            for args, kwargs in (
                ((prompt, context), {"model": model_name}),
                ((prompt, context), {}),
                ((prompt,), {"context": context, "model": model_name}),
                ((prompt,), {"context": context}),
                ((), {"prompt": prompt, "context": context, "model": model_name}),
                ((), {"prompt": prompt, "context": context}),
                ((prompt,), {"model": model_name}),
                ((prompt,), {}),
            ):
                try:
                    return method(*args, **kwargs)
                except TypeError:
                    continue
                except Exception as exc:
                    return self._safe_raw_fallback(f"sdk_exception:{exc}")

        if callable(client):
            for args, kwargs in (
                ((prompt, context), {"model": model_name}),
                ((prompt, context), {}),
                ((prompt,), {"context": context, "model": model_name}),
                ((prompt,), {"context": context}),
                ((prompt,), {"model": model_name}),
                ((prompt,), {}),
            ):
                try:
                    return client(*args, **kwargs)
                except TypeError:
                    continue
                except Exception as exc:
                    return self._safe_raw_fallback(f"sdk_exception:{exc}")

        return self._safe_raw_fallback("No compatible SDK method")

    def _resolve_client(self, context: Mapping[str, Any]) -> Any:
        for key in ("claude_client", "llm_client", "sdk_client"):
            if context.get(key) is not None:
                return context[key]
        return self.sdk_client

    def _resolve_model(self, context: Mapping[str, Any]) -> str:
        llm = context.get("llm")
        if isinstance(llm, Mapping):
            model = llm.get("model")
            if model:
                return str(model)

        for key in ("model", "llm_model"):
            value = context.get(key)
            if value:
                return str(value)

        ticket = context.get("ticket")
        if isinstance(ticket, Mapping):
            if ticket.get("llm_model"):
                return str(ticket.get("llm_model"))
            nested_llm = ticket.get("llm")
            if isinstance(nested_llm, Mapping) and nested_llm.get("model"):
                return str(nested_llm.get("model"))

        return self.default_model

    def _safe_raw_fallback(self, reason: str) -> dict[str, Any]:
        return {
            "action_type": "file",
            "parameters": {
                "operation": "exists",
                "path": ".",
            },
            "description": "Accion de respaldo generada por el adaptador",
            "confidence": 0.0,
            "metadata": {"fallback_reason": reason},
        }

    @staticmethod
    def _normalize_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
        return dict(context or {})

    @staticmethod
    def _resolve_call_inputs(
        prompt: Any,
        context: Mapping[str, Any] | None,
        kwargs: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        effective_context = dict(context or {})
        if isinstance(prompt, Mapping) and context is None and "prompt" in prompt:
            effective_context.update({k: v for k, v in prompt.items() if k != "prompt"})
            effective_prompt = str(prompt.get("prompt") or "")
            effective_context.update({k: v for k, v in kwargs.items() if k != "prompt"})
            return effective_prompt, effective_context

        effective_prompt = str(prompt if prompt is not None else kwargs.get("prompt") or "")
        effective_context.update({k: v for k, v in kwargs.items() if k != "prompt"})
        return effective_prompt, effective_context

    def _log_event(
        self,
        *,
        phase: str,
        message: str,
        action_id: str | None,
        metadata: Mapping[str, Any],
    ) -> None:
        event = Event(
            timestamp=self._ts(self._now()),
            phase=phase,
            message=message,
            action_id=action_id,
            metadata=dict(metadata),
        ).to_dict()

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
            self._fallback_events.append(event)

    def _response_preview(self, response: Any) -> str:
        if isinstance(response, Mapping):
            try:
                return json.dumps(response, ensure_ascii=True, default=str)
            except Exception:
                return str(response)
        if isinstance(response, Action):
            return json.dumps(response.to_dict(), ensure_ascii=True, default=str)
        return str(response)

    def _truncate(self, value: str) -> str:
        if len(value) <= self.max_logged_text_chars:
            return value
        return value[: self.max_logged_text_chars] + "\n...[truncated]"

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _ts(value: datetime) -> str:
        return value.isoformat()


_DEFAULT_ADAPTER = ClaudeAdapter()


def generate_action(prompt: str, context: Mapping[str, Any] | None = None) -> Action:
    return _DEFAULT_ADAPTER.generate_action(prompt=prompt, context=context)


def parse_response(raw_response: Any) -> Action:
    return _DEFAULT_ADAPTER.parse_response(raw_response)


def parse_action(response: Any) -> Action:
    return _DEFAULT_ADAPTER.parse_action(response)


def extract_action(response: Any) -> Action:
    return _DEFAULT_ADAPTER.extract_action(response)


def call_claude(prompt: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
    effective_prompt, effective_context = ClaudeAdapter._resolve_call_inputs(
        prompt, context, kwargs
    )
    return _DEFAULT_ADAPTER.call_claude(prompt=effective_prompt, context=effective_context)


def invoke(prompt: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
    return call_claude(prompt=prompt, context=context, **kwargs)


def generate(prompt: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
    return call_claude(prompt=prompt, context=context, **kwargs)


def complete(prompt: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
    return call_claude(prompt=prompt, context=context, **kwargs)


def run(prompt: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
    return call_claude(prompt=prompt, context=context, **kwargs)


def request(prompt: Any = None, context: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
    return call_claude(prompt=prompt, context=context, **kwargs)
