import sys
from typing import Any, Mapping

COLORS = {
    "RESET": "\033[0m",
    "CYAN": "\033[96m",
    "GREEN": "\033[92m",
    "YELLOW": "\033[93m",
    "RED": "\033[91m",
    "MAGENTA": "\033[95m",
    "BLUE": "\033[94m",
    "BOLD": "\033[1m"
}

class InteractiveDecisionMenu:
    """
    Gestor interactivo para la terminal. Maneja el estado WAITING_DECISION
    del orquestador y presenta al usuario un prompt en pantalla para aprobar,
    denegar o pausar la operacion.
    """
    
    @staticmethod
    def prompt(decision: Mapping[str, Any]) -> str | None:
        """
        Dibuja el menu y espera la seleccion del usuario.
        Retorna el ID de la opcion seleccionada (A, B, C) o None si el usuario aborta.
        """
        question = decision.get("question", "Decision de seguridad requerida")
        options = decision.get("options", [])
        risk_level = decision.get("risk_level", "UNKNOWN")
        risk_explanation = decision.get("risk_explanation", "")

        print(f"\n{COLORS['YELLOW']}{COLORS['BOLD']}============================================================{COLORS['RESET']}")
        print(f"{COLORS['YELLOW']}{COLORS['BOLD']}  [SYSTEM PROMPT] HUMAN DECISION REQUIRED                   {COLORS['RESET']}")
        print(f"{COLORS['YELLOW']}{COLORS['BOLD']}============================================================{COLORS['RESET']}")
        
        print(f"\n{COLORS['CYAN']}Question:{COLORS['RESET']} {question}")
        if risk_level != "UNKNOWN":
            print(f"{COLORS['RED']}Nivel de Riesgo:{COLORS['RESET']} {risk_level}")
            if risk_explanation:
                print(f"{COLORS['RED']}Explicacion:{COLORS['RESET']} {risk_explanation}")
                
        # Mostrar vista previa de la accion si existe
        action_preview = decision.get("action_preview", {})
        if action_preview:
            desc = action_preview.get("description", "")
            if desc:
                print(f"\n{COLORS['BOLD']}Contexto de la accion:{COLORS['RESET']} {desc}")

        print(f"\n{COLORS['BOLD']}Opciones Disponibles:{COLORS['RESET']}")
        valid_options = []
        for opt in options:
            opt_id = str(opt.get("id")).upper()
            valid_options.append(opt_id)
            label = opt.get("label", "")
            desc = opt.get("description", "")
            print(f"  [{COLORS['GREEN']}{opt_id}{COLORS['RESET']}] {label} - {desc}")
            
        print(f"  [{COLORS['RED']}Q{COLORS['RESET']}] Salir del agente (Abortar)")

        while True:
            try:
                choice = input(f"\n{COLORS['BOLD']}Selecciona una opcion ({'/'.join(valid_options)}/Q) > {COLORS['RESET']}").strip().upper()
                if choice == 'Q':
                    print(f"{COLORS['YELLOW']}Operacion abortada por el usuario. El ticket quedara pausado.{COLORS['RESET']}")
                    return None
                if choice in valid_options:
                    return choice
                print(f"{COLORS['RED']}Opcion invalida, por favor intenta de nuevo.{COLORS['RESET']}")
            except (KeyboardInterrupt, EOFError):
                print(f"\n{COLORS['YELLOW']}Operacion abortada por el teclado. Saliendo...{COLORS['RESET']}")
                return None
