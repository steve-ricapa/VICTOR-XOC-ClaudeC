from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePath
import re
import shlex
from typing import Any, Mapping
from urllib.parse import urlparse


class CapabilityLevel(str, Enum):
    C1_RESTRINGIDO = "C1_RESTRINGIDO"
    C2_CONTROLADO = "C2_CONTROLADO"
    C3_ELEVADO_SUPERVISADO = "C3_ELEVADO_SUPERVISADO"


class PolicyDecision(str, Enum):
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"
    REQUIRES_DECISION = "REQUIRES_DECISION"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(slots=True)
class PolicyResult:
    decision: PolicyDecision
    reason: str
    risk_level: RiskLevel
    required_permission: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "status": self.decision.value,
            "reason": self.reason,
            "risk_level": self.risk_level.value,
            "required_permission": self.required_permission,
            "blocked": self.decision == PolicyDecision.BLOCKED,
            "requires_decision": self.decision == PolicyDecision.REQUIRES_DECISION,
            "details": dict(self.details),
        }


class PolicyEngine:
    """Policy boundary that validates every action before execution."""

    _NETWORK_COMMANDS = {
        "curl",
        "wget",
        "nc",
        "netcat",
        "nmap",
        "ping",
        "telnet",
        "ssh",
        "scp",
        "ftp",
        "tftp",
        "invoke-webrequest",
        "irm",
        "iwr",
    }

    _ADMIN_INDICATORS = {
        "sudo",
        "doas",
        "su",
        "runas",
    }

    _DESTRUCTIVE_COMMANDS = {
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "mkfs",
        "dd",
        "format",
    }

    _UNIVERSAL_BLOCK_PATTERNS = [
        re.compile(r"\bcurl\b[^|\n]*\|\s*(bash|sh|zsh|ksh|pwsh|powershell)\b", re.IGNORECASE),
        re.compile(r"\bwget\b[^|\n]*\|\s*(bash|sh|zsh|ksh|pwsh|powershell)\b", re.IGNORECASE),
        re.compile(r"\bbash\s*<\(\s*(curl|wget)\b", re.IGNORECASE),
        re.compile(r"\brm\s+-rf\s+/(\s|$)", re.IGNORECASE),
        re.compile(r"\b(shutdown|reboot)\b", re.IGNORECASE),
        re.compile(r"\b(Invoke-WebRequest|iwr|irm)\b[^|\n]*\|\s*(iex|Invoke-Expression)\b", re.IGNORECASE),
    ]

    def __init__(
        self,
        *,
        c1_allowlist: set[str] | None = None,
        c2_network_allowlist: set[str] | None = None,
        mcp_allowlist: set[str] | None = None,
    ) -> None:
        self.c1_allowlist = c1_allowlist or {
            "ls",
            "pwd",
            "cat",
            "echo",
            "grep",
            "find",
            "head",
            "tail",
            "wc",
            "python",
            "python3",
        }
        self.c2_network_allowlist = c2_network_allowlist or {"localhost", "127.0.0.1"}
        self.mcp_allowlist = mcp_allowlist or set()

    def validate(self, action: Any, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        ctx = self._normalize_context(context)
        normalized_action = self._normalize_action(action)
        capability_level = self._resolve_capability_level(ctx)
        action_type = self._resolve_action_type(normalized_action)

        command = self._extract_command(normalized_action)
        universal_block = self._check_universal_block_patterns(command)
        if universal_block is not None:
            return universal_block.to_dict()

        if action_type == "shell":
            return self._validate_shell(normalized_action, ctx, capability_level).to_dict()
        if action_type == "http":
            return self._validate_http(normalized_action, ctx, capability_level).to_dict()
        if action_type == "mcp":
            return self._validate_mcp(normalized_action, ctx, capability_level).to_dict()
        if action_type == "file":
            return self._validate_file(normalized_action, ctx, capability_level).to_dict()

        return self._requires_decision(
            reason=f"Unknown action type '{action_type}' requires manual review",
            risk=RiskLevel.MEDIUM,
            permission="UNKNOWN_ACTION_REVIEW",
            details={"action_type": action_type, "capability_level": capability_level.value},
        ).to_dict()

    def _validate_shell(
        self,
        action: Mapping[str, Any],
        context: Mapping[str, Any],
        level: CapabilityLevel,
    ) -> PolicyResult:
        command = self._extract_command(action)
        if not command:
            return self._blocked(
                reason="Shell action missing command",
                risk=RiskLevel.HIGH,
                details={"action": dict(action)},
            )

        tokens = self._tokenize_command(command)
        base_command = self._command_base(tokens)
        lowered_command = command.lower()

        if self._contains_destructive_command(tokens, lowered_command):
            return self._blocked(
                reason="Destructive command is prohibited",
                risk=RiskLevel.CRITICAL,
                details={"command": command},
            )

        if self._contains_admin_indicator(tokens, lowered_command):
            if level == CapabilityLevel.C1_RESTRINGIDO:
                return self._blocked(
                    reason="C1 does not allow administrative commands",
                    risk=RiskLevel.HIGH,
                    details={"command": command, "capability_level": level.value},
                )
            if level == CapabilityLevel.C2_CONTROLADO:
                return self._requires_decision(
                    reason="Administrative command requires approval in C2",
                    risk=RiskLevel.HIGH,
                    permission="ELEVATED_SHELL",
                    details={"command": command, "capability_level": level.value},
                )

        if level == CapabilityLevel.C1_RESTRINGIDO:
            if base_command not in self._c1_allowlist(context):
                return self._blocked(
                    reason="C1 only permits allowlisted shell commands",
                    risk=RiskLevel.HIGH,
                    details={"base_command": base_command, "command": command},
                )
            if self._contains_network_usage(tokens, lowered_command):
                return self._blocked(
                    reason="C1 does not allow network access",
                    risk=RiskLevel.HIGH,
                    details={"command": command},
                )

        if level == CapabilityLevel.C2_CONTROLADO:
            if self._contains_network_usage(tokens, lowered_command):
                hosts = self._extract_hosts_from_command(command)
                allowed_hosts = self._c2_network_allowlist(context)
                if not hosts:
                    return self._requires_decision(
                        reason="Network command requires review in C2",
                        risk=RiskLevel.MEDIUM,
                        permission="NETWORK_EGRESS",
                        details={"command": command},
                    )
                if any(host not in allowed_hosts for host in hosts):
                    return self._requires_decision(
                        reason="Network destination outside C2 allowlist",
                        risk=RiskLevel.HIGH,
                        permission="NETWORK_EGRESS",
                        details={"command": command, "hosts": hosts},
                    )

        if level == CapabilityLevel.C3_ELEVADO_SUPERVISADO and self._contains_network_usage(tokens, lowered_command):
            return self._allowed(
                reason="C3 allows network shell command under audit",
                risk=RiskLevel.HIGH,
                details={"command": command, "capability_level": level.value},
            )

        risk = RiskLevel.MEDIUM if self._contains_network_usage(tokens, lowered_command) else RiskLevel.LOW
        return self._allowed(
            reason="Shell command allowed by policy",
            risk=risk,
            details={"command": command, "base_command": base_command, "capability_level": level.value},
        )

    def _validate_http(
        self,
        action: Mapping[str, Any],
        context: Mapping[str, Any],
        level: CapabilityLevel,
    ) -> PolicyResult:
        method = str(action.get("method") or "GET").upper()
        url = str(action.get("url") or action.get("uri") or action.get("endpoint") or "")
        if not url:
            return self._blocked(
                reason="HTTP action missing url",
                risk=RiskLevel.HIGH,
                details={"action": dict(action)},
            )

        host = self._extract_host(url)

        if level == CapabilityLevel.C1_RESTRINGIDO:
            return self._blocked(
                reason="C1 does not allow network access",
                risk=RiskLevel.HIGH,
                details={"url": url, "method": method},
            )

        if level == CapabilityLevel.C2_CONTROLADO:
            allowed_hosts = self._c2_network_allowlist(context)
            if host and host not in allowed_hosts:
                return self._requires_decision(
                    reason="HTTP host outside C2 allowlist",
                    risk=RiskLevel.HIGH,
                    permission="NETWORK_EGRESS",
                    details={"url": url, "method": method, "host": host},
                )
            if method not in {"GET", "HEAD"}:
                return self._requires_decision(
                    reason="Mutating HTTP methods require approval in C2",
                    risk=RiskLevel.MEDIUM,
                    permission="HTTP_WRITE",
                    details={"url": url, "method": method},
                )

        risk = RiskLevel.HIGH if method in {"DELETE", "PATCH", "PUT", "POST"} else RiskLevel.MEDIUM
        return self._allowed(
            reason="HTTP action allowed by policy",
            risk=risk,
            details={"url": url, "method": method, "capability_level": level.value},
        )

    def _validate_mcp(
        self,
        action: Mapping[str, Any],
        context: Mapping[str, Any],
        level: CapabilityLevel,
    ) -> PolicyResult:
        tool_name = str(action.get("tool") or action.get("tool_name") or action.get("name") or "")
        if not tool_name:
            return self._blocked(
                reason="MCP action missing tool name",
                risk=RiskLevel.HIGH,
                details={"action": dict(action)},
            )

        allowlist = self._mcp_allowlist(context)
        privileged = bool(action.get("privileged") or action.get("requires_admin"))

        if level == CapabilityLevel.C1_RESTRINGIDO and tool_name not in allowlist:
            return self._blocked(
                reason="C1 only allows explicitly allowlisted MCP tools",
                risk=RiskLevel.HIGH,
                details={"tool": tool_name},
            )

        if level == CapabilityLevel.C2_CONTROLADO:
            if tool_name not in allowlist:
                return self._requires_decision(
                    reason="MCP tool not in C2 allowlist",
                    risk=RiskLevel.MEDIUM,
                    permission="MCP_TOOL_USE",
                    details={"tool": tool_name},
                )
            if privileged:
                return self._requires_decision(
                    reason="Privileged MCP action requires approval in C2",
                    risk=RiskLevel.HIGH,
                    permission="MCP_PRIVILEGED",
                    details={"tool": tool_name},
                )

        risk = RiskLevel.HIGH if privileged else RiskLevel.MEDIUM
        return self._allowed(
            reason="MCP action allowed by policy",
            risk=risk,
            details={"tool": tool_name, "capability_level": level.value},
        )

    def _validate_file(
        self,
        action: Mapping[str, Any],
        context: Mapping[str, Any],
        level: CapabilityLevel,
    ) -> PolicyResult:
        operation = str(action.get("operation") or action.get("op") or "read").lower()
        path = str(action.get("path") or action.get("target") or "")

        if not path:
            return self._blocked(
                reason="File action missing path",
                risk=RiskLevel.HIGH,
                details={"action": dict(action)},
            )

        if level == CapabilityLevel.C1_RESTRINGIDO and operation not in {"read", "list", "stat", "exists"}:
            return self._blocked(
                reason="C1 only allows read-only file operations",
                risk=RiskLevel.MEDIUM,
                details={"operation": operation, "path": path},
            )

        if level == CapabilityLevel.C2_CONTROLADO and operation in {"delete", "remove", "chmod", "chown"}:
            return self._requires_decision(
                reason="Sensitive file operation requires approval in C2",
                risk=RiskLevel.HIGH,
                permission="FILE_MUTATION",
                details={"operation": operation, "path": path},
            )

        risk = RiskLevel.HIGH if operation in {"delete", "remove", "move", "rename"} else RiskLevel.LOW
        return self._allowed(
            reason="File action allowed by policy",
            risk=risk,
            details={"operation": operation, "path": path, "capability_level": level.value},
        )

    def _check_universal_block_patterns(self, command: str) -> PolicyResult | None:
        if not command:
            return None
        for pattern in self._UNIVERSAL_BLOCK_PATTERNS:
            if pattern.search(command):
                return self._blocked(
                    reason="Command matches blocked security pattern",
                    risk=RiskLevel.CRITICAL,
                    details={"pattern": pattern.pattern, "command": command},
                )
        return None

    def _resolve_action_type(self, action: Mapping[str, Any]) -> str:
        raw = str(action.get("type") or action.get("action_type") or action.get("kind") or "").strip().lower()
        if raw in {"shell", "command", "bash", "cmd", "terminal"}:
            return "shell"
        if raw in {"file", "filesystem"}:
            return "file"
        if raw in {"http", "https", "web"}:
            return "http"
        if raw in {"mcp", "tool"}:
            return "mcp"

        if "command" in action or "cmd" in action:
            return "shell"
        if "url" in action or "uri" in action:
            return "http"
        if "tool" in action or "tool_name" in action:
            return "mcp"
        if "path" in action:
            return "file"
        return "unknown"

    def _resolve_capability_level(self, context: Mapping[str, Any]) -> CapabilityLevel:
        ticket = context.get("ticket") if isinstance(context.get("ticket"), Mapping) else {}
        ticket_client = ticket.get("client_context") if isinstance(ticket, Mapping) else {}
        candidate = (
            context.get("capability_level")
            or context.get("capability")
            or (context.get("client_context") or {}).get("capability_level")
            or (context.get("client") or {}).get("capability_level")
            or (ticket_client or {}).get("capability_level")
            or CapabilityLevel.C1_RESTRINGIDO.value
        )
        normalized = str(candidate).upper()
        for level in CapabilityLevel:
            if normalized == level.value:
                return level
        return CapabilityLevel.C1_RESTRINGIDO

    @staticmethod
    def _normalize_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
        return dict(context or {})

    @staticmethod
    def _normalize_action(action: Any) -> dict[str, Any]:
        if isinstance(action, Mapping):
            payload = dict(action)
        elif hasattr(action, "__dict__"):
            payload = {k: v for k, v in vars(action).items() if not k.startswith("_")}
        else:
            payload = {"type": "unknown", "raw": action}

        parameters = payload.get("parameters")
        if isinstance(parameters, Mapping):
            for key, value in parameters.items():
                payload.setdefault(str(key), value)

        if "type" not in payload and "action_type" in payload:
            payload["type"] = payload["action_type"]

        return payload

    @staticmethod
    def _extract_command(action: Mapping[str, Any]) -> str:
        command = action.get("command") or action.get("cmd") or action.get("script")
        if isinstance(command, list):
            return " ".join(str(part) for part in command)
        return str(command or "")

    @staticmethod
    def _tokenize_command(command: str) -> list[str]:
        if not command:
            return []
        try:
            return shlex.split(command, posix=True)
        except ValueError:
            return command.split()

    @staticmethod
    def _command_base(tokens: list[str]) -> str:
        if not tokens:
            return ""
        return PurePath(tokens[0]).name.lower()

    def _contains_network_usage(self, tokens: list[str], command: str) -> bool:
        if any(PurePath(token).name.lower() in self._NETWORK_COMMANDS for token in tokens):
            return True
        return bool(re.search(r"https?://", command, re.IGNORECASE))

    def _contains_admin_indicator(self, tokens: list[str], command: str) -> bool:
        token_admin = any(PurePath(token).name.lower() in self._ADMIN_INDICATORS for token in tokens)
        command_admin = "-verb runas" in command or "start-process" in command and "runas" in command
        return token_admin or command_admin

    def _contains_destructive_command(self, tokens: list[str], command: str) -> bool:
        if any(PurePath(token).name.lower() in self._DESTRUCTIVE_COMMANDS for token in tokens):
            return True
        if re.search(r"\brm\s+-rf\s+/(\s|$)", command, re.IGNORECASE):
            return True
        return False

    @staticmethod
    def _extract_hosts_from_command(command: str) -> set[str]:
        matches = re.findall(r"https?://([^/\s:]+)", command, flags=re.IGNORECASE)
        return {host.lower() for host in matches}

    @staticmethod
    def _extract_host(url: str) -> str:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        return host.lower()

    def _c1_allowlist(self, context: Mapping[str, Any]) -> set[str]:
        configured = context.get("c1_allowlist") or (context.get("policy") or {}).get("c1_allowlist")
        if isinstance(configured, list):
            return {str(item).lower() for item in configured}
        if isinstance(configured, set):
            return {str(item).lower() for item in configured}
        return {cmd.lower() for cmd in self.c1_allowlist}

    def _c2_network_allowlist(self, context: Mapping[str, Any]) -> set[str]:
        configured = context.get("network_allowlist") or (context.get("policy") or {}).get("network_allowlist")
        if isinstance(configured, list):
            return {str(item).lower() for item in configured}
        if isinstance(configured, set):
            return {str(item).lower() for item in configured}
        return {host.lower() for host in self.c2_network_allowlist}

    def _mcp_allowlist(self, context: Mapping[str, Any]) -> set[str]:
        configured = context.get("mcp_allowlist") or (context.get("policy") or {}).get("mcp_allowlist")
        if isinstance(configured, list):
            return {str(item) for item in configured}
        if isinstance(configured, set):
            return {str(item) for item in configured}
        return set(self.mcp_allowlist)

    @staticmethod
    def _allowed(reason: str, risk: RiskLevel, details: Mapping[str, Any] | None = None) -> PolicyResult:
        return PolicyResult(
            decision=PolicyDecision.ALLOWED,
            reason=reason,
            risk_level=risk,
            details=dict(details or {}),
        )

    @staticmethod
    def _blocked(reason: str, risk: RiskLevel, details: Mapping[str, Any] | None = None) -> PolicyResult:
        return PolicyResult(
            decision=PolicyDecision.BLOCKED,
            reason=reason,
            risk_level=risk,
            details=dict(details or {}),
        )

    @staticmethod
    def _requires_decision(
        reason: str,
        risk: RiskLevel,
        permission: str,
        details: Mapping[str, Any] | None = None,
    ) -> PolicyResult:
        return PolicyResult(
            decision=PolicyDecision.REQUIRES_DECISION,
            reason=reason,
            risk_level=risk,
            required_permission=permission,
            details=dict(details or {}),
        )


_DEFAULT_ENGINE = PolicyEngine()


def validate(action: Any, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return _DEFAULT_ENGINE.validate(action=action, context=context)
