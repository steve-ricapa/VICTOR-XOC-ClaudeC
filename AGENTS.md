# AGENTS.md - VICTOR On-Premise Agent

## Repository Purpose

VICTOR is an on-premise AI agent that processes operational tickets from xoc.app, generates remediation plans using Claude, and executes shell commands on the target server. It is deployed as a Docker container on a "XOC APPLIANCE" (mini server) and is called by the XOC-API-AWS backend via HTTP.

## Architecture Overview

```
xoc.app (Frontend)
    |
    | POST /tickets
    v
XOC-API-AWS (Lambda + Step Functions)
    |
    | HTTP POST /api/agents/VictorDurableAgent/run
    v
VICTOR On-Premise (Docker on XOC APPLIANCE)
    victor_server.py (FastAPI, port 8000)
        |
        | Calls Claude API for assessment/plan
        | HTTP POST to executor for command execution
        v
    laptop_agent.py (HTTP executor, port 8888)
        |
        | subprocess.run(shell=True)
        v
    Target OS (Linux/Windows commands)
```

## Two Entry Points (Important!)

The repo has TWO separate code paths:

### 1. `victor_server.py` - PRODUCTION ENTRY POINT (FastAPI)
- **This is what XOC-API-AWS calls in production**
- FastAPI server on port 8000
- Endpoint: `POST /api/agents/VictorDurableAgent/run`
- Three phases: `assessment`, `plan`, `execute`
- Calls Claude directly (Anthropic API or Azure endpoint)
- Executes commands via `laptop_agent.py` (HTTP executor)
- Uses `json5` for parsing Claude responses
- Updates DynamoDB ticket status on success

### 2. `core/` system (VictorLoop) - ADVANCED/UNUSED ENTRY POINT
- Full orchestrated loop with policy engine, action gateway, decision engine
- Entry: `scripts/run_agent.py` (CLI interactive menu)
- Uses `claude-agent-sdk` package
- Has prompt builder, audit logging, pause/resume
- **Currently NOT used in production** - the `core/` modules are mostly stubs (docstrings only)
- Only `core/actions/action_gateway.py`, `core/execution/command_executor.py`, `core/execution/execution_service.py`, `core/policy/policy_engine.py`, `core/llm/claude_adapter.py`, `core/orchestrator/victor_loop.py` have real implementations

## Key Files

### Production Files (used by XOC-API-AWS)
| File | Purpose |
|------|---------|
| `victor_server.py` | FastAPI server - THE entry point for XOC-API-AWS |
| `laptop_agent.py` | HTTP executor - runs shell commands on the host |
| `docker-compose.yml` | 2 services: victor-server (8000) + executor (8888) |
| `Dockerfile` | Python 3.11-slim, runs `victor_server.py` |
| `.env` / `.env.example` | ANTHROPIC_API_KEY, ANTHROPIC_MODEL, AZURE_ENDPOINT, EXECUTOR_URL |

### Deployment Scripts
| File | Purpose |
|------|---------|
| `scripts/deploy-appliance.sh` | Full deploy: prereqs, clone, .env, build, run, health check |
| `scripts/verify-appliance.sh` | 5 health checks: health, root, assessment, plan, executor |
| `scripts/test-malicious-ticket.sh` | End-to-end test for malicious file ticket flow |

### Core System (VictorLoop - not used in production yet)
| File | Status | Purpose |
|------|--------|---------|
| `core/orchestrator/victor_loop.py` | Implemented (1101 lines) | Main orchestration loop |
| `core/actions/action_gateway.py` | Implemented (655 lines) | Single enforcement boundary |
| `core/execution/command_executor.py` | Implemented (291 lines) | Low-level command execution |
| `core/execution/execution_service.py` | Implemented (648 lines) | Multi-executor router |
| `core/policy/policy_engine.py` | Implemented (553 lines) | C1/C2/C3 capability tiers |
| `core/llm/claude_adapter.py` | Implemented (700 lines) | Claude SDK bridge |
| `core/prompts/builders/prompt_builder.py` | Implemented (211 lines) | Prompt construction |
| `core/decisions/decision_engine.py` | Implemented (410 lines) | Human-in-the-loop decisions |
| `core/decisions/decision_builder.py` | Implemented (186 lines) | Decision payload builder |
| `core/actions/models.py` | Implemented (113 lines) | Action dataclass |
| `core/actions/registry.py` | Implemented (123 lines) | Action type registry |
| `core/contracts/action.py` | Implemented (121 lines) | Action contract |
| `core/contracts/event.py` | Implemented (51 lines) | Event contract |
| `core/tickets/*.py` | STUBS (1 line each) | Not implemented |
| `core/security/*.py` | STUBS (1 line each) | Not implemented |
| `core/state/*.py` | STUBS (1 line each) | Not implemented |
| `core/mcp/*.py` | STUBS (1 line each) | Not implemented |
| `core/observability/*.py` | STUBS (1 line each) | Not implemented |

