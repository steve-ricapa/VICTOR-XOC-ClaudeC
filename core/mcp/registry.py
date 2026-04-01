from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable


MCPAdapter = Callable[..., Any]


@dataclass(slots=True)
class MCPToolRegistration:
    name: str
    adapter: MCPAdapter
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class MCPRegistry:
    """Deny-by-default MCP tool registry."""

    def __init__(
        self,
        *,
        allowlist: set[str] | None = None,
        deny_by_default: bool = True,
    ) -> None:
        self.deny_by_default = deny_by_default
        self._tools: dict[str, MCPToolRegistration] = {}
        self._allowlist: set[str] = {item.strip() for item in (allowlist or set()) if item.strip()}
        self._lock = Lock()

    def register_tool(
        self,
        name: str,
        adapter: MCPAdapter,
        *,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> MCPToolRegistration:
        normalized = self._normalize_name(name)
        if not callable(adapter):
            raise ValueError(f"Adapter for MCP tool '{normalized}' must be callable")

        registration = MCPToolRegistration(
            name=normalized,
            adapter=adapter,
            description=description,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._tools[normalized] = registration
        return registration

    def unregister_tool(self, name: str) -> bool:
        normalized = self._normalize_name(name)
        with self._lock:
            existed = normalized in self._tools
            self._tools.pop(normalized, None)
            return existed

    def set_allowlist(self, allowlist: set[str] | list[str]) -> None:
        normalized = {self._normalize_name(item) for item in allowlist if str(item).strip()}
        with self._lock:
            self._allowlist = normalized

    def allow_tool(self, name: str) -> None:
        normalized = self._normalize_name(name)
        with self._lock:
            self._allowlist.add(normalized)

    def disallow_tool(self, name: str) -> None:
        normalized = self._normalize_name(name)
        with self._lock:
            self._allowlist.discard(normalized)

    def list_tools(self) -> list[str]:
        with self._lock:
            return sorted(self._tools.keys())

    def list_allowed_tools(self) -> list[str]:
        with self._lock:
            if not self.deny_by_default:
                return sorted(self._tools.keys())
            return sorted(name for name in self._tools.keys() if name in self._allowlist)

    def tool_exists(self, name: str) -> bool:
        normalized = self._normalize_name(name)
        with self._lock:
            return normalized in self._tools

    def is_allowed(self, name: str) -> bool:
        normalized = self._normalize_name(name)
        with self._lock:
            if normalized not in self._tools:
                return False
            if not self.deny_by_default:
                return True
            return normalized in self._allowlist

    def validate_tool(self, name: str) -> None:
        normalized = self._normalize_name(name)
        with self._lock:
            if normalized not in self._tools:
                raise KeyError(f"MCP tool not registered: {normalized}")
            if self.deny_by_default and normalized not in self._allowlist:
                raise PermissionError(f"MCP tool not allowlisted: {normalized}")

    def get_adapter(self, name: str) -> MCPAdapter:
        normalized = self._normalize_name(name)
        self.validate_tool(normalized)
        with self._lock:
            return self._tools[normalized].adapter

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized = str(name or "").strip()
        if not normalized:
            raise ValueError("Tool name cannot be empty")
        return normalized


_DEFAULT_REGISTRY = MCPRegistry()


def register_tool(
    name: str,
    adapter: MCPAdapter,
    *,
    description: str = "",
    metadata: dict[str, Any] | None = None,
) -> MCPToolRegistration:
    return _DEFAULT_REGISTRY.register_tool(name, adapter, description=description, metadata=metadata)


def get_adapter(name: str) -> MCPAdapter:
    return _DEFAULT_REGISTRY.get_adapter(name)


def validate_tool(name: str) -> None:
    _DEFAULT_REGISTRY.validate_tool(name)
