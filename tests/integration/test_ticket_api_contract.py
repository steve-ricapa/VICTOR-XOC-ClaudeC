from __future__ import annotations

import argparse

from core.contracts.action import Action
from core.orchestrator.victor_loop import VictorLoop
from core.tickets.ticket_client import normalize_http_action_for_ticket
from scripts.run_agent import _extract_ticket_api_settings, _load_ticket


def test_ticket_client_resolves_relative_ticket_url_with_base_url() -> None:
    ticket = {
        "ticket_id": "ticket-123",
        "ticket_api": {
            "base_url": "https://txdxai-flask.replit.app/api",
            "update_template": "/tickets/{ticket_id}",
        },
    }
    action = {
        "action_id": "action-http-1",
        "type": "http",
        "parameters": {
            "method": "PATCH",
            "url": "/tickets/ticket-123",
        },
    }

    normalized = normalize_http_action_for_ticket(action, ticket)

    assert normalized["parameters"]["url"] == "https://txdxai-flask.replit.app/api/tickets/ticket-123"
    assert normalized["parameters"]["method"] == "PUT"


def test_ticket_client_does_not_duplicate_api_prefix() -> None:
    ticket = {
        "ticket_id": "ticket-123",
        "ticket_api": {
            "base_url": "https://txdxai-flask.replit.app/api",
        },
    }
    action = {
        "action_id": "action-http-2",
        "type": "http",
        "parameters": {
            "method": "PATCH",
            "url": "/api/tickets/ticket-123",
        },
    }

    normalized = normalize_http_action_for_ticket(action, ticket)

    assert normalized["parameters"]["url"] == "https://txdxai-flask.replit.app/api/tickets/ticket-123"
    assert normalized["parameters"]["method"] == "PUT"


def test_ticket_client_uses_backend_ticket_id_and_normalizes_status() -> None:
    ticket = {
        "ticket_id": "ticket-local-123",
        "backend_ticket_id": 1,
        "ticket_api": {
            "base_url": "https://txdxai-flask.replit.app/api",
        },
    }
    action = {
        "action_id": "action-http-3",
        "type": "http",
        "parameters": {
            "method": "PATCH",
            "url": "/tickets/ticket-local-123",
            "body": {
                "status": "RESOLVED",
                "resolution": "done",
            },
        },
    }

    normalized = normalize_http_action_for_ticket(action, ticket)

    assert normalized["parameters"]["url"] == "https://txdxai-flask.replit.app/api/tickets/1"
    assert normalized["parameters"]["method"] == "PUT"
    assert normalized["parameters"]["body"]["status"] == "RESUELTO"


def test_load_ticket_injects_ticket_api_settings_from_agent_config() -> None:
    args = argparse.Namespace(ticket_json=None, ticket_file=None, capability_level=None)
    agent_config = {
        "client_id": "demo-client",
        "environment": "on-prem",
        "ticket_api": {
            "base_url": "https://txdxai-flask.replit.app/api",
            "update_template": "/tickets/{ticket_id}",
            "decision_template": "/tickets/{ticket_id}/decision/select",
        },
    }

    settings = _extract_ticket_api_settings(agent_config)
    ticket = _load_ticket(args, agent_config)

    assert settings["base_url"] == "https://txdxai-flask.replit.app/api"
    assert ticket["ticket_api"]["update_template"] == "/tickets/{ticket_id}"
    assert ticket["ticket_api"]["decision_template"] == "/tickets/{ticket_id}/decision/select"


def test_victor_loop_normalizes_action_objects_for_ticket_api() -> None:
    loop = VictorLoop(max_iterations=1)
    ticket = {
        "ticket_id": "ticket-local-123",
        "backend_ticket_id": 1,
        "ticket_api": {"base_url": "https://txdxai-flask.replit.app/api"},
    }
    action = Action.from_payload(
        {
            "action_id": "action-http-4",
            "type": "http",
            "parameters": {
                "method": "PATCH",
                "url": "/api/tickets/ticket-local-123",
                "body": {"status": "RESOLVED"},
            },
        }
    )

    normalized = loop._normalize_action_for_ticket_context(action=action, ticket=ticket)

    assert normalized["parameters"]["url"] == "https://txdxai-flask.replit.app/api/tickets/1"
    assert normalized["parameters"]["method"] == "PUT"
    assert normalized["parameters"]["body"]["status"] == "RESUELTO"
