#!/usr/bin/env bash
# =============================================================================
# VICTOR On-Premise - Test Malicious File Ticket Flow
# =============================================================================
# Simulates the full flow: assessment → plan → (manual approve) → execute
# Usage: ./scripts/test-malicious-ticket.sh [--host HOST] [--port PORT] [--execute]
# =============================================================================
set -euo pipefail

HOST="localhost"
PORT=8000
EXECUTE=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --execute) EXECUTE=true; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

BASE_URL="http://${HOST}:${PORT}"
TICKET_ID="test-malicious-$(date +%s)"
TENANT_ID=1

echo "============================================="
echo "  VICTOR - Malicious File Ticket Test"
echo "============================================="
echo "  Ticket ID: ${TICKET_ID}"
echo "  Target:    ${BASE_URL}"
echo "  Execute:   ${EXECUTE}"
echo ""

# --- Step 1: Assessment ---
echo "[Step 1/4] Assessment - Can Victor resolve this?"
RESPONSE=$(curl -s -X POST "${BASE_URL}/api/agents/VictorDurableAgent/run" \
  -H "Content-Type: application/json" \
  -d "{
    \"phase\": \"assessment\",
    \"ticket_id\": \"${TICKET_ID}\",
    \"tenant_id\": ${TENANT_ID},
    \"subject\": \"Archivo malicioso detectado en /tmp\",
    \"description\": \"Se detecto un archivo sospechoso /tmp/trojan.sh ejecutando comandos de reverse shell. El archivo fue creado hace 2 horas y tiene permisos de ejecucion. Se sospecha que fue subido via una vulnerabilidad en la aplicacion web.\"
  }" --connect-timeout 10 --max-time 30 2>/dev/null)

echo "  Response:"
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"

CAN_RESOLVE=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('canResolve', False))" 2>/dev/null || echo "False")
echo ""
echo "  canResolve: ${CAN_RESOLVE}"

if [ "$CAN_RESOLVE" != "True" ]; then
  echo ""
  echo "  Victor says it cannot resolve this ticket. Stopping."
  exit 0
fi

# --- Step 2: Plan ---
echo ""
echo "[Step 2/4] Plan - Generate remediation steps..."
RESPONSE=$(curl -s -X POST "${BASE_URL}/api/agents/VictorDurableAgent/run" \
  -H "Content-Type: application/json" \
  -d "{
    \"phase\": \"plan\",
    \"ticket_id\": \"${TICKET_ID}\",
    \"tenant_id\": ${TENANT_ID},
    \"subject\": \"Archivo malicioso detectado en /tmp\",
    \"description\": \"Se detecto un archivo sospechoso /tmp/trojan.sh ejecutando comandos de reverse shell. El archivo fue creado hace 2 horas y tiene permisos de ejecucion. Se sospecha que fue subido via una vulnerabilidad en la aplicacion web.\"
  }" --connect-timeout 10 --max-time 60 2>/dev/null)

echo "  Response:"
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"

TOTAL_STEPS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total_steps', 0))" 2>/dev/null || echo "0")
echo ""
echo "  total_steps: ${TOTAL_STEPS}"

if [ "$TOTAL_STEPS" = "0" ]; then
  echo ""
  echo "  Plan has 0 steps. Something went wrong."
  exit 1
fi

# --- Step 3: Approval (manual) ---
echo ""
echo "[Step 3/4] Approval"
echo "  The plan requires manual approval before execution."
echo "  In production, this would be handled by xoc.app PATCH /tickets/{id}/approve"
echo ""
echo "  Waiting for approval... (Ctrl+C to skip execution)"

if [ "$EXECUTE" = false ]; then
  echo ""
  echo "  Skipping execution. Use --execute to run the plan."
  echo "  To execute manually:"
  echo "    curl -X POST ${BASE_URL}/api/agents/VictorDurableAgent/run \\"
  echo "      -H 'Content-Type: application/json' \\"
  echo "      -d '{\"phase\":\"execute\",\"ticket_id\":\"${TICKET_ID}\",\"tenant_id\":${TENANT_ID},...}'"
  exit 0
fi

# --- Step 4: Execute ---
echo ""
echo "[Step 4/4] Execute - Running remediation plan..."
RESPONSE=$(curl -s -X POST "${BASE_URL}/api/agents/VictorDurableAgent/run" \
  -H "Content-Type: application/json" \
  -d "{
    \"phase\": \"execute\",
    \"ticket_id\": \"${TICKET_ID}\",
    \"tenant_id\": ${TENANT_ID},
    \"plan\": $(echo "$RESPONSE" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin).get('plan',{})))" 2>/dev/null || echo '{}')
  }" --connect-timeout 10 --max-time 120 2>/dev/null)

echo "  Response:"
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"

ALL_SUCCESS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('all_success', False))" 2>/dev/null || echo "False")
echo ""
echo "  all_success: ${ALL_SUCCESS}"

echo ""
echo "============================================="
if [ "$ALL_SUCCESS" = "True" ]; then
  echo "  TEST PASSED - Malicious file remediated"
else
  echo "  TEST COMPLETED - Check results above"
fi
echo "============================================="
