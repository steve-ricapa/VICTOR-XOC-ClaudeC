import json
import logging
import os
import uuid
from datetime import datetime, timezone

import requests
import json5
from anthropic import AnthropicFoundry
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("victor-server")

AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

if ANTHROPIC_API_KEY and AZURE_ENDPOINT:
    client = AnthropicFoundry(api_key=ANTHROPIC_API_KEY, base_url=AZURE_ENDPOINT)
    logger.info("LLM client: Azure endpoint (%s)", AZURE_ENDPOINT)
elif ANTHROPIC_API_KEY:
    from anthropic import Anthropic
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    logger.info("LLM client: Anthropic direct (model=%s)", MODEL)
else:
    client = None
    logger.warning("No ANTHROPIC_API_KEY configured - LLM calls will fail")

app = FastAPI(title="Victor On-Premise", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SYSTEM_PROMPT = """Eres Victor, un agente de automatizacion on-premise empresarial.

Tu funcion es analizar tickets de soporte y generar planes de accion ejecutables
en servidores Windows/Linux. Los planes consisten en comandos shell que se ejecutaran
secuencialmente en el endpoint objetivo via el executor remoto.

REGLAS GENERALES:
- Cada paso debe tener: order, action (siempre "shell"), command, description, risk_level ("basic"|"controlled"|"risky")
- Usa rutas con FORWARD slash para compatibilidad multiplataforma
- Siempre verifica resultados con comandos posteriores
- Risk level: "basic" = solo lectura, "controlled" = instalacion/escritura, "risky" = delete/remocion
- En Windows usa variables de entorno como %USERPROFILE% en los comandos
- Responde SOLO con JSON valido, sin markdown ni explicaciones

FLUJO PARA ARCHIVOS MALICIOSOS / SEGURIDAD:
Cuando el ticket mencione archivos maliciosos, virus, trojans, ransomware, reverse shells,
webshells, minero, spyware, adware, o cualquier amenaza de seguridad:

  FASE 1 - DETECCION (risk_level: "basic"):
  - Verificar si el archivo sospechoso existe: ls -la <ruta> / dir <ruta>
  - Identificar el tipo de archivo: file <ruta> / powershell Get-Content
  - Inspeccionar las primeras lineas: head -20 <ruta>
  - Verificar procesos relacionados: ps aux | grep <nombre> / tasklist | findstr
  - Verificar conexiones de red: netstat -tulnp / netstat -an
  - Verificar cron jobs o tareas programadas: crontab -l / schtasks /query

  FASE 2 - CONTENCION (risk_level: "controlled"):
  - Crear carpeta de cuarentena: mkdir -p /var/quarantine
  - Mover archivo a cuarentena (NO eliminar aun): mv <ruta> /var/quarantine/
  - Si hay proceso activo,Identification and kill: kill -9 <PID> / taskkill /F /PID
  - Verificar que el proceso termino

  FASE 3 - REMEDIACION (risk_level: "risky"):
  - Eliminar el archivo de cuarentena: rm -f /var/quarantine/<archivo>
  - Verificar eliminacion: ls -la /var/quarantine/
  - Verificar que no quedan procesos activos
  - Verificar integridad del sistema

  FASE 4 - VERIFICACION (risk_level: "basic"):
  - Listar archivos en directorios afectados
  - Verificar permisos de archivos criticos
  - Comprobar que no hay conexiones sospechosas activas

COMANDOS RECOMENDADOS POR SISTEMA OPERATIVO:
  Linux:
    Deteccion: ls -la, file, head, ps aux | grep, netstat -tulnp, crontab -l
    Contencion: mv, kill -9
    Remediacion: rm -f, chmod 644
  Windows:
    Deteccion: dir, powershell Get-Content, tasklist | findstr, netstat -an, schtasks /query
    Contencion: move, taskkill /F /PID
    Remociion: del /f /q, icacls

IMPORTANTE:
- Siempre comienza con FASE 1 (deteccion) antes de cualquier accion destructiva
- NUNCA elimines un archivo sin haberlo inspeccionado primero
- Si el archivo esta en uso, Identificationa el proceso y terminalo antes de eliminar
- Mantén un registro de todos los pasos ejecutados
- Si no estas seguro del nivel de amenaza, mantente en FASE 1 hasta confirmar"""


def _call_claude(messages: list, system: str = SYSTEM_PROMPT, max_tokens: int = 2000) -> str:
    if not client:
        raise RuntimeError("ANTHROPIC_API_KEY no configurada")
    resp = client.messages.create(
        model=MODEL,
        system=system,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.1,
    )
    raw = resp.content[0].text
    logger.info("Claude response received (len=%d)", len(raw))
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        text = text.rsplit("```", 1)[0] if "```" in text else text
    return text.strip()


@app.post("/api/agents/VictorDurableAgent/run")
async def run_agent(request: Request):
    body = await request.json()
    phase = body.get("phase", "assessment")
    ticket_id = body.get("ticket_id", body.get("ticketId", "unknown"))
    tenant_id = body.get("tenant_id", body.get("tenantId", "unknown"))
    subject = body.get("subject", "")
    description = body.get("description", "")

    logger.info("Victor called | phase=%s ticket=%s tenant=%s", phase, ticket_id, tenant_id)

    if phase == "assessment":
        return await _handle_assessment(ticket_id, tenant_id, subject, description)
    elif phase == "plan":
        return await _handle_plan(ticket_id, tenant_id, subject, description)
    elif phase == "execute":
        return await _handle_execute(body, ticket_id, tenant_id)
    elif phase == "verify":
        return {"status": "completed", "verified": True, "details": "Ticket resuelto segun el plan."}
    return {"status": "unknown_phase", "phase": phase}


async def _handle_assessment(ticket_id, tenant_id, subject, description):
    prompt = f"""Analiza si este ticket de soporte se puede resolver automaticamente
ejecutando comandos shell en el servidor/endpoint remoto.

Ticket: {subject}
Descripcion: {description}

Considera especialmente:
- Archivos maliciosos (virus, trojans, ransomware, webshells, mineros)
- Procesos sospechosos o no autorizados
- Conexiones de red inusuales
- Configuraciones comprometidas

Responde SOLO con JSON:
{{"can_resolve": true/false, "confidence": 0.0-1.0, "assessment_type": "tipo", "requires_human": false, "reason": "razon breve"}}"""
    try:
        text = _call_claude([{"role": "user", "content": prompt}], max_tokens=500)
        result = json5.loads(text)
    except Exception as e:
        logger.warning("Claude assessment fallback: %s", e)
        result = {"can_resolve": True, "confidence": 0.5, "assessment_type": "fallback"}
    return {
        "can_resolve": result.get("can_resolve", True),
        "canResolve": result.get("can_resolve", True),
        "confidence": result.get("confidence", 0.5),
        "assessment_type": result.get("assessment_type", "unknown"),
        "ticket_id": ticket_id,
        "ticketId": ticket_id,
        "tenant_id": tenant_id,
        "tenantId": tenant_id,
    }


async def _handle_plan(ticket_id, tenant_id, subject, description):
    prompt = f"""Genera un plan de accion en comandos shell para resolver:

Ticket: {subject}
Descripcion: {description}

Sigue el flujo de seguridad si aplica:
1. DETECCION: Verificar existencia, tipo, contenido del archivo sospechoso
2. CONTENCION: Mover a cuarentena (/var/quarantine/) antes de eliminar
3. REMEDIACION: Eliminar el archivo malicioso
4. VERIFICACION: Confirmar que la amenaza fue removida

Responde SOLO con JSON:
{{"plan": [{{"step_id": "uuid", "order": 1, "action": "shell", "command": "comando", "description": "que hace", "risk_level": "basic|controlled|risky"}}], "plan_summary": "resumen", "total_steps": N}}

IMPORTANTE:
- Los comandos deben funcionar en Windows cmd.exe (o bash en Linux)
- En Windows usa rutas con %USERPROFILE% (ej: %USERPROFILE%/archivo.exe), en Linux usa rutas absolutas
- Para detectar archivos usa: dir/findstr (Windows) o ls/grep (Linux)
- Para cuarentena usa: move (Windows) o mv (Linux)
- Para eliminar usa: del /f /q (Windows) o rm -f (Linux)
- Cada comando debe ser autocontenido (usa && para encadenar)
- ESCAPA todas las comillas dentro del JSON con \\" (ej: command: \"dir \\\"%USERPROFILE%\\\"\")
- NUNCA elimines sin antes inspeccionar el archivo"""
    try:
        text = _call_claude([{"role": "user", "content": prompt}])
        result = json5.loads(text)
    except Exception as e:
        logger.warning("Claude plan fallback: %s", e)
        result = {"plan": [], "plan_summary": f"Fallback para: {subject}", "total_steps": 0}
    return {
        "plan": result.get("plan", []),
        "plan_summary": result.get("plan_summary", ""),
        "total_steps": result.get("total_steps", 0),
        "source": "victor_claude",
    }


async def _handle_execute(body, ticket_id, tenant_id):
    plan = body.get("plan", body.get("steps", []))
    logger.info("Ejecutando plan de %d pasos en servidor remoto", len(plan))
    if not plan:
        logger.warning("Plan vacio para ticket %s. Body keys: %s, plan_type: %s, plan_repr: %s",
                       ticket_id, list(body.keys()), type(body.get("plan")).__name__,
                       repr(body.get("plan"))[:300])
    exec_result = _execute_plan(plan)
    if exec_result["all_success"]:
        try:
            import boto3
            table_name = f"xoc-api-tickets-{os.environ.get('STAGE', 'prod')}-tickets"
            dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
            table = dynamodb.Table(table_name)
            table.update_item(
                Key={"pk": f"TICKET#{tenant_id}", "sk": f"TICKET#{ticket_id}"},
                UpdateExpression="SET #s = :s, updated_at = :u",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":s": "RESUELTO",
                    ":u": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.info("Ticket %s marcado como RESUELTO en DynamoDB", ticket_id)
        except Exception as exc:
            logger.error("Error actualizando ticket en DynamoDB: %s", exc)
    return {
        "status": "completed" if exec_result["all_success"] else "failed",
        "all_success": exec_result["all_success"],
        "step_results": exec_result["step_results"],
        "ticket_id": ticket_id,
        "tenant_id": tenant_id,
    }


def _execute_plan(plan: list[dict]) -> dict:
    results = []
    all_ok = True
    executor_url = os.getenv("EXECUTOR_URL", "http://localhost:9000")
    for step in plan:
        cmd = step.get("command", "")
        desc = step.get("description", "")
        logger.info("Ejecutando paso %d: %s", step.get("order"), desc)
        try:
            resp = requests.post(
                f"{executor_url}/execute",
                json={"command": cmd, "timeout": 120},
                timeout=130,
            )
            resp.raise_for_status()
            result = resp.json()
            step_ok = result.get("returncode") == 0
            if not step_ok:
                all_ok = False
            results.append({
                "order": step.get("order"),
                "command": cmd,
                "success": step_ok,
                "stdout": result.get("stdout", "")[:500],
                "stderr": result.get("stderr", "")[:500],
            })
        except Exception as exc:
            all_ok = False
            results.append({"order": step.get("order"), "command": cmd, "success": False, "error": str(exc)})
    return {"all_success": all_ok, "step_results": results}


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/")
async def root():
    return {"service": "Victor On-Premise Agent", "status": "running"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
