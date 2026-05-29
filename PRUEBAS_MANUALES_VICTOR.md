# Pruebas Manuales de VICTOR

Usa siempre el Python del entorno virtual:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_agent.py --ticket-file .\NOMBRE_DEL_TICKET.json --pretty --no-menu
```

## 1. Remediacion simple

Archivo:
- `ticket_create_txt.json`

Comando:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_agent.py --ticket-file .\ticket_create_txt.json --pretty --no-menu
```

Que deberias ver:
- se crea `runtime/artifacts/prueba_victor.txt`
- contenido: `VICTOR OK`
- el run probablemente termine en `WAITING_DECISION` porque intenta cerrar el ticket con `ticket_update`

Verificacion:

```powershell
Get-Content .\runtime\artifacts\prueba_victor.txt
```

## 2. Aprobacion requerida

Archivo:
- `ticket_approval_http.json`

Comando:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_agent.py --ticket-file .\ticket_approval_http.json --pretty --no-menu
```

Que deberias ver:
- `WAITING_DECISION`
- una accion propuesta que requiera aprobacion por politica
- un bloque `pending_decision` con pregunta, opciones y recomendacion

Campos a revisar en la salida:
- `status`
- `execution_status`
- `pending_decision.question`
- `pending_decision.action_preview`
- `pending_decision.recommended_option`

## 3. Bloqueo por politica

Archivo:
- `ticket_block_admin.json`

Comando:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_agent.py --ticket-file .\ticket_block_admin.json --pretty --no-menu
```

Que deberias ver:
- `FAILED` o `BLOCKED` a nivel de ejecucion final
- detalle de politica indicando bloqueo

Campos a revisar en la salida:
- `status`
- `execution_status`
- `failure_response.error_type`
- `failure_response.message`

## Auditoria

Para revisar eventos del agente:

```powershell
Get-Content .\runtime\audit\events.jsonl
```

## Nota importante

Hoy el repo si genera solicitudes de aprobacion, pero no expone todavia un flujo CLI claro para responder `Approve`, `Deny` o `Pause` y continuar el mismo run desde consola. Estas pruebas sirven para observar el comportamiento del agente y validar seguridad, no para cerrar todavia un ciclo humano-en-el-loop completo desde CLI.

## Arquitectura futura

La aprobacion por CLI no debe ser el producto final. Debe ser solo un adaptador de operador para pruebas, soporte o debugging.

El flujo objetivo para web y app movil debe ser este:

1. Sophia o el backend crea el ticket.
2. El backend guarda el ticket y dispara un run de VICTOR.
3. VICTOR procesa el ticket.
4. Si necesita aprobacion, genera un `pending_decision` estructurado.
5. El backend guarda ese `pending_decision` en base de datos.
6. Web y app movil consultan ese estado.
7. El operador responde `Approve`, `Deny` o `Pause` desde UI.
8. El backend persiste la respuesta.
9. El backend reanuda el run de VICTOR con esa decision.
10. VICTOR continua, ejecuta o cancela, y devuelve estado final.

## Como deberia modelarse

En vez de implementar primero un flujo solo para CLI, conviene implementar un nucleo reusable con estas piezas:

- `PendingDecisionStore`
- `DecisionResponse`
- `ResumeRunService`
- `TicketUpdateAdapter`

Luego ese mismo nucleo puede exponerse por:

- CLI
- API REST
- web
- app movil

## Contratos recomendados

Entidades minimas:

- `tickets`
- `agent_runs`
- `pending_decisions`
- `decision_events`

Endpoints futuros sugeridos:

- `POST /tickets`
- `GET /tickets/:id`
- `GET /decisions/:decisionId`
- `POST /decisions/:decisionId/respond`
- `POST /runs/:runId/resume`

## Recomendacion tecnica

Si se implementa `Approve/Deny/Pause` en este repo, debe hacerse pensando en backend primero y CLI despues.

Orden recomendado:

1. Definir contrato de `pending_decision`.
2. Persistir decision y contexto de run.
3. Permitir reanudar el run con una decision humana.
4. Exponer esa capacidad por CLI.
5. Exponer esa misma capacidad por API para web y app movil.

## Skills propios

Para el dominio de ciberseguridad y remediacion, es mejor crear skills propios dentro de este repo en vez de depender de skills externos genericos.

Motivos:

- alineacion con politicas reales del proyecto
- lenguaje y formato adaptados a Sophia y VICTOR
- menor riesgo de instrucciones genericas o inseguras
- facilidad para auditar y evolucionar el comportamiento

Skills iniciales creados en este repo:

- `skills/vulnerability_triage.yaml`
- `skills/secure_file_remediation.yaml`
- `skills/network_security_investigation.yaml`
- `skills/human_escalation.yaml`
