# Threat Model

## Trust Boundaries

- Ticket backend input.
- Claude Code SDK output.
- Local OS command/file/network execution.
- External MCP tools.
- Human approval channel.

## Primary Risks

- Prompt or tool-invocation injection.
- Policy bypass through direct executor access.
- Secret leakage through logs or artifacts.
- Privilege escalation inside host runtime.
- Replay or duplicate ticket execution.

## Mitigations

- Action gateway as mandatory policy choke point.
- Input sanitization and output redaction.
- Least-privilege execution account and sandbox guards.
- Correlation IDs and append-only audit events.
- Idempotency keys and checkpointed state transitions.
