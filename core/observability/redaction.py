from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping


DEFAULT_SENSITIVE_FIELDS = {
    "token",
    "api_key",
    "apikey",
    "secret",
    "password",
    "passwd",
    "authorization",
    "auth",
    "access_token",
    "refresh_token",
    "credentials",
    "credential",
    "private_key",
}


@dataclass(slots=True)
class RedactionConfig:
    enabled: bool = True
    redact_paths: bool = True
    sensitive_fields: set[str] = field(default_factory=lambda: set(DEFAULT_SENSITIVE_FIELDS))
    custom_patterns: list[str] = field(default_factory=list)


class Redactor:
    _BUILTIN_PATTERNS = [
        re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
        re.compile(
            r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|access[_-]?token|refresh[_-]?token)\s*[:=]\s*['\"]?[^'\"\s]+"
        ),
        re.compile(r"AKIA[0-9A-Z]{16}"),
    ]

    _PATH_PATTERN = re.compile(
        r"(?:[A-Za-z]:\\[^\s\"']+|/(?:Users|home|etc|var|opt|root|srv|tmp)[^\s\"']*)"
    )

    def __init__(self, config: RedactionConfig | None = None) -> None:
        self.config = config or RedactionConfig()
        self._custom_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in self.config.custom_patterns]

    def redact_text(self, value: str) -> str:
        if not self.config.enabled:
            return value

        redacted = value
        for pattern in self._BUILTIN_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        for pattern in self._custom_patterns:
            redacted = pattern.sub("[REDACTED]", redacted)

        if self.config.redact_paths:
            redacted = self._PATH_PATTERN.sub("[REDACTED_PATH]", redacted)
        return redacted

    def redact_data(self, value: Any) -> Any:
        if not self.config.enabled:
            return value

        if isinstance(value, str):
            return self.redact_text(value)

        if isinstance(value, Mapping):
            output: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if key_text.lower() in self.config.sensitive_fields:
                    output[key_text] = "[REDACTED]"
                else:
                    output[key_text] = self.redact_data(item)
            return output

        if isinstance(value, list):
            return [self.redact_data(item) for item in value]

        if isinstance(value, tuple):
            return tuple(self.redact_data(item) for item in value)

        if isinstance(value, set):
            return {self.redact_data(item) for item in value}

        return value


_DEFAULT_REDACTOR = Redactor()


def redact_text(value: str) -> str:
    return _DEFAULT_REDACTOR.redact_text(value)


def redact_data(value: Any) -> Any:
    return _DEFAULT_REDACTOR.redact_data(value)
