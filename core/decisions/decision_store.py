from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DECISION_DIR = PROJECT_ROOT / "runtime" / "state" / "decisions"


class DecisionStore:
    def __init__(self, root_dir: str | Path | None = None) -> None:
        self.root_dir = Path(root_dir) if root_dir is not None else DEFAULT_DECISION_DIR
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def save(self, decision: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(decision)
        decision_id = str(payload.get("decision_id") or "")
        if not decision_id:
            raise ValueError("Decision payload requires decision_id")
        path = self._path_for_decision(decision_id)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return {"status": "SAVED", "decision_id": decision_id, "path": str(path)}

    def create(self, decision: Mapping[str, Any]) -> dict[str, Any]:
        return self.save(decision)

    def upsert(self, decision: Mapping[str, Any]) -> dict[str, Any]:
        return self.save(decision)

    def store(self, decision: Mapping[str, Any]) -> dict[str, Any]:
        return self.save(decision)

    def add(self, decision: Mapping[str, Any]) -> dict[str, Any]:
        return self.save(decision)

    def load(self, decision_id: str) -> dict[str, Any] | None:
        path = self._path_for_decision(decision_id)
        if not path.exists():
            return None
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return dict(loaded) if isinstance(loaded, Mapping) else None

    def get(self, decision_id: str) -> dict[str, Any] | None:
        return self.load(decision_id)

    def respond(
        self,
        decision_id: str,
        *,
        option: str,
        actor: str | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        payload = self.load(decision_id)
        if payload is None:
            raise FileNotFoundError(f"Decision not found: {decision_id}")

        selected_option = str(option).strip().upper()
        status_map = {"A": "APPROVED", "B": "DENIED", "C": "PAUSED"}
        payload["response"] = {
            "selected_option": selected_option,
            "status": status_map.get(selected_option, "UNKNOWN"),
            "actor": actor,
            "comment": comment,
        }
        payload["status"] = str(payload["response"]["status"])
        self.save(payload)
        return payload

    def _path_for_decision(self, decision_id: str) -> Path:
        safe_decision_id = str(decision_id).replace("/", "_").replace("\\", "_")
        return self.root_dir / f"{safe_decision_id}.json"


_DEFAULT_STORE = DecisionStore()


def save(decision: Mapping[str, Any]) -> dict[str, Any]:
    return _DEFAULT_STORE.save(decision)


def load(decision_id: str) -> dict[str, Any] | None:
    return _DEFAULT_STORE.load(decision_id)


def respond(
    decision_id: str,
    *,
    option: str,
    actor: str | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    return _DEFAULT_STORE.respond(decision_id, option=option, actor=actor, comment=comment)
