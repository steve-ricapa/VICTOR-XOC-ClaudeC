import json
import logging
import os
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("laptop-agent")

HOST = os.environ.get("AGENT_HOST", "0.0.0.0")
PORT = int(os.environ.get("AGENT_PORT", "8888"))

_PYTHON_DIR = os.path.dirname(sys.executable)
_SCRIPTS_DIR = os.path.join(_PYTHON_DIR, "Scripts")
_NODE_DIRS = [
    r"C:\Program Files\nodejs",
    os.path.expandvars(r"%APPDATA%\npm"),
    os.path.expandvars(r"%USERPROFILE%\AppData\Roaming\npm"),
]
_EXTRA_PATHS = [_PYTHON_DIR, _SCRIPTS_DIR] + _NODE_DIRS


class AgentHandler(BaseHTTPRequestHandler):
    def _respond(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        if self.path == "/health":
            return self._respond(200, {"status": "ok", "hostname": os.environ.get("COMPUTERNAME", "unknown")})
        self._respond(404, {"error": "not_found"})

    def do_POST(self):
        if self.path == "/execute":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode()) if length else {}
            cmd = body.get("command", "")
            if not cmd:
                return self._respond(400, {"error": "command is required"})
            try:
                logger.info("Executing: %s", cmd)
                env = os.environ.copy()
                existing = env.get("PATH", "")
                extra = os.pathsep.join(p for p in _EXTRA_PATHS if os.path.isdir(p))
                env["PATH"] = f"{extra}{os.pathsep}{existing}"
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=int(body.get("timeout", 120)),
                    env=env,
                )
                return self._respond(200, {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                })
            except subprocess.TimeoutExpired:
                return self._respond(504, {"error": "command timed out"})
            except Exception as e:
                return self._respond(500, {"error": str(e)})
        self._respond(404, {"error": "not_found"})

    def log_message(self, fmt, *args):
        logger.info(" %s - %s", self.client_address[0], fmt % args)


if __name__ == "__main__":
    server = HTTPServer((HOST, PORT), AgentHandler)
    logger.info("Laptop agent listening on %s:%s", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.shutdown()
