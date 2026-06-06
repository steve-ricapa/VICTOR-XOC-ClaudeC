"""Checkpoint persistence for crash-safe pause/resume."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHECKPOINT_DIR = PROJECT_ROOT / "runtime" / "state" / "checkpoints"


class CheckpointStore:
    def __init__(self, root_dir: str | Path | None = None) -> None:
        self.root_dir = Path(root_dir) if root_dir is not None else DEFAULT_CHECKPOINT_DIR
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def save_checkpoint(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        run_id = str(normalized.get("run_id") or (normalized.get("run_context") or {}).get("run_id") or "")
        if not run_id:
            raise ValueError("Checkpoint payload requires run_id")
        path = self._path_for_run(run_id)
        path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return {"status": "SAVED", "run_id": run_id, "path": str(path)}

    def load_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        path = self._path_for_run(run_id)
        if not path.exists():
            return None
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return dict(loaded) if isinstance(loaded, Mapping) else None

    def save_run_state(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.save_checkpoint(payload)

    def persist_state(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.save_checkpoint(payload)

    def load_run_state(self, run_id: str) -> dict[str, Any] | None:
        return self.load_checkpoint(run_id)

    def get_run_state(self, run_id: str) -> dict[str, Any] | None:
        return self.load_checkpoint(run_id)

    def _path_for_run(self, run_id: str) -> Path:
        safe_run_id = str(run_id).replace("/", "_").replace("\\", "_")
        return self.root_dir / f"{safe_run_id}.json"


_DEFAULT_STORE = CheckpointStore()


def save_checkpoint(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _DEFAULT_STORE.save_checkpoint(payload)


def load_checkpoint(run_id: str) -> dict[str, Any] | None:
    return _DEFAULT_STORE.load_checkpoint(run_id)


def save_run_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _DEFAULT_STORE.save_run_state(payload)


def load_run_state(run_id: str) -> dict[str, Any] | None:
    return _DEFAULT_STORE.load_run_state(run_id)
