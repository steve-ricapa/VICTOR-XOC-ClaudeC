from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import os
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any, Mapping, Sequence


class CommandErrorType(str, Enum):
    TOOL_ERROR = "TOOL_ERROR"
    TIMEOUT = "TIMEOUT"
    ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class CommandExecutionResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    duration_seconds: float
    command: str
    argv: list[str]
    started_at: str
    finished_at: str
    status: str
    error_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "command": self.command,
            "argv": list(self.argv),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "error_type": self.error_type,
        }


class CommandExecutor:
    """Safe shell command execution with timeout and structured output."""

    _BLOCKED_PATTERNS = [
        re.compile(r"\bcurl\b[^|\n]*\|\s*(bash|sh|zsh|ksh|pwsh|powershell)\b", re.IGNORECASE),
        re.compile(r"\bwget\b[^|\n]*\|\s*(bash|sh|zsh|ksh|pwsh|powershell)\b", re.IGNORECASE),
        re.compile(r"\bbash\s*<\(\s*(curl|wget)\b", re.IGNORECASE),
        re.compile(r"\brm\s+-rf\s+/(\s|$)", re.IGNORECASE),
        re.compile(r"\b(shutdown|reboot)\b", re.IGNORECASE),
    ]

    def __init__(
        self,
        *,
        default_timeout_seconds: int = 30,
        max_output_chars: int = 50_000,
    ) -> None:
        self.default_timeout_seconds = max(1, int(default_timeout_seconds))
        self.max_output_chars = max(1_024, int(max_output_chars))

    def execute(
        self,
        command: str | Sequence[str],
        *,
        timeout_seconds: int | float | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        started_at_dt = self._now()
        started_at = self._ts(started_at_dt)

        try:
            argv = self._normalize_command(command)
        except Exception as exc:
            return self._failure_result(
                command=self._command_to_string(command),
                argv=[],
                stdout="",
                stderr=f"Invalid command input: {exc}",
                error_type=CommandErrorType.TOOL_ERROR,
                started_at=started_at,
                finished_at=self._ts(self._now()),
                duration_seconds=(self._now() - started_at_dt).total_seconds(),
                exit_code=None,
            )

        command_text = self._command_to_string(argv)
        blocked_reason = self._blocked_reason(command_text)
        if blocked_reason is not None:
            return self._failure_result(
                command=command_text,
                argv=argv,
                stdout="",
                stderr=blocked_reason,
                error_type=CommandErrorType.TOOL_ERROR,
                started_at=started_at,
                finished_at=self._ts(self._now()),
                duration_seconds=(self._now() - started_at_dt).total_seconds(),
                exit_code=None,
            )

        timeout = float(timeout_seconds) if timeout_seconds is not None else float(self.default_timeout_seconds)
        if timeout <= 0:
            timeout = float(self.default_timeout_seconds)

        resolved_cwd = self._resolve_cwd(cwd)
        safe_env = self._safe_env(env)

        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=resolved_cwd,
                env=safe_env,
                shell=False,
                check=False,
            )
            finished_at_dt = self._now()
            result = CommandExecutionResult(
                success=completed.returncode == 0,
                stdout=self._truncate(completed.stdout or ""),
                stderr=self._truncate(completed.stderr or ""),
                exit_code=int(completed.returncode),
                duration_seconds=(finished_at_dt - started_at_dt).total_seconds(),
                command=command_text,
                argv=argv,
                started_at=started_at,
                finished_at=self._ts(finished_at_dt),
                status="COMPLETED" if completed.returncode == 0 else "FAILED",
                error_type=None if completed.returncode == 0 else CommandErrorType.TOOL_ERROR.value,
            )
            return result.to_dict()

        except subprocess.TimeoutExpired as exc:
            finished_at_dt = self._now()
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            return self._failure_result(
                command=command_text,
                argv=argv,
                stdout=self._truncate(stdout),
                stderr=self._truncate(stderr or f"Command timed out after {timeout:.2f}s"),
                error_type=CommandErrorType.TIMEOUT,
                started_at=started_at,
                finished_at=self._ts(finished_at_dt),
                duration_seconds=(finished_at_dt - started_at_dt).total_seconds(),
                exit_code=None,
            )

        except OSError as exc:
            finished_at_dt = self._now()
            return self._failure_result(
                command=command_text,
                argv=argv,
                stdout="",
                stderr=self._truncate(str(exc)),
                error_type=CommandErrorType.ENVIRONMENT_ERROR,
                started_at=started_at,
                finished_at=self._ts(finished_at_dt),
                duration_seconds=(finished_at_dt - started_at_dt).total_seconds(),
                exit_code=None,
            )

        except Exception as exc:
            finished_at_dt = self._now()
            return self._failure_result(
                command=command_text,
                argv=argv,
                stdout="",
                stderr=self._truncate(str(exc)),
                error_type=CommandErrorType.UNKNOWN,
                started_at=started_at,
                finished_at=self._ts(finished_at_dt),
                duration_seconds=(finished_at_dt - started_at_dt).total_seconds(),
                exit_code=None,
            )

    def _normalize_command(self, command: str | Sequence[str]) -> list[str]:
        if isinstance(command, str):
            stripped = command.strip()
            if not stripped:
                raise ValueError("Command cannot be empty")
            try:
                argv = shlex.split(stripped, posix=os.name != "nt")
            except ValueError:
                argv = stripped.split()
        else:
            argv = [str(part) for part in command]

        if not argv:
            raise ValueError("Command arguments cannot be empty")
        return argv

    def _blocked_reason(self, command_text: str) -> str | None:
        for pattern in self._BLOCKED_PATTERNS:
            if pattern.search(command_text):
                return "Command blocked by executor safety rule"
        return None

    @staticmethod
    def _resolve_cwd(cwd: str | None) -> str | None:
        if cwd is None:
            return None
        resolved = str(Path(cwd).resolve())
        if not Path(resolved).exists():
            raise OSError(f"Working directory does not exist: {resolved}")
        return resolved

    @staticmethod
    def _safe_env(env: Mapping[str, str] | None) -> dict[str, str] | None:
        if env is None:
            return None
        merged = dict(os.environ)
        merged.update({str(k): str(v) for k, v in env.items()})
        return merged

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_output_chars:
            return text
        return text[: self.max_output_chars] + "\n...[truncated]"

    def _failure_result(
        self,
        *,
        command: str,
        argv: list[str],
        stdout: str,
        stderr: str,
        error_type: CommandErrorType,
        started_at: str,
        finished_at: str,
        duration_seconds: float,
        exit_code: int | None,
    ) -> dict[str, Any]:
        return CommandExecutionResult(
            success=False,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
            command=command,
            argv=argv,
            started_at=started_at,
            finished_at=finished_at,
            status="FAILED" if error_type != CommandErrorType.TIMEOUT else "TIMEOUT",
            error_type=error_type.value,
        ).to_dict()

    @staticmethod
    def _command_to_string(command: str | Sequence[str]) -> str:
        if isinstance(command, str):
            return command
        return " ".join(str(part) for part in command)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _ts(value: datetime) -> str:
        return value.isoformat()


_DEFAULT_EXECUTOR = CommandExecutor()


def execute(
    command: str | Sequence[str],
    *,
    timeout_seconds: int | float | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return _DEFAULT_EXECUTOR.execute(
        command=command,
        timeout_seconds=timeout_seconds,
        cwd=cwd,
        env=env,
    )
