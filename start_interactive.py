import os, json, subprocess, urllib.request

print("🔑 Obteniendo nuevo token del backend...")
req = urllib.request.Request(
    'https://txdxai-flask.replit.app/api/auth/login',
    data=b'{"email":"adminaenza@aenza.com","password":"pepe123"}',
    headers={'Content-Type': 'application/json'},
    method='POST'
)

try:
    with urllib.request.urlopen(req) as response:
        token = json.loads(response.read().decode())['access_token']
        print("✅ Token obtenido exitosamente.")
except Exception as e:
    print(f"❌ Error al obtener token: {e}")
    exit(1)

print("📝 Actualizando ticket_remediate_tls.json...")
ticket_path = 'ticket_remediate_tls.json'
with open(ticket_path, 'r+') as f:
    d = json.load(f)
    d['ticket_api']['auth_token'] = token
    f.seek(0)
    json.dump(d, f, indent=2)
    f.truncate()

print("🚀 Lanzando VICTOR en modo interactivo...")
# Lanzamos el proceso interactivamente, sin capturar salida, 
# para que el usuario pueda jugar con la terminal.
subprocess.run(
    [r'.\.venv\Scripts\python.exe', r'.\scripts\run_agent.py', '--ticket-file', ticket_path, '--pretty', '--no-menu']
)
