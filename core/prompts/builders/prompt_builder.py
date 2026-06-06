from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping

import yaml


PROMPTS_ROOT = Path(__file__).resolve().parent.parent
BASE_PROMPTS_DIR = PROMPTS_ROOT / "base"
TEMPLATES_DIR = PROMPTS_ROOT / "templates"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = PROJECT_ROOT / "skills"


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


@lru_cache(maxsize=1)
def load_skills_catalog() -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    if not SKILLS_DIR.exists():
        return skills

    for path in sorted(SKILLS_DIR.glob("*.yaml")):
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(loaded, Mapping):
            continue
        skill = dict(loaded)
        skill["_path"] = str(path.relative_to(PROJECT_ROOT))
        skills.append(skill)
    return skills


def get_skills_catalog(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
    return [dict(skill) for skill in load_skills_catalog()]


def _stringify_ticket_for_matching(ticket: Mapping[str, Any]) -> str:
    return _json(ticket).lower()


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _trigger_matches_haystack(trigger: str, haystack: str) -> bool:
    normalized_trigger = trigger.strip().lower()
    if not normalized_trigger:
        return False
    if normalized_trigger in haystack:
        return True

    words = [word for word in normalized_trigger.replace("-", " ").split() if len(word) >= 4]
    if not words:
        return False

    matched_words = sum(1 for word in words if word in haystack)
    if len(words) == 1:
        return matched_words == 1
    return matched_words >= min(2, len(words))


def _skill_matches_domain(skill: Mapping[str, Any], haystack: str) -> bool:
    skill_name = str(skill.get("name") or "").lower()
    domain_keywords: dict[str, tuple[str, ...]] = {
        "network-security-investigation": (
            "http",
            "https",
            "conectividad",
            "red",
            "host externo",
            "internet",
            "egreso",
            "url",
        ),
        "secure-file-remediation": (
            "archivo",
            "contenido",
            "configuracion",
            "path",
            "ruta",
            "parche",
            "write",
            "sobrescribir",
        ),
        "vulnerability-triage": (
            "vulnerabilidad",
            "cve",
            "hallazgo",
            "severidad",
            "incidente",
            "evidencia",
        ),
        "human-escalation": (
            "aprobacion",
            "approve",
            "deny",
            "pause",
            "escalar",
            "revision humana",
            "pending_decision",
        ),
    }

    for domain_name, keywords in domain_keywords.items():
        if skill_name != domain_name:
            continue
        return any(keyword in haystack for keyword in keywords)
    return False


def _select_skills(ticket: Mapping[str, Any]) -> list[dict[str, Any]]:
    catalog = load_skills_catalog()
    if not catalog:
        return []

    requested_raw = ticket.get("skills") or ticket.get("requested_skills")
    requested = {item.lower() for item in _normalize_string_list(requested_raw)}
    selected: list[dict[str, Any]] = []

    if requested:
        for skill in catalog:
            identifiers = {
                str(skill.get("name") or "").lower(),
                str(skill.get("_path") or "").lower(),
                Path(str(skill.get("_path") or "")).stem.lower(),
            }
            if requested & identifiers:
                selected.append(skill)
        return selected

    haystack = _stringify_ticket_for_matching(ticket)
    for skill in catalog:
        triggers = [trigger.lower() for trigger in _normalize_string_list(skill.get("triggers"))]
        if triggers and any(_trigger_matches_haystack(trigger, haystack) for trigger in triggers):
            selected.append(skill)
            continue
        if _skill_matches_domain(skill, haystack):
            selected.append(skill)

    return selected


def _render_skill(skill: Mapping[str, Any]) -> str:
    lines = [
        f"- name: {skill.get('name') or 'sin-nombre'}",
        f"  description: {skill.get('description') or 'N/D'}",
    ]

    triggers = _normalize_string_list(skill.get("triggers"))
    if triggers:
        lines.append("  triggers:")
        for trigger in triggers:
            lines.append(f"    - {trigger}")

    actions = skill.get("recommended_actions")
    if isinstance(actions, list) and actions:
        lines.append("  recommended_actions:")
        for action in actions:
            if not isinstance(action, Mapping):
                continue
            lines.append(
                f"    - type: {action.get('type') or action.get('action_type') or 'unknown'}"
            )
            lines.append(f"      description: {action.get('description') or 'N/D'}")
            params = action.get("parameters") if isinstance(action.get("parameters"), Mapping) else {}
            if params:
                lines.append("      parameters:")
                for key, value in dict(params).items():
                    rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=True)
                    lines.append(f"        {key}: {rendered}")

    for field_name in ("safety_notes", "verification_expectations", "escalation_criteria"):
        items = _normalize_string_list(skill.get(field_name))
        if not items:
            continue
        lines.append(f"  {field_name}:")
        for item in items:
            lines.append(f"    - {item}")

    path_value = skill.get("_path")
    if path_value:
        lines.append(f"  source: {path_value}")

    return "\n".join(lines)


def _render_selected_skills(ticket: Mapping[str, Any]) -> str:
    skills = _select_skills(ticket)
    if not skills:
        return "Sin skills aplicables detectadas para este ticket."
    return "\n\n".join(_render_skill(skill) for skill in skills)


