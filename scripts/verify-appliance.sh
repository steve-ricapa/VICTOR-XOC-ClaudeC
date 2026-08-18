#!/usr/bin/env bash
# =============================================================================
# VICTOR On-Premise - Post-Deploy Verification
# =============================================================================
# Usage: ./scripts/verify-appliance.sh [--host HOST] [--port PORT]
# =============================================================================
set -euo pipefail

HOST="localhost"
PORT=8000

while [[ $# -gt 0 ]]; do
  case $1 in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

BASE_URL="http://${HOST}:${PORT}"
PASS=0
FAIL=0

check() {
  local name="$1"
  local result="$2"
  if [ "$result" = "OK" ]; then
    echo "  [PASS] ${name}"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] ${name}: ${result}"
    FAIL=$((FAIL + 1))
  fi
}

echo "============================================="
echo "  VICTOR On-Premise - Verification"
echo "============================================="
echo "  Target: ${BASE_URL}"
echo ""

# --- Test 1: Health endpoint ---
echo "[1/5] Health endpoint..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/health" --connect-timeout 5 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
  HEALTH=$(curl -s "${BASE_URL}/health" 2>/dev/null)
  check "Health endpoint returns 200" "OK"
  echo "       Response: ${HEALTH}"
else
  check "Health endpoint returns 200" "HTTP ${HTTP_CODE}"
fi

# --- Test 2: Root endpoint ---
echo "[2/5] Root endpoint..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/" --connect-timeout 5 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
  check "Root endpoint returns 200" "OK"
else
  check "Root endpoint returns 200" "HTTP ${HTTP_CODE}"
fi

# --- Test 3: Assessment phase (dry run) ---
echo "[3/5] Assessment phase..."
RESPONSE=$(curl -s -X POST "${BASE_URL}/api/agents/VictorDurableAgent/run" \
  -H "Content-Type: application/json" \
  -d '{
    "phase": "assessment",
    "ticket_id": "verify-001",
    "tenant_id": 1,
    "subject": "Verificacion de sistema - ticket de prueba",
    "description": "Ticket de verificacion post-deploy. Solo lectura."
  }' --connect-timeout 10 --max-time 30 2>/dev/null || echo '{"error":"connection_failed"}')

if echo "$RESPONSE" | grep -q '"canResolve"'; then
  check "Assessment phase returns canResolve" "OK"
  echo "       canResolve: $(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('canResolve','?'))" 2>/dev/null || echo '?')"
else
  check "Assessment phase returns canResolve" "Response: ${RESPONSE:0:200}"
fi

# --- Test 4: Plan phase (dry run) ---
echo "[4/5] Plan phase..."
RESPONSE=$(curl -s -X POST "${BASE_URL}/api/agents/VictorDurableAgent/run" \
  -H "Content-Type: application/json" \
  -d '{
    "phase": "plan",
    "ticket_id": "verify-001",
    "tenant_id": 1,
    "subject": "Verificacion de sistema - ticket de prueba",
    "description": "Ticket de verificacion post-deploy. Solo listar archivos del directorio actual."
  }' --connect-timeout 10 --max-time 60 2>/dev/null || echo '{"error":"connection_failed"}')

if echo "$RESPONSE" | grep -q '"plan"'; then
  check "Plan phase returns plan" "OK"
  STEPS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total_steps',0))" 2>/dev/null || echo '0')
  echo "       total_steps: ${STEPS}"
else
  check "Plan phase returns plan" "Response: ${RESPONSE:0:200}"
fi

# --- Test 5: Executor health ---
echo "[5/5] Executor health..."
EXECUTOR_URL="${EXECUTOR_URL:-http://localhost:8888}"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${EXECUTOR_URL}/health" --connect-timeout 5 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
  check "Executor is healthy" "OK"
else
  check "Executor is healthy" "HTTP ${HTTP_CODE} (${EXECUTOR_URL})"
fi

# --- Summary ---
echo ""
echo "============================================="
echo "  Results: ${PASS} passed, ${FAIL} failed"
echo "============================================="

if [ $FAIL -gt 0 ]; then
  echo ""
  echo "  Troubleshooting:"
  echo "  - Check docker compose logs victor-server"
  echo "  - Check docker compose logs executor"
  echo "  - Verify ANTHROPIC_API_KEY is set in .env"
  exit 1
fi

echo ""
echo "  All checks passed. Victor On-Premise is ready."
