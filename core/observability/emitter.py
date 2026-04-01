from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping


ExternalEmitter = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class EmitterConfig:
    runtime_dir: str | None = None
    file_name: str = "events.jsonl"
    enable_local_file: bool = True


class EventEmitter:
    """Emits events to local runtime audit storage and optional external sinks."""

    def __init__(
        self,
        *,
        config: EmitterConfig | None = None,
        external_emitters: list[ExternalEmitter] | None = None,
    ) -> None:
        self.config = config or EmitterConfig()
        self.external_emitters: list[ExternalEmitter] = list(external_emitters or [])
        self._lock = Lock()
        self._local_path = self._resolve_local_path(self.config)
        self._failed_external_emits: list[dict[str, Any]] = []

    def emit(self, event: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(event)

        if self.config.enable_local_file and self._local_path is not None:
            self._write_local(payload)

        if self.external_emitters:
            for sink in self.external_emitters:
                try:
                    sink(dict(payload))
                except Exception as exc:
                    self._failed_external_emits.append(
                        {
                            "error": str(exc),
                            "event": payload,
                            "sink": repr(sink),
                        }
                    )

        return payload

    def add_external_emitter(self, sink: ExternalEmitter) -> None:
        self.external_emitters.append(sink)

    def _write_local(self, payload: Mapping[str, Any]) -> None:
        if self._local_path is None:
            return

        line = json.dumps(payload, ensure_ascii=True, default=str)
        with self._lock:
            self._local_path.parent.mkdir(parents=True, exist_ok=True)
            with self._local_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    @staticmethod
    def _resolve_local_path(config: EmitterConfig) -> Path | None:
        if not config.enable_local_file:
            return None

        if config.runtime_dir:
            base = Path(config.runtime_dir)
        else:
            base = Path.cwd() / "runtime"

        return (base / "audit" / config.file_name).resolve()


_DEFAULT_EMITTER = EventEmitter()


def emit(event: Mapping[str, Any]) -> dict[str, Any]:
    return _DEFAULT_EMITTER.emit(event)


def add_external_emitter(sink: ExternalEmitter) -> None:
    _DEFAULT_EMITTER.add_external_emitter(sink)
