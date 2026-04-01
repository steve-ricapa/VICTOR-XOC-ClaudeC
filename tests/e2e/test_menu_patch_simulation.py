from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_agent import simulate_ticket_patch_flow
from scripts.run_agent import _build_local_demo_adapter
from core.orchestrator.victor_loop import VictorLoop


def test_menu_patch_simulation_flow_completes_and_dispatches_ticket(tmp_path: Path) -> None:
    result = simulate_ticket_patch_flow(runtime_root=tmp_path / "runtime", verbose=False)

    assert result["status"] == "COMPLETED"
    assert result["gateway_result"]["status"] == "ALLOWED"
    assert result["dispatch_result"]["status"] == "SENT"

    patched_file = Path(result["patched_file"])
    response_file = Path(result["response_file"])

    assert patched_file.exists()
    assert response_file.exists()

    patched_content = patched_file.read_text(encoding="utf-8")
    assert "modo=seguro" in patched_content
    assert "modo=legacy" not in patched_content

    payload = json.loads(response_file.read_text(encoding="utf-8"))
    assert payload["status"] == "RESUELTO"
    assert payload["patch_applied"] == "modo=legacy -> modo=seguro"
    assert payload["gateway_status"] == "ALLOWED"

    trace = result["trace"]
    phases = [event["phase"] for event in trace]
    assert "PLAN" in phases
    assert "EXEC" in phases
    assert "VERIFY" in phases


def test_menu_demo_adapter_completes_without_llm_credentials() -> None:
    loop = VictorLoop(max_iterations=2, claude_adapter_module=_build_local_demo_adapter())
    result = loop.run(
        {
            "ticket_id": "ticket-menu-demo",
            "status": "NEW",
            "client_context": {
                "capability_level": "C1_RESTRINGIDO",
            },
        }
    )

    assert result["status"] == "RESUELTO"
    assert result["execution_status"] == "COMPLETED"
