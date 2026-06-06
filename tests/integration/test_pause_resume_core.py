from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from core.decisions.decision_store import DecisionStore
from core.orchestrator.pause_resume_controller import PauseResumeController
from core.orchestrator.victor_loop import VictorLoop
from core.state.checkpoint_store import CheckpointStore


def _build_checkpoint(run_id: str, ticket_id: str, ticket: dict[str, object], decision: dict[str, object]) -> dict[str, object]:
    return {
        "run_id": run_id,
        "ticket_id": ticket_id,
        "run_context": {
            "run_id": run_id,
            "ticket_id": ticket_id,
            "correlation_id": f"{ticket_id}:{run_id}",
            "started_at": "2026-06-01T00:00:00+00:00",
            "updated_at": "2026-06-01T00:00:01+00:00",
            "execution_status": "WAITING_DECISION",
            "iteration": 1,
            "pending_decision": decision,
        },
        "history": [],
        "pending_decision": decision,
        "ticket": ticket,
    }


def test_pause_resume_controller_approve_executes_preapproved_action() -> None:
    with TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        decisions = DecisionStore(base / "decisions")
        checkpoints = CheckpointStore(base / "checkpoints")
        controller = PauseResumeController(decision_store_module=decisions, checkpoint_store_module=checkpoints)

        artifact_path = base / "approved.txt"
        ticket = {
            "ticket_id": "ticket-approve-1",
            "client_context": {"capability_level": "C2_CONTROLADO"},
            "task": {
                "target_file": str(artifact_path),
                "expected_artifact": str(artifact_path),
            },
        }
        decision = {
            "decision_id": "decision-approve-1",
            "run_id": "run-approve-1",
            "ticket_id": "ticket-approve-1",
            "status": "PENDING",
            "action_preview": {
                "action_id": "action-approve-1",
                "type": "file",
                "parameters": {
                    "operation": "delete",
                    "path": str(artifact_path),
                },
                "description": "approved delete",
            },
        }
        artifact_path.write_text("delete-me", encoding="utf-8")
        decisions.save(decision)
        checkpoints.save_checkpoint(_build_checkpoint("run-approve-1", "ticket-approve-1", ticket, decision))

        resumed_ticket = controller.build_resume_ticket(decision_id="decision-approve-1", option="A")
        loop = VictorLoop(max_iterations=2, checkpoint_store_module=checkpoints, pause_resume_controller_module=controller)
        result = loop.run(resumed_ticket)

        assert result["status"] == "RESUELTO"
        assert result["execution_status"] == "COMPLETED"
        assert not artifact_path.exists()


def test_pause_resume_controller_deny_blocks_run() -> None:
    with TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        decisions = DecisionStore(base / "decisions")
        checkpoints = CheckpointStore(base / "checkpoints")
        controller = PauseResumeController(decision_store_module=decisions, checkpoint_store_module=checkpoints)

        ticket = {
            "ticket_id": "ticket-deny-1",
            "client_context": {"capability_level": "C2_CONTROLADO"},
        }
        decision = {
            "decision_id": "decision-deny-1",
            "run_id": "run-deny-1",
            "ticket_id": "ticket-deny-1",
            "status": "PENDING",
            "action_preview": {
                "action_id": "action-deny-1",
                "type": "http",
                "parameters": {"method": "PATCH", "url": "/tickets/ticket-deny-1"},
                "description": "denied close",
            },
        }
        decisions.save(decision)
        checkpoints.save_checkpoint(_build_checkpoint("run-deny-1", "ticket-deny-1", ticket, decision))

        resumed_ticket = controller.build_resume_ticket(decision_id="decision-deny-1", option="B", comment="manual deny")
        loop = VictorLoop(max_iterations=2, checkpoint_store_module=checkpoints, pause_resume_controller_module=controller)
        result = loop.run(resumed_ticket)

        assert result["status"] == "FAILED"
        assert result["execution_status"] == "BLOCKED"
        assert result["failure_response"]["message"] == "manual deny"


def test_pause_resume_controller_pause_keeps_waiting_decision() -> None:
    with TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        decisions = DecisionStore(base / "decisions")
        checkpoints = CheckpointStore(base / "checkpoints")
        controller = PauseResumeController(decision_store_module=decisions, checkpoint_store_module=checkpoints)

        ticket = {
            "ticket_id": "ticket-pause-1",
            "client_context": {"capability_level": "C2_CONTROLADO"},
        }
        decision = {
            "decision_id": "decision-pause-1",
            "run_id": "run-pause-1",
            "ticket_id": "ticket-pause-1",
            "status": "PENDING",
            "action_preview": {
                "action_id": "action-pause-1",
                "type": "http",
                "parameters": {"method": "PATCH", "url": "/tickets/ticket-pause-1"},
                "description": "paused close",
            },
        }
        decisions.save(decision)
        checkpoints.save_checkpoint(_build_checkpoint("run-pause-1", "ticket-pause-1", ticket, decision))

        resumed_ticket = controller.build_resume_ticket(decision_id="decision-pause-1", option="C", comment="need more review")
        loop = VictorLoop(max_iterations=2, checkpoint_store_module=checkpoints, pause_resume_controller_module=controller)
        result = loop.run(resumed_ticket)

        assert result["status"] == "WAITING_DECISION"
        assert result["execution_status"] == "WAITING_DECISION"
        assert (result.get("pending_decision") or {}).get("status") == "PAUSED"


def test_pause_resume_controller_approve_preserves_executor_error_type() -> None:
    with TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        decisions = DecisionStore(base / "decisions")
        checkpoints = CheckpointStore(base / "checkpoints")
        controller = PauseResumeController(decision_store_module=decisions, checkpoint_store_module=checkpoints)

        ticket = {
            "ticket_id": "ticket-approve-error-1",
            "client_context": {"capability_level": "C2_CONTROLADO"},
        }
        decision = {
            "decision_id": "decision-approve-error-1",
            "run_id": "run-approve-error-1",
            "ticket_id": "ticket-approve-error-1",
            "status": "PENDING",
            "action_preview": {
                "action_id": "action-approve-error-1",
                "type": "http",
                "parameters": {"method": "PUT", "url": "https://example.test/tickets/1"},
                "description": "approved close with expired token",
            },
        }
        decisions.save(decision)
        checkpoints.save_checkpoint(_build_checkpoint("run-approve-error-1", "ticket-approve-error-1", ticket, decision))

        class FailingExecutionService:
            @staticmethod
            def execute(action, context=None):
                return {
                    "status": "FAILED",
                    "success": False,
                    "error_type": "TOOL_ERROR",
                    "message": "HTTP Error 401: Unauthorized",
                    "stdout": '{"msg":"Token has expired"}',
                }

        resumed_ticket = controller.build_resume_ticket(decision_id="decision-approve-error-1", option="A")
        loop = VictorLoop(
            max_iterations=2,
            checkpoint_store_module=checkpoints,
            pause_resume_controller_module=controller,
            execution_service_module=FailingExecutionService(),
        )
        result = loop.run(resumed_ticket)

        assert result["status"] == "FAILED"
        assert result["execution_status"] == "FAILED"
        assert result["failure_response"]["error_type"] == "TOOL_ERROR"
        assert "Unauthorized" in result["failure_response"]["message"]
