# Skills

Este directorio contiene definiciones reutilizables de skills para automatizaciones por cliente.

Contenido recomendado:
- Metadatos del skill (`name`, `version`, `owner`)
- Condiciones de activacion (`triggers`)
- Acciones recomendadas (`recommended_actions`)
- Notas de seguridad (`safety_notes`)
- Expectativas de salida y validacion

Buenas practicas:
- Priorizar acciones de solo lectura al inicio
- Evitar comandos destructivos
- Documentar riesgos operativos y criterios de escalamiento

Archivo de ejemplo:
- `example_incident_triage.yaml`

Formato recomendado (YAML):

```yaml
name: "incident-triage-basic"
version: 1
owner: "platform-security"
description: "Guia base de triage para tickets operativos"

triggers:
  - "servicio caido"
  - "alto ratio de errores"

recommended_actions:
  - type: "file"
    description: "Verificar si existe un path de runtime"
    parameters:
      operation: "exists"
      path: "runtime/artifacts"

  - type: "shell"
    description: "Recolectar una captura rapida"
    parameters:
      command:
        - "python"
        - "-c"
        - "print('captura-triage')"

safety_notes:
  - "Priorizar acciones de solo lectura al inicio"
  - "No ejecutar comandos destructivos durante el triage"
```

Notas de formato:
- `name` puede mantenerse en ingles si lo prefieres.
- `description`, `triggers`, `recommended_actions` y `safety_notes` deben ir en espanol.
- Cada accion en `recommended_actions` debe seguir el contrato de acciones (`type` + `parameters`).

## Como se usan en el runtime

El `prompt_builder` incorpora una seccion `SKILLS_APLICABLES` dentro del prompt del agente.

Seleccion actual:
- Si el ticket incluye `skills` o `requested_skills`, esas skills se cargan de forma explicita.
- Si no las incluye, el sistema intenta seleccionar skills por coincidencia de `triggers` contra el contenido del ticket.
- Como refuerzo, tambien existen heuristicas por dominio para red, remediacion de archivos, vulnerabilidades y escalacion humana.

Ejemplos de uso desde ticket:

```json
{
  "ticket_id": "ticket-demo",
  "skills": [
    "secure-file-remediation",
    "human-escalation"
  ]
}
```

Recomendacion:
- Para integracion con backend o Sophia, conviene que el sistema emisor pueda enviar `skills` explicitas cuando ya conoce el tipo de ticket.
- Si no se envian skills explicitas, el matching automatico ayuda como fallback.
