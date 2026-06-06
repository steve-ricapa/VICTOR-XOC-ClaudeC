import os, json, subprocess, urllib.request, re

print("Obteniendo nuevo token...")
req = urllib.request.Request(
    'https://txdxai-flask.replit.app/api/auth/login',
    data=b'{"email":"adminaenza@aenza.com","password":"pepe123"}',
    headers={'Content-Type': 'application/json'},
    method='POST'
)
with urllib.request.urlopen(req) as response:
    token = json.loads(response.read().decode())['access_token']

print("Actualizando ticket_remediate_debug_mode.json...")
f = open('ticket_remediate_debug_mode.json', 'r+')
d = json.load(f)
d['ticket_api']['auth_token'] = token
f.seek(0)
json.dump(d, f, indent=2)
f.truncate()
f.close()

print("Ejecutando agente (fase 1: evaluacion)...")
p = subprocess.run(
    [r'.\.venv\Scripts\python.exe', r'.\scripts\run_agent.py', '--ticket-file', r'.\ticket_remediate_debug_mode.json', '--pretty', '--no-menu'],
    capture_output=True, text=True
)

match = re.search(r'"decision_id":\s*"([^"]+)"', p.stdout)
if match:
    did = match.group(1)
    print("Decision ID generada:", did)
    print("Aprobando accion HTTP...")
    p2 = subprocess.run(
        [r'.\.venv\Scripts\python.exe', r'.\scripts\run_agent.py', '--decision-id', did, '--decision-option', 'A', '--decision-actor', 'diego', '--decision-comment', 'Auto-aprobado', '--pretty', '--no-menu'],
        capture_output=True, text=True
    )
    if "COMPLETED" in p2.stdout or "RESUELTO" in p2.stdout or '"status": "SUCCESS"' in p2.stdout or '"execution_status": "COMPLETED"' in p2.stdout:
        print("\n=== EXITO: El ticket fue cerrado correctamente en el backend ===")
    else:
        print("\n=== RESULTADO DEL CIERRE ===")
        print(p2.stdout[-1500:])
else:
    print("No se encontro un Decision ID. Salida:")
    print(p.stdout)
