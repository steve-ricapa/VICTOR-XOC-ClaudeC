"""Backend ticket API client."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse
from urllib.parse import urljoin


DEFAULT_UPDATE_TEMPLATE = "/api/tickets/{ticket_id}"
DEFAULT_DECISION_TEMPLATE = "/api/tickets/{ticket_id}/decision/select"


def resolve_relative_ticket_url(
    url: str,
    *,
    base_url: str | None,
) -> str:
    if not url:
        return url
    normalized_url = str(url).strip()
    if normalized_url.startswith("http://") or normalized_url.startswith("https://"):
        return normalized_url
    if not base_url:
        return normalized_url
    normalized_base = str(base_url).rstrip("/")
    parsed_base = urlparse(normalized_base)
    base_path = parsed_base.path.rstrip("/")

    normalized_path = normalized_url if normalized_url.startswith("/") else f"/{normalized_url}"
    if base_path and normalized_path.startswith(base_path + "/"):
        normalized_path = normalized_path[len(base_path) :]
    elif base_path and normalized_path == base_path:
        normalized_path = "/"

    return urljoin(normalized_base + "/", normalized_path.lstrip("/"))


def extract_ticket_api(ticket: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ticket, Mapping):
        return {}
    raw = ticket.get("ticket_api")
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}


def build_ticket_update_url(ticket: Mapping[str, Any] | None) -> str | None:
    ticket_api = extract_ticket_api(ticket)
    ticket_id = _backend_ticket_id(ticket)
    if not ticket_id:
        return None
    template = str(ticket_api.get("update_template") or DEFAULT_UPDATE_TEMPLATE)
    base_url = ticket_api.get("base_url")
    relative = template.format(ticket_id=ticket_id)
    return resolve_relative_ticket_url(relative, base_url=str(base_url) if base_url else None)


def build_ticket_decision_url(ticket: Mapping[str, Any] | None) -> str | None:
    ticket_api = extract_ticket_api(ticket)
    ticket_id = _backend_ticket_id(ticket)
    if not ticket_id:
        return None
    template = str(ticket_api.get("decision_template") or DEFAULT_DECISION_TEMPLATE)
    base_url = ticket_api.get("base_url")
    relative = template.format(ticket_id=ticket_id)
    return resolve_relative_ticket_url(relative, base_url=str(base_url) if base_url else None)


def normalize_http_action_for_ticket(action: Mapping[str, Any], ticket: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(action)
    action_type = str(payload.get("type") or payload.get("action_type") or "").lower()
    if action_type != "http":
        return payload

    parameters = payload.get("parameters") if isinstance(payload.get("parameters"), Mapping) else {}
    merged_parameters = dict(parameters)
    method = str(payload.get("method") or merged_parameters.get("method") or "GET").upper()
    url = payload.get("url") or merged_parameters.get("url")
    if url is None:
        return payload

    ticket_api = extract_ticket_api(ticket)
    if _looks_like_ticket_update_path(str(url)):
        resolved = build_ticket_update_url(ticket) or resolve_relative_ticket_url(
            str(url), base_url=str(ticket_api.get("base_url") or "") or None
        )
    elif _looks_like_ticket_decision_path(str(url)):
        resolved = build_ticket_decision_url(ticket) or resolve_relative_ticket_url(
            str(url), base_url=str(ticket_api.get("base_url") or "") or None
        )
    else:
        resolved = resolve_relative_ticket_url(str(url), base_url=str(ticket_api.get("base_url") or "") or None)
    normalized_method = _normalize_ticket_http_method(url=str(url), resolved_url=resolved, method=method)
    headers = payload.get("headers") if isinstance(payload.get("headers"), Mapping) else {}
    merged_headers = dict(headers)
    param_headers = merged_parameters.get("headers") if isinstance(merged_parameters.get("headers"), Mapping) else {}
    merged_headers.update(dict(param_headers))
    auth_token = ticket_api.get("auth_token")
    if auth_token:
        merged_headers["Authorization"] = f"Bearer {auth_token}"
    merged_headers.setdefault("Content-Type", "application/json")

    body = payload.get("body") if isinstance(payload.get("body"), Mapping) else {}
    merged_body = dict(body)
    param_body = merged_parameters.get("body") if isinstance(merged_parameters.get("body"), Mapping) else {}
    merged_body.update(dict(param_body))
    if merged_body:
        _normalize_ticket_status_field(merged_body)

    payload["method"] = normalized_method
    payload["url"] = resolved
    merged_parameters["method"] = normalized_method
    merged_parameters["url"] = resolved
    merged_parameters["headers"] = merged_headers
    if merged_body:
        merged_parameters["body"] = merged_body
        payload["body"] = merged_body
    payload["headers"] = merged_headers
    payload["parameters"] = merged_parameters
    return payload


def _normalize_ticket_http_method(*, url: str, resolved_url: str, method: str) -> str:
    candidates = f"{url} {resolved_url}".lower()
    if "/decision/select" in candidates or candidates.endswith("/approve") or candidates.endswith("/reject"):
        return "PATCH"
    if "/tickets/" in candidates:
        return "PUT"
    return method


def _looks_like_ticket_update_path(url: str) -> bool:
    normalized = str(url).lower()
    return "/tickets/" in normalized and "/decision/select" not in normalized and not normalized.endswith("/approve") and not normalized.endswith("/reject")


def _looks_like_ticket_decision_path(url: str) -> bool:
    normalized = str(url).lower()
    return "/decision/select" in normalized


def _backend_ticket_id(ticket: Mapping[str, Any] | None) -> str:
    if not isinstance(ticket, Mapping):
        return ""
    return str(
        ticket.get("backend_ticket_id")
        or (ticket.get("ticket_api") or {}).get("backend_ticket_id")
        or ticket.get("ticket_id")
        or ticket.get("id")
        or ""
    )


 