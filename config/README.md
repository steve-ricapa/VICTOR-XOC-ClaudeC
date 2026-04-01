# Configuracion local

Este directorio guarda configuracion por cliente y no debe versionarse en Git.

Archivos esperados:
- `agent.yaml`
- `policy.yaml`
- `capabilities.yaml`
- `mcp.yaml`

Usa `config.dist/` como plantilla base.

## Modelo de Claude Code

Puedes cambiar el modelo que usa el runtime en:

- `config/agent.yaml`
- Seccion: `llm.model`

Ejemplo:

```yaml
llm:
  provider: "anthropic"
  model: "claude-sonnet-4-6"
  temperature: 0.0
  max_tokens: 4000
```

Modelos recomendados:
- `claude-opus-4-6`
- `claude-sonnet-4-6`
- `claude-haiku-4-5`

Notas:
- Si no defines `llm.model`, el runtime usa el valor por defecto interno.
- Para pruebas reales del LLM necesitas `ANTHROPIC_API_KEY` en `config/secrets/.env`.
