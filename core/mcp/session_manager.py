from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Mapping
from uuid import uuid4


@dataclass(slots=True)
class MCPSession:
    session_id: str
    run_id: str
    tool_name: str
    created_at: str
    expires_at: str
    last_used_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "tool_name": self.tool_name,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_used_at": self.last_used_at,
            "metadata": dict(self.metadata),
        }


class MCPSessionManager:
    """In-memory MCP session lifecycle manager keyed by run and tool."""

    def __init__(self, *, default_ttl_seconds: int = 1800) -> None:
        self.default_ttl_seconds = max(30, int(default_ttl_seconds))
        self._sessions: dict[tuple[str, str], MCPSession] = {}
        self._credentials: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = Lock()

    def get_or_create_session(
        self,
        *,
        run_id: str,
        tool_name: str,
        credentials: Mapping[str, Any] | None = None,
        ttl_seconds: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MCPSession:
        key = (self._normalize(run_id), self._normalize(tool_name))
        now = self._now()

        with self._lock:
            existing = self._sessions.get(key)
            if existing and not self._is_expired(existing, now):
                existing.last_used_at = now.isoformat()
                if metadata:
                    existing.metadata.update(dict(metadata))
                if credentials is not None:
                    self._credentials[key] = dict(credentials)
                return existing

            ttl = max(30, int(ttl_seconds or self.default_ttl_seconds))
            expires = now + timedelta(seconds=ttl)
            session = MCPSession(
                session_id=f"mcp-session-{uuid4()}",
                run_id=key[0],
                tool_name=key[1],
                created_at=now.isoformat(),
                expires_at=expires.isoformat(),
                last_used_at=now.isoformat(),
                metadata=dict(metadata or {}),
            )
            self._sessions[key] = session
            if credentials is not None:
                self._credentials[key] = dict(credentials)
            return session

    def get_session(self, *, run_id: str, tool_name: str) -> MCPSession | None:
        key = (self._normalize(run_id), self._normalize(tool_name))
        now = self._now()
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                return None
            if self._is_expired(session, now):
                self._sessions.pop(key, None)
                self._credentials.pop(key, None)
                return None
            session.last_used_at = now.isoformat()
            return session

    def get_credentials(self, *, run_id: str, tool_name: str) -> dict[str, Any] | None:
        key = (self._normalize(run_id), self._normalize(tool_name))
        with self._lock:
            creds = self._credentials.get(key)
            return dict(creds) if creds is not None else None

    def invalidate_session(self, *, run_id: str, tool_name: str) -> bool:
        key = (self._normalize(run_id), self._normalize(tool_name))
        with self._lock:
            existed = key in self._sessions
            self._sessions.pop(key, None)
            self._credentials.pop(key, None)
            return existed

    def invalidate_run(self, run_id: str) -> int:
        normalized_run = self._normalize(run_id)
        removed = 0
        with self._lock:
            keys = [key for key in self._sessions.keys() if key[0] == normalized_run]
            for key in keys:
                self._sessions.pop(key, None)
                self._credentials.pop(key, None)
                removed += 1
        return removed

    def purge_expired(self) -> int:
        now = self._now()
        removed = 0
        with self._lock:
            keys = [key for key, session in self._sessions.items() if self._is_expired(session, now)]
            for key in keys:
                self._sessions.pop(key, None)
                self._credentials.pop(key, None)
                removed += 1
        return removed

    @staticmethod
    def _is_expired(session: MCPSession, now: datetime) -> bool:
        try:
            expires = datetime.fromisoformat(session.expires_at)
        except ValueError:
            return True
        return now >= expires

    @staticmethod
    def _normalize(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("Session key value cannot be empty")
        return text

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)


_DEFAULT_SESSION_MANAGER = MCPSessionManager()


def get_or_create_session(
    *,
    run_id: str,
    tool_name: str,
    credentials: Mapping[str, Any] | None = None,
    ttl_seconds: int | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> MCPSession:
    return _DEFAULT_SESSION_MANAGER.get_or_create_session(
        run_id=run_id,
        tool_name=tool_name,
        credentials=credentials,
        ttl_seconds=ttl_seconds,
        metadata=metadata,
    )
