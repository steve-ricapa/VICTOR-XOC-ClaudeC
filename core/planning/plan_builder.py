from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4


@dataclass(slots=True)
class PlanStep:
    step_id: str
    description: str
    action: dict[str, Any]
    depends_on: list[str] = field(default_factory=list)
    status: str = "PENDING"

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "action": dict(self.action),
            "depends_on": list(self.depends_on),
            "status": self.status,
        }


@dataclass(slots=True)
class ExecutionPlan:
    plan_id: str
    ticket_id: str
    created_at: str
    steps: list[PlanStep]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "ticket_id": self.ticket_id,
            "created_at": self.created_at,
            "steps": [step.to_dict() for step in self.steps],
            "metadata": dict(self.metadata),
        }


class PlanBuilder:
    """Builds deterministic action plans from ticket intent."""

    def build_plan(
        self,
        ticket: Any,
        client_context: Mapping[str, Any] | None = None,
        run_context: Mapping[str, Any] | None = None,
        *,
        llm_actions: list[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        ticket_map = self._normalize_mapping(ticket)
        client_map = self._normalize_mapping(client_context)
        run_map = self._normalize_mapping(run_context)

        ticket_id = str(ticket_map.get("ticket_id") or ticket_map.get("id") or "unknown-ticket")
        steps = self._derive_steps(ticket_map, llm_actions)
        plan = ExecutionPlan(
            plan_id=f"plan-{uuid4()}",
            ticket_id=ticket_id,
            created_at=self._now_iso(),
            steps=steps,
            metadata={
                "client_context": client_map,
                "run_context": run_map,
                "source": "plan_builder",
            },
        )
        return plan.to_dict()

    def build(
        self,
        ticket: Any,
        client_context: Mapping[str, Any] | None = None,
        run_context: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self.build_plan(ticket=ticket, client_context=client_context, run_context=run_context, **kwargs)

    def _derive_steps(
        self,
        ticket: Mapping[str, Any],
        llm_actions: list[Mapping[str, Any]] | None,
    ) -> list[PlanStep]:
        explicit_steps = ticket.get("plan_steps")
        if isinstance(explicit_steps, list):
            built = self._steps_from_payload(explicit_steps)
            if built:
                return built

        if llm_actions:
            built = self._steps_from_payload(llm_actions)
            if built:
                return built

        action_payload = ticket.get("proposed_action")
        if isinstance(action_payload, Mapping):
            return self._steps_from_payload([action_payload])

        title = str(ticket.get("title") or ticket.get("summary") or "ticket")
        fallback_action = {
            "type": "file",
            "parameters": {
                "operation": "exists",
                "path": ".",
            },
            "description": f"Collect baseline context for {title}",
        }
        return [
            PlanStep(
                step_id=f"step-{uuid4()}",
                description="Baseline read-only validation step",
                action=fallback_action,
            )
        ]

    def _steps_from_payload(self, payload_steps: list[Any]) -> list[PlanStep]:
        steps: list[PlanStep] = []
        for index, raw_step in enumerate(payload_steps, start=1):
            step_map = self._normalize_mapping(raw_step)
            action = self._extract_action(step_map)
            if not action:
                continue

            description = str(
                step_map.get("description")
                or action.get("description")
                or f"Execute planned step {index}"
            )
            depends_on_raw = step_map.get("depends_on")
            depends_on = [str(item) for item in depends_on_raw] if isinstance(depends_on_raw, list) else []

            step_id = str(step_map.get("step_id") or f"step-{uuid4()}")
            steps.append(
                PlanStep(
                    step_id=step_id,
                    description=description,
                    action=action,
                    depends_on=depends_on,
                )
            )
        return steps

    def _extract_action(self, step_map: Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(step_map.get("action"), Mapping):
            action = dict(step_map["action"])
        else:
            action = dict(step_map)

        if "parameters" not in action:
            params = {
                key: value
                for key, value in action.items()
                if key not in {"step_id", "description", "depends_on", "status"}
            }
            action["parameters"] = params if isinstance(params, dict) else {}

        action_type = str(action.get("type") or action.get("action_type") or "").lower().strip()
        if not action_type:
            if "command" in action or "command" in action.get("parameters", {}):
                action_type = "shell"
            elif "tool" in action or "tool" in action.get("parameters", {}):
                action_type = "mcp"
            elif "url" in action or "url" in action.get("parameters", {}):
                action_type = "http"
            else:
                action_type = "file"
        action["type"] = action_type
        action["action_type"] = action_type
        return action

    @staticmethod
    def _normalize_mapping(value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "__dict__"):
            return {k: v for k, v in vars(value).items() if not k.startswith("_")}
        return {}

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


_DEFAULT_BUILDER = PlanBuilder()


def build_plan(
    ticket: Any,
    client_context: Mapping[str, Any] | None = None,
    run_context: Mapping[str, Any] | None = None,
    *,
    llm_actions: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    return _DEFAULT_BUILDER.build_plan(
        ticket=ticket,
        client_context=client_context,
        run_context=run_context,
        llm_actions=llm_actions,
    )


def build(
    ticket: Any,
    client_context: Mapping[str, Any] | None = None,
    run_context: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return _DEFAULT_BUILDER.build(ticket=ticket, client_context=client_context, run_context=run_context, **kwargs)
