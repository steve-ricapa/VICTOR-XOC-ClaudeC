from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Any


class CapabilityLevel(str, Enum):
    C1_RESTRINGIDO = "C1_RESTRINGIDO"
    C2_CONTROLADO = "C2_CONTROLADO"
    C3_ELEVADO_SUPERVISADO = "C3_ELEVADO_SUPERVISADO"


_LEVEL_ORDER = {
    CapabilityLevel.C1_RESTRINGIDO.value: 1,
    CapabilityLevel.C2_CONTROLADO.value: 2,
    CapabilityLevel.C3_ELEVADO_SUPERVISADO.value: 3,
}


@dataclass(slots=True)
class ToolCapability:
    tool_name: str
    minimum_level: str
    required_permission: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "minimum_level": self.minimum_level,
            "required_permission": self.required_permission,
            "notes": self.notes,
        }


class MCPCapabilityMapper:
    """Maps MCP tools to required capability levels."""

    def __init__(self) -> None:
        self._tool_capabilities: dict[str, ToolCapability] = {}
        self._lock = Lock()

    def register_tool(
        self,
        tool_name: str,
        minimum_level: str,
        *,
        required_permission: str | None = None,
        notes: str = "",
    ) -> ToolCapability:
        normalized_tool = self._normalize_tool(tool_name)
        normalized_level = self._normalize_level(minimum_level)
        mapping = ToolCapability(
            tool_name=normalized_tool,
            minimum_level=normalized_level,
            required_permission=required_permission,
            notes=notes,
        )
        with self._lock:
            self._tool_capabilities[normalized_tool] = mapping
        return mapping

    def get_requirement(self, tool_name: str) -> ToolCapability | None:
        normalized_tool = self._normalize_tool(tool_name)
        with self._lock:
            requirement = self._tool_capabilities.get(normalized_tool)
            return requirement

    def is_allowed_for_level(self, tool_name: str, capability_level: str) -> bool:
        requirement = self.get_requirement(tool_name)
        if requirement is None:
            return False
        candidate_level = self._normalize_level(capability_level)
        return _LEVEL_ORDER[candidate_level] >= _LEVEL_ORDER[requirement.minimum_level]

    def validate_access(self, tool_name: str, capability_level: str) -> dict[str, Any]:
        requirement = self.get_requirement(tool_name)
        if requirement is None:
            return {
                "allowed": False,
                "reason": "Tool capability mapping not found",
                "tool_name": self._normalize_tool(tool_name),
                "required_level": None,
                "candidate_level": self._normalize_level(capability_level),
                "required_permission": None,
            }

        candidate_level = self._normalize_level(capability_level)
        allowed = _LEVEL_ORDER[candidate_level] >= _LEVEL_ORDER[requirement.minimum_level]
        return {
            "allowed": allowed,
            "reason": "Allowed" if allowed else "Insufficient capability level",
            "tool_name": requirement.tool_name,
            "required_level": requirement.minimum_level,
            "candidate_level": candidate_level,
            "required_permission": requirement.required_permission,
        }

    def list_mappings(self) -> list[dict[str, Any]]:
        with self._lock:
            return [mapping.to_dict() for mapping in sorted(self._tool_capabilities.values(), key=lambda item: item.tool_name)]

    @staticmethod
    def _normalize_tool(tool_name: str) -> str:
        normalized = str(tool_name or "").strip()
        if not normalized:
            raise ValueError("tool_name is required")
        return normalized

    @staticmethod
    def _normalize_level(level: str) -> str:
        normalized = str(level or "").upper().strip()
        if normalized not in _LEVEL_ORDER:
            return CapabilityLevel.C1_RESTRINGIDO.value
        return normalized


_DEFAULT_MAPPER = MCPCapabilityMapper()


def register_tool(
    tool_name: str,
    minimum_level: str,
    *,
    required_permission: str | None = None,
    notes: str = "",
) -> ToolCapability:
    return _DEFAULT_MAPPER.register_tool(
        tool_name=tool_name,
        minimum_level=minimum_level,
        required_permission=required_permission,
        notes=notes,
    )


def validate_access(tool_name: str, capability_level: str) -> dict[str, Any]:
    return _DEFAULT_MAPPER.validate_access(tool_name=tool_name, capability_level=capability_level)
