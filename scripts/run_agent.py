from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib import error as url_error
from urllib import request as url_request
from uuid import uuid4

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.actions.action_gateway import ActionGateway
from core.llm.claude_adapter import ClaudeAdapter
from core.orchestrator.victor_loop import VictorLoop


_BANNER = r"""
 __     _____ ____ _____ ___  ____           __  _____   ____
\ \   / /_ _/ ___|_   _/ _ \|  _ \          \ \/ / _ \ / ___|
 \ \ / / | | |     | || | | | |_) |  _____   \  / | | | |
  \ V /  | | |___  | || |_| |  _ <  |_____|  /  \ |_| | |___
   \_/  |___\____| |_| \___/|_| \_\         /_/\_\___/ \____|
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Arranca una ejecucion del agente VICTOR")
    parser.add_argument("--ticket-file", type=str, default=None, help="Ruta a ticket JSON")
    parser.add_argument("--ticket-json", type=str, default=None, help="Ticket JSON inline")
    parser.add_argument("--capability-level", type=str, default=None, help="Nivel de capacidad (C1/C2/C3)")
    parser.add_argument("--max-iterations", type=int, default=10, help="Maximo de iteraciones del loop")
    parser.add_argument("--pretty", action="store_true", help="Imprime salida JSON formateada")
    parser.add_argument("--menu", action="store_true", help="Abre un menu interactivo de pruebas")
    parser.add_argument("--no-menu", action="store_true", help="Desactiva menu automatico")
    parser.add_argument("--demo-patch-flow", action="store_true", help="Ejecuta demo guiada de parcheo")
    parser.add_argument("--run-demo-test", action="store_true", help="Ejecuta test de demo desde consola")
    args = parser.parse_args()

    _load_dotenv(PROJECT_ROOT / "config" / "secrets" / ".env")
    _print_banner()

    if args.run_demo_test:
        return _run_demo_test(PROJECT_ROOT)

    if args.demo_patch_flow:
        result = simulate_ticket_patch_flow(project_root=PROJECT_ROOT, verbose=True)
        _print_json(result, pretty=args.pretty)
        return 0 if result.get("status") == "COMPLETED" else 1

    agent_config = _read_yaml(PROJECT_ROOT / "config" / "agent.yaml")

    should_open_menu = args.menu or (
        not args.no_menu
        and args.ticket_file is None
        and args.ticket_json is None
        and sys.stdin.isatty()
    )
    if should_open_menu:
        return _interactive_menu(args=args, agent_config=agent_config)

    ticket = _load_ticket(args, agent_config)
    return _run_loop(ticket=ticket, max_iterations=args.max_iterations, pretty=args.pretty)


def _interactive_menu(args: argparse.Namespace, agent_config: dict[str, Any]) -> int:
    while True:
        print("Menu VICTOR - XOC")
        print("1) Ejecutar agente con ticket demo")
        print("2) Simular flujo: ticket -> parcheo -> devolucion")
        print("3) Ejecutar tests (simulacion + prueba real LLM Claude)")
        print("4) Salir")
        choice = input("Selecciona una opcion [1-4]: ").strip()

        if choice == "1":
            ticket = _load_ticket(args, agent_config)
            adapter_override = None
            if not _has_llm_credentials():
                adapter_override = _build_local_demo_adapter()
                print(
                    "No se detectaron credenciales Claude. "
                    "Se ejecuta un modo demo local determinista para evitar MAX_ITERATIONS."
                )
            code = _run_loop(
                ticket=ticket,
                max_iterations=args.max_iterations,
                pretty=True,
                claude_adapter_module=adapter_override,
            )
            print(f"Resultado opcion 1: {'OK' if code == 0 else 'FALLO'}")
            print()
            continue

        if choice == "2":
            result = simulate_ticket_patch_flow(project_root=PROJECT_ROOT, verbose=True)
            _print_json(result, pretty=True)
            print()
            continue

        if choice == "3":
            code = _run_menu_tests(PROJECT_ROOT, agent_config)
            print(f"Resultado opcion 3: {'OK' if code == 0 else 'FALLO'}")
            print()
            continue

        if choice == "4":
            print("Saliendo de VICTOR - XOC.")
            return 0

        print("Opcion invalida, intenta nuevamente.\n")


def _run_loop(
    *,
    ticket: dict[str, Any],
    max_iterations: int,
    pretty: bool,
    claude_adapter_module: Any | None = None,
) -> int:
    loop_kwargs: dict[str, Any] = {"max_iterations": max(1, int(max_iterations))}
    if claude_adapter_module is not None:
        loop_kwargs["claude_adapter_module"] = claude_adapter_module
    loop = VictorLoop(**loop_kwargs)
    result = loop.run(ticket)
    _print_json(result, pretty=pretty)

    execution_status = str(result.get("execution_status") or "")
    if execution_status in {"COMPLETED", "WAITING_DECISION"}:
        return 0
    return 1


def simulate_ticket_patch_flow(
    *,
    project_root: Path | None = None,
    runtime_root: Path | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    root = project_root or PROJECT_ROOT
    runtime_dir = runtime_root or (root / "runtime")
    artifacts_dir = runtime_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    run_id = f"run-demo-{uuid4()}"
    ticket_id = f"ticket-demo-{uuid4()}"
    trace: list[dict[str, Any]] = []

    original_file = artifacts_dir / "servicio_demo.conf"
    original_content = "version=1\nmodo=legacy\n"
    original_file.write_text(original_content, encoding="utf-8")

    ticket = {
        "ticket_id": ticket_id,
        "title": "Parchear configuracion insegura",
        "status": "NEW",
        "priority": "HIGH",
        "llm_model": "claude-sonnet-4-6",
        "client_context": {
            "client_id": "demo-client",
            "capability_level": "C2_CONTROLADO",
            "environment": "on-prem",
        },
        "task": {
            "target_file": str(original_file),
            "find": "modo=legacy",
            "replace": "modo=seguro",
        },
    }
    _trace(trace, verbose, "PLAN", "Ticket recibido", {"ticket_id": ticket_id, "run_id": run_id})

    target_file = Path(str(ticket["task"]["target_file"]))
    patched_content = original_content.replace(
        str(ticket["task"]["find"]),
        str(ticket["task"]["replace"]),
    )

    def _fake_claude_sdk(prompt: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "action_type": "file",
            "parameters": {
                "operation": "write",
                "path": str(target_file),
                "content": patched_content,
            },
            "description": "Aplicar parche de configuracion en archivo objetivo",
            "confidence": 0.97,
        }

    adapter = ClaudeAdapter(sdk_client=_fake_claude_sdk)
    prompt = (
        "Ticket de ejemplo: aplicar parche de configuracion de modo legacy a modo seguro. "
        "Devuelve una sola accion estructurada."
    )
    action = adapter.generate_action(
        prompt,
        {
            "run_id": run_id,
            "ticket_id": ticket_id,
            "model": str(ticket.get("llm_model")),
        },
    )
    action_payload = action.to_dict()
    _trace(
        trace,
        verbose,
        "PLAN",
        "Claude Code propone accion de parcheo",
        {"action_id": action_payload.get("action_id"), "action_type": action_payload.get("type")},
    )

    gateway = ActionGateway()
    gateway_result = gateway.handle_action(
        action=action,
        context={
            "run_id": run_id,
            "ticket_id": ticket_id,
            "capability_level": "C2_CONTROLADO",
            "workspace_root": str(runtime_dir),
        },
    )
    _trace(
        trace,
        verbose,
        "EXEC",
        "Action Gateway procesa la accion de parcheo",
        {"status": gateway_result.get("status")},
    )

    if gateway_result.get("status") != "ALLOWED":
        _trace(
            trace,
            verbose,
            "ERROR",
            "El parche fue bloqueado o fallo",
            {"gateway_result": gateway_result},
        )
        return {
            "status": "FAILED",
            "run_id": run_id,
            "ticket_id": ticket_id,
            "trace": trace,
            "gateway_result": gateway_result,
        }

    file_after = target_file.read_text(encoding="utf-8")
    _trace(
        trace,
        verbose,
        "VERIFY",
        "Parche aplicado en archivo objetivo",
        {"target_file": str(target_file)},
    )

    ticket_response = {
        "ticket_id": ticket_id,
        "run_id": run_id,
        "status": "RESUELTO",
        "summary": "Ticket parchado y validado",
        "before": original_content,
        "after": file_after,
        "patch_applied": "modo=legacy -> modo=seguro",
        "gateway_status": gateway_result.get("status"),
    }
    response_file = artifacts_dir / f"{ticket_id}_patched_response.json"
    dispatch_result = _dispatch_ticket_response(ticket_response, response_file)
    _trace(
        trace,
        verbose,
        "VERIFY",
        "Envio de devolucion del ticket parchado",
        {"dispatch_status": dispatch_result.get("status"), "response_file": str(response_file)},
    )

    return {
        "status": "COMPLETED",
        "run_id": run_id,
        "ticket_id": ticket_id,
        "trace": trace,
        "action": action_payload,
        "gateway_result": gateway_result,
        "dispatch_result": dispatch_result,
        "patched_file": str(target_file),
        "response_file": str(response_file),
    }


def _dispatch_ticket_response(payload: dict[str, Any], output_file: Path) -> dict[str, Any]:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {
        "status": "SENT",
        "output_file": str(output_file),
        "bytes": output_file.stat().st_size,
    }


def _run_demo_test(project_root: Path) -> int:
    test_path = project_root / "tests" / "e2e" / "test_menu_patch_simulation.py"
    if not test_path.exists():
        print("No se encontro test de simulacion en tests/e2e/test_menu_patch_simulation.py")
        return 1

    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        str(test_path),
        "-s",
    ]
    print("Ejecutando test de simulacion...")
    completed = subprocess.run(command, cwd=str(project_root), check=False)
    return int(completed.returncode)


def _run_menu_tests(project_root: Path, agent_config: dict[str, Any]) -> int:
    demo_code = _run_demo_test(project_root)
    llm_code = _run_live_llm_test(agent_config)

    if demo_code == 0 and llm_code == 0:
        return 0
    return 1


def _run_live_llm_test(agent_config: dict[str, Any]) -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
    if not api_key:
        print("Prueba LLM: FALLO (no se encontro ANTHROPIC_API_KEY o CLAUDE_API_KEY)")
        return 1

    llm = _extract_llm_settings(agent_config)
    model = str(llm.get("model") or "claude-sonnet-4-6")

    payload = {
        "model": model,
        "max_tokens": 32,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": "Responde exactamente con: OK_VICTOR_LLM",
            }
        ],
    }

    request = url_request.Request(
        url="https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    print(f"Prueba LLM: ejecutando llamada real a Claude con modelo '{model}'...")

    try:
        with url_request.urlopen(request, timeout=45) as response:
            body = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(body)
    except url_error.HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = str(exc)
        print(f"Prueba LLM: FALLO (HTTP {exc.code})")
        if error_body:
            print(error_body)
        return 1
    except Exception as exc:
        print(f"Prueba LLM: FALLO ({exc})")
        return 1

    text = _extract_claude_text(parsed)
    if not text:
        print("Prueba LLM: FALLO (respuesta sin texto)")
        return 1

    print(f"Prueba LLM: respuesta = {text}")
    if "OK_VICTOR_LLM" in text:
        print("Prueba LLM: OK")
        return 0

    print("Prueba LLM: FALLO (respuesta no coincide)")
    return 1


def _extract_claude_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, list):
        texts: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                texts.append(block["text"])
        if texts:
            return "\n".join(texts).strip()

    if isinstance(payload.get("output_text"), str):
        return str(payload["output_text"]).strip()

    if isinstance(payload.get("completion"), str):
        return str(payload["completion"]).strip()

    return ""


def _has_llm_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY"))


def _build_local_demo_adapter() -> Any:
    class _DemoAdapter:
        def call_claude(self, prompt: Any = None, context: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
            return {"prompt": str(prompt or ""), "context": dict(context or {}), "kwargs": dict(kwargs)}

        @staticmethod
        def parse_response(response: dict[str, Any]) -> dict[str, Any]:
            return {
                "action_id": f"demo-{uuid4()}",
                "type": "file",
                "parameters": {
                    "operation": "exists",
                    "path": ".",
                },
                "description": "Chequeo local determinista para demo sin credenciales",
                "completed": True,
                "metadata": {
                    "demo_mode": True,
                    "source": "interactive_menu",
                },
            }

    return _DemoAdapter()


def _trace(trace: list[dict[str, Any]], verbose: bool, phase: str, message: str, metadata: dict[str, Any]) -> None:
    event = {
        "phase": phase,
        "message": message,
        "metadata": dict(metadata),
    }
    trace.append(event)
    if verbose:
        step = len(trace)
        print(f"[{step}] {phase}: {message}")
        if metadata:
            print("    " + json.dumps(metadata, ensure_ascii=False, default=str))


def _load_ticket(args: argparse.Namespace, agent_config: dict[str, Any]) -> dict[str, Any]:
    llm_settings = _extract_llm_settings(agent_config)

    if args.ticket_json:
        payload = json.loads(args.ticket_json)
        if not isinstance(payload, dict):
            raise ValueError("--ticket-json debe representar un objeto JSON")
        ticket = dict(payload)
    elif args.ticket_file:
        ticket_path = Path(args.ticket_file)
        payload = json.loads(ticket_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("El ticket en archivo debe ser un objeto JSON")
        ticket = dict(payload)
    else:
        ticket = {
            "ticket_id": "ticket-demo",
            "title": "Ejecucion de prueba",
            "priority": "MEDIUM",
            "status": "NEW",
            "llm": llm_settings,
            "llm_model": llm_settings.get("model"),
            "client_context": {
                "client_id": str(agent_config.get("client_id") or "demo-client"),
                "capability_level": "C1_RESTRINGIDO",
                "environment": str(agent_config.get("environment") or "on-prem"),
            },
        }

    client_context = ticket.get("client_context")
    if not isinstance(client_context, dict):
        client_context = {}
    if args.capability_level:
        client_context["capability_level"] = str(args.capability_level).upper()
    ticket["client_context"] = client_context

    if "llm" not in ticket:
        ticket["llm"] = llm_settings
    if "llm_model" not in ticket and llm_settings.get("model") is not None:
        ticket["llm_model"] = llm_settings.get("model")

    ticket.setdefault("ticket_id", "ticket-demo")
    return ticket


def _extract_llm_settings(agent_config: dict[str, Any]) -> dict[str, Any]:
    llm_raw = agent_config.get("llm")
    if isinstance(llm_raw, dict):
        settings = dict(llm_raw)
    else:
        settings = {}

    settings.setdefault("provider", "anthropic")
    settings.setdefault("model", "claude-sonnet-4-6")
    settings.setdefault("temperature", 0.0)
    settings.setdefault("max_tokens", 4000)
    return settings


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return {}
    return dict(loaded)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _print_json(value: dict[str, Any], *, pretty: bool) -> None:
    if pretty:
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(value, ensure_ascii=False, default=str))


def _print_banner() -> None:
    print(_BANNER.strip("\n"))
    print()


if __name__ == "__main__":
    sys.exit(main())
