import json
import logging
from typing import Any, Mapping

COLORS = {
    "RESET": "\033[0m",
    "CYAN": "\033[36m",
    "GREEN": "\033[32m",
    "YELLOW": "\033[33m",
    "RED": "\033[31m",
    "MAGENTA": "\033[35m",
    "BLUE": "\033[34m",
    "WHITE": "\033[37m",
    "BOLD": "\033[1m",
    "DIM": "\033[2m",
}

class CLIAuditLogger:
    """
    Logger visual elegante y serio para la terminal.
    Interpreta los eventos del orquestador y los presenta con estilo hacker/corporativo.
    """
    def __init__(self, debug_mode: bool = False):
        self.debug_mode = debug_mode

    def log_event(self, event: Mapping[str, Any]) -> None:
        if not isinstance(event, Mapping):
            return

        phase = str(event.get("phase", "UNKNOWN")).upper()
        message = str(event.get("message", ""))
        action_id = event.get("action_id", "")
        metadata = event.get("metadata", {})
        
        # Ignorar ruido interno si no estamos en debug profundo
        if not self.debug_mode and phase in ["PROMPT_SENT", "RESPONSE_RECEIVED"]:
            return

        print(f"\n{COLORS['DIM']}[{phase}]{COLORS['RESET']} ", end="")

        if phase == "PLAN":
            action_type = metadata.get("action_type")
            reason = metadata.get("reason", "")
            
            if "Action parsed" in message and action_type:
                print(f"{COLORS['CYAN']}Intent detected:{COLORS['RESET']} {action_type}")
                if reason:
                    print(f"       {COLORS['DIM']}Reason:{COLORS['RESET']} {reason}")
            else:
                print(f"{COLORS['CYAN']}{message}{COLORS['RESET']}")
                
        elif phase == "POLICY":
            if "decision" in message.lower() or "paused" in message.lower():
                print(f"{COLORS['YELLOW']}INTERVENTION REQUIRED: {message}{COLORS['RESET']}")
            else:
                print(f"{COLORS['BLUE']}Policy Engine: {message}{COLORS['RESET']}")
                
        elif phase == "EXEC":
            if "success" in message.lower():
                print(f"{COLORS['GREEN']}Action executed successfully.{COLORS['RESET']}")
            else:
                print(f"{COLORS['WHITE']}{message}{COLORS['RESET']}")
                
            # Tratar de extraer que hizo
            result = event.get("result", {})
            if isinstance(result, Mapping) and "stdout" in result:
                out = str(result["stdout"]).strip()
                if out:
                    # Mostrar solo las primeras lineas si es muy largo
                    lines = out.split('\n')
                    preview = '\n'.join(lines[:3]) + ('\n...' if len(lines) > 3 else '')
                    print(f"       {COLORS['DIM']}Output: {preview}{COLORS['RESET']}")
                    
        elif phase == "ERROR":
            print(f"{COLORS['RED']}FATAL ERROR: {message}{COLORS['RESET']}")
            if metadata:
                print(f"       {COLORS['RED']}Details: {json.dumps(metadata)}{COLORS['RESET']}")
                
        elif phase == "VERIFY":
            print(f"{COLORS['GREEN']}State verified.{COLORS['RESET']}")
            
        else:
            print(f"{COLORS['WHITE']}{message}{COLORS['RESET']}")

    def __call__(self, event: Mapping[str, Any]) -> None:
        self.log_event(event)
