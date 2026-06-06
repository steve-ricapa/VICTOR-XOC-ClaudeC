from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping

from core.actions import action_gateway
from core.llm.claude_adapter import ClaudeAdapter
from core.mcp.capability_mapper import MCPCapabilityMapper
from core.mcp.client import MCPClient
from core.mcp.registry import MCPRegistry
from core.mcp.session_manager import MCPSessionManager
from core.orchestrator import victor_loop as victor_loop_module
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


class _ArtifactVerificationAdapter:
    def __init__(self, artifact_path: str, expected_content: str) -> None:
        self.calls = 0
        self.artifact_path = artifact_path
        self.expected_content = expected_content

    def call_claude(self, prompt: str | None = None, **_: Any) -> dict[str, int]:
        self.calls += 1
        return {"call": self.calls}

    def parse_response(self, response: Mapping[str, Any]) -> dict[str, Any]:
        call = int(response.get("call", 0))
        if call == 1:
            return {
                "action_id": "action-artifact-write",
                "type": "file",
                "parameters": {
                    "operation": "write",
                    "path": self.artifact_path,
                    "content": self.expected_content,
                },
                "description": "write expected artifact",
            }
        if call == 2:
            return {
                "action_id": "action-artifact-read",
                "type": "file",
                "parameters": {
                    "operation": "read",
                    "path": self.artifact_path,
                },
                "description": "read back artifact",
            }
        return {
            "action_id": "action-extra",
            "type": "file",
            "parameters": {
                "operation": "list",
                "path": str(Path(self.artifact_path).parent),
            },
            "description": "redundant post verification action",
        }


def test_ticket_completes_when_expected_artifact_is_written_and_verified() -> None:
    with TemporaryDirectory() as temp_dir:
        artifact_path = str(Path(temp_dir) / "artifact.txt")
        expected_content = "artifact-ok"
        adapter = _ArtifactVerificationAdapter(artifact_path=artifact_path, expected_content=expected_content)
        loop = VictorLoop(max_iterations=5, claude_adapter_module=adapter)

        ticket = {
            "ticket_id": "ticket-artifact-complete",
            "client_context": {"capability_level": "C2_CONTROLADO"},
            "task": {
                "expected_artifact": artifact_path,
                "expected_content": expected_content,
            },
        }

        result = loop.run(ticket)

        assert result["status"] == "RESUELTO"
        assert result["execution_status"] == "COMPLETED"
        assert adapter.calls == 2


def test_file_write_outside_ticket_scope_is_blocked() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        allowed_file = root / "runtime" / "lab" / "allowed.conf"
        blocked_file = root / "runtime" / "lab" / "other.conf"
        allowed_file.parent.mkdir(parents=True, exist_ok=True)
        allowed_file.write_text("safe=before", encoding="utf-8")

        adapter = _SingleActionAdapter(
            {
                "action_id": "action-scope-blocked",
                "type": "file",
                "parameters": {
                    "operation": "write",
                    "path": str(blocked_file),
                    "content": "safe=after",
                },
                "description": "write outside ticket scope",
            }
        )

        original_root = victor_loop_module.PROJECT_ROOT
        victor_loop_module.PROJECT_ROOT = root
        try:
            loop = VictorLoop(max_iterations=1, claude_adapter_module=adapter)
            result = loop.run(
                {
                    "ticket_id": "ticket-scope-blocked",
                    "client_context": {"capability_level": "C2_CONTROLADO"},
                    "task": {
                        "target_file": str(allowed_file),
                        "expected_artifact": str(allowed_file),
                    },
                }
            )
        finally:
            victor_loop_module.PROJECT_ROOT = original_root

        assert result["status"] == "FAILED"
        assert result["execution_status"] == "BLOCKED"
        assert result["failure_response"]["error_type"] == "POLICY_BLOCKED"


def test_relative_expected_artifact_completes_after_write_and_read(monkeypatch) -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        relative_artifact = Path("runtime/lab/tls_policy.conf")
        absolute_artifact = root / relative_artifact
        absolute_artifact.parent.mkdir(parents=True, exist_ok=True)
        absolute_artifact.write_text("TLS_VERSION=1.2\n", encoding="utf-8")

        adapter = _ArtifactVerificationAdapter(
            artifact_path=str(relative_artifact),
            expected_content="TLS_VERSION=1.3\n",
        )

        monkeypatch.setattr(victor_loop_module, "PROJECT_ROOT", root)
        loop = VictorLoop(max_iterations=5, claude_adapter_module=adapter)
        result = loop.run(
            {
                "ticket_id": "ticket-relative-artifact",
                "client_context": {"capability_level": "C2_CONTROLADO"},
                "task": {
                    "target_file": str(relative_artifact),
                    "expected_artifact": str(relative_artifact),
                    "expected_content": "TLS_VERSION=1.3\n",
                    "acceptance_criteria": ["TLS_VERSION debe ser 1.3"],
                },
            }
        )

        assert result["status"] == "RESUELTO"
        assert result["execution_status"] == "COMPLETED"
        assert adapter.calls == 2


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
