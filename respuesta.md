# Prompt informativo para tu GPT

Usa este texto como contexto para otro GPT. Resume exactamente lo que se hizo al construir el proyecto `VICTOR On-Premise Agent`.

## INICIO PROMPT

Actua como arquitecto senior de sistemas de ejecucion segura para agentes on-premise.

Contexto: ya se ejecuto una fase de scaffolding estructural (sin implementar logica de negocio) para el proyecto `victor-agent`.

### Objetivo cumplido

Se construyo una base de proyecto para un runtime de agente seguro (no chatbot, no web app) que usa Claude Code SDK como motor de ejecucion, con enforcement de policy, integracion MCP, aislamiento por cliente y logging auditable.

### Acciones ejecutadas para armar el proyecto

1. Se creo el arbol raiz `victor-agent/` con separacion estricta entre:
   - `core/` (codigo del runtime)
   - `config/` (local/no versionado por cliente)
   - `runtime/` (estado local/no versionado)
   - `config.dist/` (plantillas versionables)
   - `mcp/`, `skills/`, `playbooks/`, `scripts/`, `tests/`, `docs/`

2. Se incorporaron archivos base de proyecto:
   - `pyproject.toml` con dependencias minimas y tooling (`pytest`, `ruff`, `mypy`)
   - `README.md` con objetivos, guardrails y layout
   - `.gitignore` orientado a excluir `config/` y `runtime/` reales

3. Se definio el nucleo por dominios en `core/` (solo stubs de modulos):
   - `orchestrator/`: loop, FSM, contexto de sesion, pause/resume
   - `llm/`: adapter Claude Code SDK, parser, prompt compiler
   - `actions/`: `action_gateway` obligatorio, router, registry, models
   - `policy/`: engine, capability matrix, decision artifacts, validators, hooks
   - `execution/`: service, registry, sandbox guards y executors (`shell`, `file`, `http`, `mcp`)
   - `mcp/`: client, registry, session manager, capability mapper
   - `planning/`: builder, validator, models
   - `tickets/`: client, mapper, lease manager, state manager
   - `decisions/`: engine, builder, approval client, escalation rules
   - `state/`: checkpoint, run state, idempotency
   - `observability/`: audit logger, event schema, emitter, redaction, correlation
   - `security/`: secrets provider, input sanitizer, output redactor, integrity verifier
   - `contracts/`: modelos compartidos (ticket, action, policy, decision, event)
   - `prompts/`: `base/`, `templates/`, `builders/`

4. Se creo estructura de configuracion:
   - `config/agent.yaml`, `config/policy.yaml`, `config/capabilities.yaml`, `config/mcp.yaml`
   - `config/secrets/`
   - `config.dist/` con equivalentes de ejemplo para versionar plantillas

5. Se creo estructura de runtime local:
   - `runtime/runs/`, `runtime/workspaces/`, `runtime/checkpoints/`, `runtime/audit/`, `runtime/decisions/`, `runtime/artifacts/`, `runtime/locks/`

6. Se agregaron activos MCP y operativos:
   - `mcp/manifests/`, `mcp/adapters/`, `mcp/allowlists/`
   - scripts stub: `run_agent.py`, `bootstrap_client.py`, `verify_policy_bundle.py`, `rotate_runtime.py`

7. Se preparo testing por capas:
   - `tests/unit/`, `tests/integration/`, `tests/contract/`, `tests/security/`, `tests/e2e/`

8. Se agrego documentacion de arquitectura y seguridad:
   - `docs/architecture.md`
   - `docs/threat_model.md`
   - ADR: `docs/adr/0001-action-gateway-boundary.md` (policy gate obligatorio)

### Decisiones arquitectonicas clave ya reflejadas en la estructura

- No hay ruta directa de ejecucion fuera de `core/actions/action_gateway.py`.
- `observability/` reemplaza `logging/` para evitar colision semantica con stdlib.
- MCP se trata como frontera de confianza separada y deny-by-default.
- Estado y reintentos se preparan con `checkpoint_store` + `idempotency_store`.
- Se preserva single-tenant por despliegue y aislamiento por run/workspace.

### Notas sobre .gitkeep

Se usaron multiples `.gitkeep` para mantener carpetas vacias dentro del scaffold inicial (Git no guarda directorios vacios por si solo). En carpetas no versionadas (`config/`, `runtime/`) sirven principalmente para dejar la estructura visible en local durante bootstrap.

### Estado actual

- Estructura completa: SI
- Modulos base (stubs): SI
- Implementacion funcional del runtime: NO (pendiente)

### Siguiente fase recomendada

1. Implementar contratos tipados (`pydantic`) en `core/contracts/*.py`.
2. Cablear flujo `execution_loop -> action_gateway -> policy_engine -> execution_service`.
3. Implementar schemas de eventos auditables y redaccion de secretos.
4. Agregar pruebas de seguridad para demostrar que no existe bypass de policy.

## FIN PROMPT
