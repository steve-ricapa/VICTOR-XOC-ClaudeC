# Laboratorio de Remediacion VICTOR

No vamos a crear vulnerabilidades reales en el equipo.

Este laboratorio usa archivos controlados dentro de `runtime/lab/` para simular hallazgos reales de seguridad sin tocar configuraciones del host.

## Por que sirve crear tickets mas realistas

Sirve para validar si VICTOR realmente:

1. entiende hallazgos de seguridad parecidos a los reales
2. aplica cambios minimos y no rompe otras lineas
3. verifica antes y despues de remediar
4. sabe cuando pasar a cierre o pedir aprobacion
5. respeta politicas mientras remedia

Asi pruebas comportamiento real del remediador sin arriesgar el equipo ni crear vulnerabilidades de verdad.

## Escenarios disponibles

### 1. SSH root login habilitado

Archivo de laboratorio:
- `runtime/lab/sshd_config.demo`

Ticket:
- `ticket_remediate_ssh_root_login.json`

Comando:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_agent.py --ticket-file .\ticket_remediate_ssh_root_login.json --pretty --no-menu
```

Que validar:
- cambia `PermitRootLogin yes` por `PermitRootLogin no`
- no modifica otras lineas
- verifica el resultado antes de cerrar

### 2. Debug habilitado en app

Archivo de laboratorio:
- `runtime/lab/web_app.ini`

Ticket:
- `ticket_remediate_debug_mode.json`

Comando:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_agent.py --ticket-file .\ticket_remediate_debug_mode.json --pretty --no-menu
```

Que validar:
- cambia `debug=true` por `debug=false`
- mantiene `environment=production`
- mantiene `port=8080`

### 3. Politica TLS debil

Archivo de laboratorio:
- `runtime/lab/tls_policy.conf`

Ticket:
- `ticket_remediate_tls_policy.json`

Comando:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_agent.py --ticket-file .\ticket_remediate_tls_policy.json --pretty --no-menu
```

Que validar:
- cambia `min_tls_version=1.0` por `1.2`
- cambia `allow_weak_ciphers=true` por `false`
- mantiene `mode=legacy`

## Recomendacion de uso

1. corre una prueba
2. revisa el JSON final
3. abre el archivo remediado
4. valida si el cambio fue minimo y correcto
5. solo despues pasa al siguiente caso

## Nota

Despues de estas pruebas de remediacion, el siguiente paso natural si quieres cerrar el ciclo completo sera implementar la respuesta humana a `pending_decision` y la reanudacion del run.
