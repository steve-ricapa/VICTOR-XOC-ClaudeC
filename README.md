# VICTOR On-Premise Agent

Runtime seguro, on-premise y single-tenant para ejecutar tareas operativas con control estricto de politicas, decisiones humanas y trazabilidad completa.

## Objetivo del sistema

- Procesar tickets operativos.
- Proponer acciones con Claude Code SDK.
- Validar siempre por politica antes de ejecutar.
- Ejecutar con aislamiento y auditoria estructurada.
- Pausar para aprobacion humana cuando aplica.

## Como arrancar el agente

1. Crear entorno Python e instalar dependencias:
   - `python -m venv .venv`
   - `.venv\Scripts\activate` (Windows)
   - `pip install -e .`

2. Configurar archivos locales (no versionados):
   - `config/agent.yaml`
   - `config/policy.yaml`
   - `config/capabilities.yaml`
   - `config/mcp.yaml`
   - Usa `config.dist/` como base.

3. Configurar secretos:
   - Archivo: `config/secrets/.env`
   - Variable recomendada: `ANTHROPIC_API_KEY=tu_key_real`
   - Alias opcional: `CLAUDE_API_KEY=tu_key_real`

4. Ejecutar un ticket de prueba:
   - `python scripts/run_agent.py` (abre menu interactivo)
   - `python scripts/run_agent.py --pretty` (ejecucion directa sin menu)
   - Opcional: `python scripts/run_agent.py --ticket-file ruta/al/ticket.json --pretty`

5. Ver resultado y auditoria:
   - Salida del run en consola (JSON)
   - Eventos en `runtime/audit/events.jsonl`

## Estructura principal

- `core/`: orquestacion, politicas, ejecucion, decisiones, observabilidad, MCP y contratos.
- `config/`: configuracion local por cliente (no versionada).
- `config.dist/`: plantillas versionadas.
- `runtime/`: estado local, auditoria, checkpoints y artefactos (no versionado).
- `mcp/`: assets de integracion (manifests, adapters, allowlists).
- `skills/`: definiciones reutilizables por dominio/cliente.
- `playbooks/`: flujos predefinidos ejecutables paso a paso.
- `scripts/`: utilidades operativas.
- `tests/`: pruebas de seguridad, integracion, contrato, unitarias y e2e.

## Reglas de seguridad

- Ninguna accion se ejecuta fuera de `core/actions/action_gateway.py`.
- `execution_service` rechaza ejecucion si no existe decision de politica `ALLOWED`.
- MCP es deny-by-default: herramienta no registrada o no permitida se bloquea.
- Se redactan datos sensibles antes de persistir logs.

## Ejecucion en espanol

- Los prompts base y templates del agente estan en espanol.
- El comportamiento esperado del agente y sus respuestas operativas es en espanol.

## Validacion

- Correr toda la suite:
  - `pytest -q`
- Estado actual esperado:
  - pruebas de seguridad, integracion y e2e en verde.

## Menu interactivo

Al arrancar `python scripts/run_agent.py` se muestra un menu con opciones:

1. Ejecutar agente con ticket demo.
2. Simular flujo completo de parcheo:
   - ticket recibido
   - Claude propone accion de parcheo
   - Action Gateway valida/ejecuta
   - se genera envio de devolucion del ticket parchado
3. Ejecutar test de esa simulacion desde el menu.

Tambien puedes ejecutar opciones directas por bandera:

- `python scripts/run_agent.py --demo-patch-flow --pretty`
- `python scripts/run_agent.py --run-demo-test`
