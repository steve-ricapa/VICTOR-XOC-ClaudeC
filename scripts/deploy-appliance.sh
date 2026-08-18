#!/usr/bin/env bash
# =============================================================================
# VICTOR On-Premise - Deploy to XOC APPLIANCE
# =============================================================================
# Usage:
#   ./scripts/deploy-appliance.sh [--update] [--port PORT]
#
# Flags:
#   --update    Pull latest code before deploying (default: skip pull)
#   --port      Port for Victor server (default: 8000)
# =============================================================================
set -euo pipefail

REPO_URL="https://github.com/steve-ricapa/VICTOR-XOC-ClaudeC.git"
INSTALL_DIR="${HOME}/victor-on-premise"
PORT=8000
UPDATE=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --update) UPDATE=true; shift ;;
    --port) PORT="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "============================================="
echo "  VICTOR On-Premise - XOC APPLIANCE DEPLOY"
echo "============================================="
echo ""

# --- Prerequisites check ---
echo "[1/7] Checking prerequisites..."

if ! command -v docker &>/dev/null; then
  echo "ERROR: Docker is not installed."
  echo "Install: curl -fsSL https://get.docker.com | sh"
  exit 1
fi

if ! docker compose version &>/dev/null && ! docker-compose version &>/dev/null; then
  echo "ERROR: Docker Compose is not installed."
  echo "Install: sudo apt-get install docker-compose-plugin"
  exit 1
fi

echo "  Docker: $(docker --version)"
echo "  Compose: $(docker compose version 2>/dev/null || docker-compose version 2>/dev/null)"
echo ""

# --- Clone or update repo ---
echo "[2/7] Preparing repository..."

if [ -d "${INSTALL_DIR}/.git" ]; then
  echo "  Repository already exists at ${INSTALL_DIR}"
  if [ "$UPDATE" = true ]; then
    echo "  Pulling latest changes..."
    cd "${INSTALL_DIR}"
    git pull origin main
  fi
else
  echo "  Cloning repository..."
  git clone "${REPO_URL}" "${INSTALL_DIR}"
  cd "${INSTALL_DIR}"
fi

echo ""

# --- Configure .env ---
echo "[3/7] Configuring environment..."

if [ ! -f "${INSTALL_DIR}/.env" ]; then
  echo "  Creating .env from template..."
  cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
  echo ""
  echo "  IMPORTANT: Edit ${INSTALL_DIR}/.env and set your ANTHROPIC_API_KEY"
  echo "  Then re-run this script."
  echo ""
  exit 0
fi

# Validate ANTHROPIC_API_KEY
source "${INSTALL_DIR}/.env" 2>/dev/null || true
if [ -z "${ANTHROPIC_API_KEY:-}" ] || [ "${ANTHROPIC_API_KEY:-}" = "replace_with_real_key" ]; then
  echo "  ERROR: ANTHROPIC_API_KEY is not set in ${INSTALL_DIR}/.env"
  echo "  Edit the file and set a valid key."
  exit 1
fi

echo "  ANTHROPIC_API_KEY: ...${ANTHROPIC_API_KEY: -6}"
echo "  ANTHROPIC_MODEL: ${ANTHROPIC_MODEL:-claude-sonnet-4-20250514}"
echo "  PORT: ${PORT}"
echo ""

# --- Build images ---
echo "[4/7] Building Docker images..."
cd "${INSTALL_DIR}"
docker compose build --no-cache
echo ""

# --- Stop existing containers ---
echo "[5/7] Stopping existing containers..."
docker compose down --remove-orphans 2>/dev/null || true
echo ""

# --- Start services ---
echo "[6/7] Starting Victor On-Premise services..."
PORT=${PORT} docker compose up -d
echo ""

# --- Health check ---
echo "[7/7] Verifying deployment..."
sleep 5

MAX_RETRIES=10
RETRY=0
while [ $RETRY -lt $MAX_RETRIES ]; do
  if curl -sf "http://localhost:${PORT}/health" >/dev/null 2>&1; then
    echo "  Victor server: HEALTHY"
    break
  fi
  RETRY=$((RETRY + 1))
  echo "  Waiting for Victor server... (${RETRY}/${MAX_RETRIES})"
  sleep 3
done

if [ $RETRY -ge $MAX_RETRIES ]; then
  echo "  ERROR: Victor server failed to start. Check logs:"
  echo "  docker compose logs victor-server"
  exit 1
fi

# Check executor
if curl -sf "http://localhost:8888/health" >/dev/null 2>&1; then
  echo "  Executor: HEALTHY"
else
  echo "  WARNING: Executor not responding on port 8888"
fi

echo ""
echo "============================================="
echo "  DEPLOYMENT COMPLETE"
echo "============================================="
echo ""
echo "  Victor Server: http://localhost:${PORT}"
echo "  Health Check:  http://localhost:${PORT}/health"
echo "  Executor:      http://localhost:8888"
echo ""
echo "  Logs: docker compose logs -f"
echo "  Stop:  docker compose down"
echo ""
echo "  Next: Configure XOC-API-AWS to point to this server"
echo "  Update AGENTS_FUNCTION_BASE_URL in serverless/stages/prod.yml"
echo "  Or set per-tenant in RDS tenant_runtime_settings table"
echo ""
