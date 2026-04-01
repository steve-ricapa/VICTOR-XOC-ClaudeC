from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Mapping


ActionExecutor = Callable[[Mapping[str, Any], Mapping[str, Any] | None], Any]


@dataclass(slots=True)
class ActionRegistration:
    action_type: str
    executor_name: str
    handler: ActionExecutor
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "executor_name": self.executor_name,
            "description": self.description,
        }


class ActionRegistry:
    """Maps action types to executor handlers for extensible execution."""

    def __init__(self) -> None:
        self._registry: dict[str, ActionRegistration] = {}
        self._lock = Lock()

    def register(
        self,
        action_type: str,
        handler: ActionExecutor,
        *,
        executor_name: str,
        description: str = "",
    ) -> ActionRegistration:
        normalized_type = self._normalize_action_type(action_type)
        if not callable(handler):
            raise ValueError(f"Handler for action type '{normalized_type}' must be callable")

        registration = ActionRegistration(
            action_type=normalized_type,
            executor_name=str(executor_name),
            handler=handler,
            description=description,
        )
        with self._lock:
            self._registry[normalized_type] = registration
        return registration

    def unregister(self, action_type: str) -> bool:
        normalized_type = self._normalize_action_type(action_type)
        with self._lock:
            existed = normalized_type in self._registry
            self._registry.pop(normalized_type, None)
            return existed

    def resolve(self, action_type: str) -> ActionRegistration:
        normalized_type = self._normalize_action_type(action_type)
        with self._lock:
            registration = self._registry.get(normalized_type)
        if registration is None:
            raise KeyError(f"No executor registered for action type: {normalized_type}")
        return registration

    def list_registered(self) -> list[dict[str, Any]]:
        with self._lock:
            registrations = sorted(self._registry.values(), key=lambda item: item.action_type)
        return [item.to_dict() for item in registrations]

    def execute(
        self,
        action: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> Any:
        action_type = str(action.get("type") or action.get("action_type") or "")
        registration = self.resolve(action_type)
        handler = registration.handler
        ctx = dict(context or {})

        for args, kwargs in (
            ((action, ctx), {}),
            ((action,), {"context": ctx}),
            ((), {"action": action, "context": ctx}),
        ):
            try:
                return handler(*args, **kwargs)
            except TypeError:
                continue
        raise RuntimeError(f"Executor for action type '{registration.action_type}' has unsupported signature")

    @staticmethod
    def _normalize_action_type(action_type: str) -> str:
        normalized = str(action_type or "").strip().lower()
        if not normalized:
            raise ValueError("action_type is required")
        return normalized


_DEFAULT_REGISTRY = ActionRegistry()


def register(
    action_type: str,
    handler: ActionExecutor,
    *,
    executor_name: str,
    description: str = "",
) -> ActionRegistration:
    return _DEFAULT_REGISTRY.register(
        action_type=action_type,
        handler=handler,
        executor_name=executor_name,
        description=description,
    )


def resolve(action_type: str) -> ActionRegistration:
    return _DEFAULT_REGISTRY.resolve(action_type)
