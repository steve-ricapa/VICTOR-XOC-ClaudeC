#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

if command -v python >/dev/null 2>&1; then
  PYTHON_CMD="python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD="python3"
else
  echo "Error: Python no esta instalado o no esta en PATH."
  exit 1
fi

if [ ! -d "${VENV_DIR}" ]; then
  echo "[1/5] Creando entorno virtual en .venv"
  "${PYTHON_CMD}" -m venv "${VENV_DIR}"
fi

if [ -x "${VENV_DIR}/Scripts/python.exe" ]; then
  VENV_PY="${VENV_DIR}/Scripts/python.exe"
elif [ -x "${VENV_DIR}/Scripts/python" ]; then
  VENV_PY="${VENV_DIR}/Scripts/python"
elif [ -x "${VENV_DIR}/bin/python" ]; then
  VENV_PY="${VENV_DIR}/bin/python"
else
  echo "Error: No se encontro el interprete de Python dentro de .venv"
  exit 1
fi

echo "[2/5] Actualizando pip"
"${VENV_PY}" -m pip install --upgrade pip

echo "[3/5] Instalando dependencias"
"${VENV_PY}" -m pip install -r "${ROOT_DIR}/requirements.txt"

echo "[4/5] Preparando configuracion base"
mkdir -p "${ROOT_DIR}/config/secrets"
for file in agent policy capabilities mcp; do
  if [ ! -f "${ROOT_DIR}/config/${file}.yaml" ] && [ -f "${ROOT_DIR}/config.dist/${file}.yaml" ]; then
    cp "${ROOT_DIR}/config.dist/${file}.yaml" "${ROOT_DIR}/config/${file}.yaml"
  fi
done

if [ ! -f "${ROOT_DIR}/config/secrets/.env" ]; then
  touch "${ROOT_DIR}/config/secrets/.env"
fi

echo "[5/5] Iniciando VICTOR Agent"
exec "${VENV_PY}" "${ROOT_DIR}/scripts/run_agent.py" "$@"