### Configuration
| File | Purpose |
|------|---------|
| `config.dist/agent.yaml` | Agent identity, LLM settings (claude-sonnet-4-6, temp 0.0) |
| `config.dist/policy.yaml` | Default deny, human approval for destructive/privileged/http.write |
| `config.dist/capabilities.yaml` | C1: read.file/ticket.update, C2: write.file/http.get, C3: shell.exec |
| `config.dist/mcp.yaml` | Deny-by-default, 30s timeout |
| `config.dist/secrets.env.example` | ANTHROPIC_API_KEY template |

## XOC-API-AWS Connection (How it calls VICTOR)

### Endpoint Resolution (3-tier priority)
1. **Global**: `AGENTS_FUNCTION_BASE_URL` env var (prod: `http://44.222.129.186:8000`)
2. **Per-tenant**: RDS `tenant_runtime_settings` table (function_base_url field)
3. **Fallback**: Returns `canResolve: false` / empty plan

### HTTP Contract
XOC-API-AWS calls `POST /api/agents/VictorDurableAgent/run` with:

**Assessment phase:**
```json
{
  "phase": "assessment",
  "ticketId": "uuid",
  "tenantId": 123,
  "subject": "Ticket subject",
  "description": "Ticket description"
}
```
Response:
```json
{
  "canResolve": true,
  "confidence": 0.9,
  "assessment_type": "malware_remediation",
  "ticketId": "uuid",
  "tenantId": 123
}
```

**Plan phase:**
```json
{
  "phase": "plan",
  "ticketId": "uuid",
  "tenantId": 123,
  "subject": "Ticket subject",
  "description": "Ticket description"
}
```
Response:
```json
{
  "plan": [
    {"step_id": "uuid", "order": 1, "action": "shell", "command": "ls -la /tmp/trojan.sh", "description": "Check file exists", "risk_level": "basic"},
    {"order": 2, "action": "shell", "command": "mv /tmp/trojan.sh /var/quarantine/", "description": "Quarantine", "risk_level": "controlled"},
    {"order": 3, "action": "shell", "command": "rm -f /var/quarantine/trojan.sh", "description": "Delete", "risk_level": "risky"}
  ],
  "plan_summary": "Deteccion, cuarentena y eliminacion de archivo malicioso",
  "total_steps": 3,
  "source": "victor_claude"
}
```

**Execute phase:**
```json
{
  "phase": "execute",
  "ticketId": "uuid",
  "tenantId": 123,
  "plan": { ... }
}
```
Response:
```json
{
  "status": "completed",
  "all_success": true,
  "step_results": [
    {"order": 1, "command": "ls -la /tmp/trojan.sh", "success": true, "stdout": "...", "stderr": ""}
  ],
  "ticketId": "uuid",
  "tenantId": 123
}
```

### Risk Levels & Approval Flow
From `XOC-API-AWS/src/shared/risk_config.py`:
| Risk Level | Required Role | Approver Label |
|------------|---------------|----------------|
| `basic` | USER | Usuario |
| `controlled` | ADMIN | Admin del tenant |
| `risky` | ADMIN_XOC | Admin XOC |
| `critical` | SUPERADMIN | Superadmin XOC |

Steps with `risk_level: "risky"` or higher require human approval via `PATCH /tickets/{id}/approve` before execution.

### Step Functions Flow
```
Ticket Created → EventBridge → TicketWorkflow → StartAutomation
    → AssessTicketAutomation (HTTP to Victor)
    → CheckCanResolve
    → SearchSimilarCases
    → AssessTicketPlan (HTTP to Victor)
    → WaitForApproval (7-day timeout, taskToken pattern)
    → ExecuteTicketPlan (HTTP to Victor)
    → CheckTicketStatus
    → RegisterSuccessfulCase / RegisterFailedCase
```

## Malicious File Handling Flow

When a ticket mentions malware/virus/trojan/etc:

