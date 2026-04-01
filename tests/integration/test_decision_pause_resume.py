from __future__ import annotations

from typing import Any, Mapping

from core.orchestrator.victor_loop import VictorLoop


class _StatefulClaudeAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def call_claude(self, prompt: str | None = None, **_: Any) -> dict[str, int]:
        self.calls += 1
        return {"call": self.calls}

    def parse_response(self, response: Mapping[str, Any]) -> dict[str, Any]:
        if int(response.get("call", 0)) == 1:
            return {
                "action_id": "action-pause-1",
                "type": "shell",
                "command": "sudo ls /",
                "description": "requires elevation",
            }
        return {
            "action_id": "action-resume-1",
            "type": "shell",
            "command": ["python", "-c", "print(321)"],
            "description": "resume and complete",
            "completed": True,
        }


def test_decision_pause_then_resume_flow() -> None:
    adapter = _StatefulClaudeAdapter()
    loop = VictorLoop(max_iterations=3, claude_adapter_module=adapter)

    ticket = {
        "ticket_id": "ticket-pause-resume",
        "client_context": {"capability_level": "C2_CONTROLADO"},
    }

    paused = loop.run(ticket)
    pending = paused.get("pending_decision") or {}

    assert paused["execution_status"] == "WAITING_DECISION"
    assert paused["status"] == "WAITING_DECISION"
    assert pending.get("decision_id")
    assert pending.get("question")
    assert pending.get("expires_at")

    resumed = loop.run(ticket)
    assert resumed["execution_status"] == "COMPLETED"
    assert resumed["status"] == "RESUELTO"
    assert resumed.get("execution_summary")


def test_decision_payload_contains_a_b_c_options() -> None:
    adapter = _StatefulClaudeAdapter()
    loop = VictorLoop(max_iterations=1, claude_adapter_module=adapter)

    ticket = {
        "ticket_id": "ticket-decision-options",
        "client_context": {"capability_level": "C2_CONTROLADO"},
    }

    result = loop.run(ticket)
    pending = result.get("pending_decision") or {}
    option_ids = [str(item.get("id")) for item in pending.get("options", [])]

    assert result["execution_status"] == "WAITING_DECISION"
    assert option_ids == ["A", "B", "C"]
    assert pending.get("recommended_option") in {"A", "B", "C"}
