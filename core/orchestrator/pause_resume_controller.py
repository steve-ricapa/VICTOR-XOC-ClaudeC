"""Pause and resume coordination for human-in-the-loop decisions."""

from __future__ import annotations

from typing import Any, Mapping

from core.decisions import decision_store
from core.state import checkpoint_store


class PauseResumeController:
    def __init__(
        self,
        *,
        decision_store_module: Any = decision_store,
        checkpoint_store_module: Any = checkpoint_store,
    ) -> None:
        self.decision_store = decision_store_module
        self.checkpoint_store = checkpoint_store_module

    def build_resume_ticket(
        self,
        *,
        decision_id: str,
        option: str,
        actor: str | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        decision = self._respond_decision(decision_id=decision_id, option=option, actor=actor, comment=comment)
        run_id = str(decision.get("run_id") or "")
        checkpoint = self._load_checkpoint(run_id)
        if not checkpoint:
            raise FileNotFoundError(f"Checkpoint not found for run_id: {run_id}")

        ticket = checkpoint.get("ticket")
        if not isinstance(ticket, Mapping):
            raise ValueError("Checkpoint is missing ticket payload")

        resume_payload = {
            "decision": decision,
            "checkpoint": checkpoint,
        }
        resumed_ticket = dict(ticket)
        resumed_ticket["_resume"] = resume_payload
        return resumed_ticket

    def _respond_decision(
        self,
        *,
        decision_id: str,
        option: str,
        actor: str | None,
        comment: str | None,
    ) -> dict[str, Any]:
        responder = getattr(self.decision_store, "respond", None)
        if callable(responder):
            return dict(responder(decision_id, option=option, actor=actor, comment=comment))
        raise RuntimeError("decision_store.respond is required")

    def _load_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        for method_name in ("load_checkpoint", "load_run_state", "get_run_state"):
            method = getattr(self.checkpoint_store, method_name, None)
            if callable(method):
                result = method(run_id)
                if isinstance(result, Mapping):
                    return dict(result)
        return None


_DEFAULT_CONTROLLER = PauseResumeController()


def build_resume_ticket(
    *,
    decision_id: str,
    option: str,
    actor: str | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    return _DEFAULT_CONTROLLER.build_resume_ticket(
        decision_id=decision_id,
        option=option,
        actor=actor,
        comment=comment,
    )