1. **Assessment**: Victor evaluates if it can resolve (usually `canResolve: true`)
2. **Plan**: Victor generates 4-phase plan:
   - FASE 1 - DETECTION (basic): `ls -la`, `file`, `head`, `ps aux | grep`, `netstat`
   - FASE 2 - CONTAINMENT (controlled): `mkdir -p /var/quarantine`, `mv <file> /var/quarantine/`
   - FASE 3 - REMEDIATION (risky): `rm -f /var/quarantine/<file>`
   - FASE 4 - VERIFICATION (basic): `ls -la`, verify no suspicious processes
3. **Approval**: If any step is "risky", workflow pauses for human approval
4. **Execute**: Victor runs each step via executor (laptop_agent.py)
5. **Verify**: Check ticket status → RESUELTO

## Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `ANTHROPIC_API_KEY` | Yes | - | Claude API key |
| `ANTHROPIC_MODEL` | No | `claude-sonnet-4-20250114` | Model to use |
| `AZURE_ENDPOINT` | No | `""` | Azure-hosted Claude URL (overrides Anthropic direct) |
| `AZURE_DEPLOYMENT` | No | `claude-sonnet-4-6` | Azure deployment name |
| `EXECUTOR_URL` | No | `http://localhost:8888` | Executor service URL |
| `PORT` | No | `8000` | FastAPI server port |
| `STAGE` | No | `prod` | Deployment stage |

## SSH Access

### EC2 (XOC-API-AWS deploys)
- IP: `13.223.193.42`
- User: `ubuntu`
- Key: `~/.ssh/xoc-ec2`
- Passphrase: `pepe123`

### RDS Dev VM
- IP: `3.235.129.140`
- User: `ubuntu`
- Key: same as EC2

Non-interactive SSH pattern:
```bash
printf '#!/bin/bash\necho "pepe123"\n' > /tmp/ssh-askpass.sh
chmod +x /tmp/ssh-askpass.sh
eval $(ssh-agent -s) > /dev/null
SSH_ASKPASS_REQUIRE=force SSH_ASKPASS=/tmp/ssh-askpass.sh ssh-add ~/.ssh/xoc-ec2 </dev/null 2>&1
ssh -A -o ConnectTimeout=20 -o ServerAliveInterval=15 ubuntu@<IP>
```

## Deploy Commands

### XOC-API-AWS (on EC2)
```bash
cd ~/XOC_AWS && git pull origin main
npm run deploy:automation:prod  # Victor integration
npm run deploy:tickets:prod     # Ticket workflow
```

### VICTOR On-Premise (on XOC APPLIANCE)
```bash
cd ~/victor-on-premise
git pull origin main
docker compose down
docker compose build --no-cache
docker compose up -d
./scripts/verify-appliance.sh
```

## Known Issues / TODO

1. **`core/` modules are mostly stubs**: `core/tickets/`, `core/security/`, `core/state/`, `core/mcp/`, `core/observability/` are all 1-line docstrings. The VictorLoop in `core/orchestrator/victor_loop.py` references these modules but they don't do anything.

2. **Two code paths**: `victor_server.py` (production) and `core/orchestrator/victor_loop.py` (unused) are separate implementations. They should eventually be unified.

3. **No authentication on Victor endpoint**: The `/api/agents/VictorDurableAgent/run` endpoint has no auth middleware. XOC-API-AWS sends a JWT token in the `Authorization` header but Victor doesn't validate it.

4. **`laptop_agent.py` runs commands with `shell=True`**: This is by design for the executor, but there's no sandboxing. The policy engine in `core/policy/policy_engine.py` exists but is not used by `victor_server.py`.

5. **DynamoDB update in `_handle_execute`**: The `victor_server.py` directly updates DynamoDB on success, which creates a tight coupling to AWS. This could fail if AWS credentials aren't configured.

6. **`EXPOSE ${PORT}` in Dockerfile**: The `ENV` variable expansion in `EXPOSE` may not work as expected. Should be `EXPOSE 8000`.

7. **`test-malicious-ticket.sh` step 4 bug**: The plan response variable is overwritten between step 2 and step 4, so step 4 may send an empty plan.

## Testing

```bash
# Local unit tests (core system)
pytest -q

# Deploy verification
./scripts/verify-appliance.sh

# Malicious file flow test
./scripts/test-malicious-ticket.sh --execute
```

## Git History (Recent)

```
4be9e6a Appliance_1.0          # Docker, deploy scripts, malicious file flow
c172fb4 Remediacion 1.0        # Previous remediacion work
08cdb9a sub skills con doc oficial de claude agent
2fe802b parche de los test ya funcionales
4ffaf2d requirements update
41287d4 subida proy
```