def _render_completion_guidance(ticket: Mapping[str, Any]) -> str:
    task = ticket.get("task")
    if not isinstance(task, Mapping):
        return "Si el ticket queda efectivamente resuelto, evita pasos extra de auditoria que no cambien la decision final."

    artifact = task.get("expected_artifact")
    content = task.get("expected_content")
    acceptance_criteria = task.get("acceptance_criteria")
    criteria: list[str] = []

    if artifact:
        criteria.append(f"- Considera el objetivo principal cumplido cuando el artefacto `{artifact}` exista.")
    if content:
        criteria.append(f"- Si ademas verificas que el contenido esperado es `{content}`, da por completada la remediacion principal.")
    if isinstance(acceptance_criteria, list) and acceptance_criteria:
        criteria.append("- Antes de cerrar, verifica explicitamente en el artefacto objetivo que los acceptance_criteria se cumplieron despues de la mutacion.")
        criteria.append("- Si mutaste el archivo objetivo, la siguiente accion debe ser leer o comprobar ese mismo archivo; no cierres el ticket solo con la evidencia del write.")

    criteria.append("- Despues de cumplir el objetivo principal y verificarlo, no sigas creando artefactos auxiliares ni verificaciones redundantes.")
    criteria.append("- Si necesitas cerrar el ticket, la siguiente accion debe ser la minima accion final de cierre o escalacion, no una nueva cadena de auditoria local.")
    return "\n".join(criteria)


def _render_ticket_api_contract(ticket: Mapping[str, Any]) -> str:
    ticket_api = ticket.get("ticket_api")
    if not isinstance(ticket_api, Mapping):
        return (
            "No hay ticket_api configurada en este ticket. Si necesitas proponer una accion HTTP de cierre, "
            "usa el contrato real solo si tienes una URL completa. No inventes rutas relativas sin base_url."
        )

    base_url = str(ticket_api.get("base_url") or "N/D")
    update_template = str(ticket_api.get("update_template") or "/api/tickets/{ticket_id}")
    decision_template = str(ticket_api.get("decision_template") or "/api/tickets/{ticket_id}/decision/select")

    backend_id = ticket.get("backend_ticket_id") or ticket.get("id")
    api_ticket_id = str(int(backend_id)) if backend_id is not None else "{ticket_id}"
    resolved_update_url = f"{base_url.rstrip('/')}/tickets/{api_ticket_id}" if base_url != "N/D" else update_template

    allowed_statuses = ticket_api.get("allowed_statuses")
    if not isinstance(allowed_statuses, list):
        allowed_statuses = [
            "PENDING",
            "EXECUTED",
            "FAILED",
            "DERIVED",
            "PREAPROBADO",
            "APROBADO",
            "RECHAZADO",
            "PENDIENTE_EJECUCION",
            "EN_EJECUCION",
            "RESUELTO",
            "FALLIDO",
        ]
    statuses_text = ", ".join(str(item) for item in allowed_statuses)
    return "\n".join(
        [
            f"- base_url: {base_url}",
            f"- endpoint para actualizar ticket: PUT {update_template}",
            f"- URL concreta para CIERRE de este ticket: PUT {resolved_update_url}",
            f"- endpoint para seleccionar decision humana: PATCH {decision_template}",
            f"- estados permitidos conocidos: {statuses_text}",
            "- IMPORTANTE: Siempre usa la URL CONCRETA (con el ID numerico del backend) para las acciones HTTP, no uses placeholders como {ticket_id}.",
            f"- REGLAS ESTRICTAS DE PAYLOAD PARA PUT {resolved_update_url}:",
            "  1. CIERRE (RESUELTO, FALLIDO, DERIVED): Es OBLIGATORIO enviar 'status', 'execution_summary' (string), 'execution_logs' (objeto con metadatos + timeline) Y 'action_plan' (objeto con summary + steps). NO uses la clave 'resolution'.",
            "     Formato exacto de execution_logs: {\"run_id\": string, \"correlation_id\": string, \"iterations\": number, \"duration_seconds\": number, \"final_result\": {\"status\": string, \"message\": string}, \"timeline\": [{\"step\": \"read|write|verify|http\", \"artifact\": string, \"result\": string}]}",
            "     Formato exacto de action_plan: {\"summary\": string (resumen de lo ejecutado), \"steps\": [{\"tool\": \"shell|file|http|mcp\", \"description\": string}]} - Cada paso del timeline debe tener su correspondiente entrada en steps.",
            "  2. DECISION HUMANA: Es OBLIGATORIO enviar 'execution_status': 'WAITING_DECISION' y 'pending_decision' (con 'decision_id', 'question' y 'options' [minimo 2 opciones con 'option_id' y 'title']).",
            "  3. PENDIENTE_EJECUCION: Es OBLIGATORIO enviar 'status': 'PENDIENTE_EJECUCION' y 'capability_level' (string).",
            "  4. Los campos 'run_id', 'correlation_id', 'iterations', 'duration_seconds' DEBEN extraerse del [CONTEXTO_EJECUCION]. El campo 'final_result' debe reflejar el estado final de la ejecucion. El array 'timeline' debe contener cada paso ejecutado en orden cronologico.",
        ]
    )


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
        and any(key in ticket for key in ("ticket_context", "run_context", "base_prompts", "history"))
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
        "[SKILLS_APLICABLES]\n" + _render_selected_skills(ticket_map),
        "[CRITERIO_CIERRE]\n" + _render_completion_guidance(ticket_map),
        "[CONTRATO_API_TICKETS]\n" + _render_ticket_api_contract(ticket_map),
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

    def load_skills_catalog(self) -> list[dict[str, Any]]:
        return get_skills_catalog()

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
