from __future__ import annotations

from typing import Any, Mapping

from core.actions import action_gateway
from core.llm.claude_adapter import ClaudeAdapter
from core.mcp.capability_mapper import MCPCapabilityMapper
from core.mcp.client import MCPClient
from core.mcp.registry import MCPRegistry
from core.mcp.session_manager import MCPSessionManager
from core.orchestrator.victor_loop import VictorLoop


class _SingleActionAdapter:
    def __init__(self, action_payload: Mapping[str, Any]) -> None:
        self.action_payload = dict(action_payload)

    def call_claude(self, prompt: str | None = None, **_: Any) -> dict[str, Any]:
        return {"ok": True}

    def parse_response(self, response: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.action_payload)


def test_full_ticket_execution_success_path() -> None:
    adapter = _SingleActionAdapter(
        {
            "action_id": "action-e2e-success",
            "type": "shell",
            "command": ["python", "-c", "print(111)"],
            "description": "execute and finish",
            "completed": True,
        }
    )
    loop = VictorLoop(max_iterations=3, claude_adapter_module=adapter)

    ticket = {
        "ticket_id": "ticket-e2e-success",
        "client_context": {"capability_level": "C1_RESTRINGIDO"},
    }

    result = loop.run(ticket)

    assert result["status"] == "RESUELTO"
    assert result["execution_status"] == "COMPLETED"
    assert result.get("execution_summary")


def test_full_ticket_execution_blocked_path() -> None:
    adapter = _SingleActionAdapter(
        {
            "action_id": "action-e2e-blocked",
            "type": "shell",
            "command": "rm -rf /",
            "description": "dangerous command",
        }
    )
    loop = VictorLoop(max_iterations=2, claude_adapter_module=adapter)

    ticket = {
        "ticket_id": "ticket-e2e-blocked",
        "client_context": {"capability_level": "C3_ELEVADO_SUPERVISADO"},
    }

    result = loop.run(ticket)

    assert result["status"] == "FAILED"
    assert result["execution_status"] == "BLOCKED"
    assert result["failure_response"]["error_type"] == "POLICY_BLOCKED"


def test_malformed_claude_response_is_handled_safely() -> None:
    adapter = ClaudeAdapter(sdk_client=lambda prompt, context=None: "<<not-json-response>>")
    action = adapter.generate_action("test prompt", {"run_id": "r-malformed", "ticket_id": "t-malformed"})

    assert action.type == "file"
    assert action.parameters["operation"] == "exists"
    assert action.parameters["path"] == "."


def test_policy_engine_failure_does_not_crash_loop() -> None:
    class BrokenPolicyEngine:
        @staticmethod
        def validate(action: Any, context: Mapping[str, Any]) -> dict[str, Any]:
            raise RuntimeError("forced policy failure")

    adapter = _SingleActionAdapter(
        {
            "action_id": "action-e2e-policy-failure",
            "type": "shell",
            "command": ["python", "-c", "print(1)"],
            "description": "policy failure path",
        }
    )

    loop = VictorLoop(
        max_iterations=1,
        claude_adapter_module=adapter,
        action_gateway_module=action_gateway,
        policy_engine_module=BrokenPolicyEngine(),
    )
    result = loop.run(
        {
            "ticket_id": "ticket-e2e-policy-failure",
            "client_context": {"capability_level": "C1_RESTRINGIDO"},
        }
    )

    assert result["status"] == "FAILED"
    assert result["execution_status"] == "FAILED"
    assert result["failure_response"]["error_type"] in {"UNKNOWN", "TOOL_ERROR", "VALIDATION_ERROR"}


def test_execution_timeout_is_classified() -> None:
    class AllowAllPolicy:
        @staticmethod
        def validate(action: Any, context: Mapping[str, Any]) -> dict[str, Any]:
            return {"status": "ALLOWED", "reason": "test"}

    class TimeoutExecutionService:
        @staticmethod
        def execute(action: Any, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
            raise TimeoutError("simulated execution timeout")

    adapter = _SingleActionAdapter(
        {
            "action_id": "action-e2e-timeout",
            "type": "shell",
            "command": ["python", "-c", "print(1)"],
            "description": "timeout path",
        }
    )

    loop = VictorLoop(
        max_iterations=1,
        retry_limits={"TIMEOUT": 0},
        claude_adapter_module=adapter,
        action_gateway_module=action_gateway,
        policy_engine_module=AllowAllPolicy(),
        execution_service_module=TimeoutExecutionService(),
    )
    result = loop.run(
        {
            "ticket_id": "ticket-e2e-timeout",
            "client_context": {"capability_level": "C1_RESTRINGIDO"},
        }
    )

    assert result["status"] == "FAILED"
    assert result["execution_status"] == "FAILED"
    assert result["failure_response"]["error_type"] == "TIMEOUT"


def test_mcp_connection_failure_is_handled() -> None:
    def failing_adapter(tool_name: str, params: dict[str, Any], **_: Any) -> dict[str, Any]:
        raise ConnectionError(f"unable to connect to {tool_name}")

    registry = MCPRegistry(allowlist={"siem.search"}, deny_by_default=True)
    registry.register_tool("siem.search", failing_adapter)

    mapper = MCPCapabilityMapper()
    mapper.register_tool("siem.search", "C1_RESTRINGIDO")

    client = MCPClient(
        registry_module=registry,
        capability_mapper_module=mapper,
        session_manager_module=MCPSessionManager(),
    )

    result = client.call_tool(
        "siem.search",
        parameters={"query": "failed logins"},
        context={"run_id": "run-e2e-mcp-fail", "capability_level": "C1_RESTRINGIDO"},
        timeout_seconds=2,
    )

    assert result["status"] == "FAILED"
    assert result["success"] is False
    assert result["error_type"] == "CONNECTION_FAILURE"
