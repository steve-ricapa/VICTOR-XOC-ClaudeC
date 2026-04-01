from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping


PROMPTS_ROOT = Path(__file__).resolve().parent.parent
BASE_PROMPTS_DIR = PROMPTS_ROOT / "base"
TEMPLATES_DIR = PROMPTS_ROOT / "templates"


class _SafeFormatDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "N/D"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def load_base_prompts() -> dict[str, str]:
    return {
        "system": _read_text(BASE_PROMPTS_DIR / "system.txt"),
        "rules": _read_text(BASE_PROMPTS_DIR / "rules.txt"),
        "execution_loop": _read_text(BASE_PROMPTS_DIR / "execution_loop.txt"),
    }


def get_base_prompts(*_args: Any, **_kwargs: Any) -> dict[str, str]:
    return dict(load_base_prompts())


def base_prompts(*_args: Any, **_kwargs: Any) -> dict[str, str]:
    return dict(load_base_prompts())


def _normalize_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "__dict__"):
        return {k: v for k, v in vars(value).items() if not k.startswith("_")}
    return {}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, default=str)


def _render_ticket_context(ticket: Mapping[str, Any]) -> str:
    template = _read_text(TEMPLATES_DIR / "ticket_context.txt")
    payload = _SafeFormatDict(
        {
            "ticket_id": ticket.get("ticket_id") or ticket.get("id") or "ticket-desconocido",
            "title": ticket.get("title") or "N/D",
            "priority": ticket.get("priority") or "N/D",
            "status": ticket.get("status") or "N/D",
            "ticket_json": _json(ticket),
        }
    )
    return template.format_map(payload)


def _render_client_context(client_context: Mapping[str, Any]) -> str:
    template = _read_text(TEMPLATES_DIR / "client_context.txt")
    payload = _SafeFormatDict(
        {
            "client_id": client_context.get("client_id") or client_context.get("id") or "cliente-desconocido",
            "capability_level": client_context.get("capability_level") or "C1_RESTRINGIDO",
            "environment": client_context.get("environment") or "on-prem",
            "client_json": _json(client_context),
        }
    )
    return template.format_map(payload)


def _normalize_build_inputs(
    ticket: Any,
    client_context: Any,
    run_context: Any,
    *,
    base_prompts_payload: Any,
    ticket_context: Any,
    history: Any,
) -> tuple[dict[str, str], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    inferred_ticket = ticket
    inferred_client = client_context
    inferred_run = run_context
    inferred_base = base_prompts_payload
    inferred_ticket_context = ticket_context
    inferred_history = history

    if (
        isinstance(ticket, Mapping)
        and client_context is None
        and run_context is None
        and any(key in ticket for key in ("ticket_context", "client_context", "run_context", "base_prompts"))
    ):
        inferred_base = ticket.get("base_prompts")
        inferred_ticket_context = ticket.get("ticket_context")
        inferred_client = ticket.get("client_context")
        inferred_run = ticket.get("run_context")
        inferred_history = ticket.get("history")
        inferred_ticket = None

    if not isinstance(inferred_base, Mapping):
        base_prompts_dict = load_base_prompts()
    else:
        base_prompts_dict = {
            "system": str(inferred_base.get("system") or load_base_prompts()["system"]),
            "rules": str(inferred_base.get("rules") or load_base_prompts()["rules"]),
            "execution_loop": str(
                inferred_base.get("execution_loop") or load_base_prompts()["execution_loop"]
            ),
        }

    ticket_map = _normalize_mapping(inferred_ticket_context if inferred_ticket_context is not None else inferred_ticket)
    client_map = _normalize_mapping(inferred_client)
    run_map = _normalize_mapping(inferred_run)

    if not client_map and isinstance(ticket_map.get("client_context"), Mapping):
        client_map = dict(ticket_map["client_context"])

    history_list: list[dict[str, Any]] = []
    if isinstance(inferred_history, list):
        history_list = [dict(item) for item in inferred_history if isinstance(item, Mapping)]

    return base_prompts_dict, ticket_map, client_map, run_map, history_list


def build_prompt(
    ticket: Any = None,
    client_context: Mapping[str, Any] | None = None,
    run_context: Mapping[str, Any] | None = None,
    *,
    base_prompts: Mapping[str, str] | None = None,
    ticket_context: Mapping[str, Any] | None = None,
    history: list[Mapping[str, Any]] | None = None,
) -> str:
    base, ticket_map, client_map, run_map, history_list = _normalize_build_inputs(
        ticket,
        client_context,
        run_context,
        base_prompts_payload=base_prompts,
        ticket_context=ticket_context,
        history=history,
    )

    sections = [
        "[SISTEMA]\n" + base["system"],
        "[REGLAS]\n" + base["rules"],
        "[CICLO_EJECUCION]\n" + base["execution_loop"],
        "[CONTEXTO_TICKET]\n" + _render_ticket_context(ticket_map),
        "[CONTEXTO_CLIENTE]\n" + _render_client_context(client_map),
        "[CONTEXTO_EJECUCION]\n" + _json(run_map),
        "[HISTORIAL]\n" + _json(history_list),
        (
            "[CONTRATO_SALIDA]\n"
            "Devuelve exactamente un objeto JSON con las claves: action_type, parameters, description, confidence.\n"
            "No incluyas markdown, explicaciones ni texto extra."
        ),
    ]
    return "\n\n".join(section.strip() for section in sections if section.strip())


def build(*args: Any, **kwargs: Any) -> str:
    return build_prompt(*args, **kwargs)


def compile_prompt(*args: Any, **kwargs: Any) -> str:
    return build_prompt(*args, **kwargs)


def create_prompt(*args: Any, **kwargs: Any) -> str:
    return build_prompt(*args, **kwargs)


class PromptBuilder:
    def load_base_prompts(self) -> dict[str, str]:
        return load_base_prompts()

    def get_base_prompts(self) -> dict[str, str]:
        return get_base_prompts()

    def build_prompt(
        self,
        ticket: Any = None,
        client_context: Mapping[str, Any] | None = None,
        run_context: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        return build_prompt(
            ticket=ticket,
            client_context=client_context,
            run_context=run_context,
            **kwargs,
        )

    def build(self, *args: Any, **kwargs: Any) -> str:
        return build_prompt(*args, **kwargs)

    def compile_prompt(self, *args: Any, **kwargs: Any) -> str:
        return build_prompt(*args, **kwargs)

    def create_prompt(self, *args: Any, **kwargs: Any) -> str:
        return build_prompt(*args, **kwargs)


_DEFAULT_BUILDER = PromptBuilder()
