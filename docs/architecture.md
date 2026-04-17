# Architecture

VICTOR is a secure execution runtime, not a chatbot or web application.

## Runtime Flow

1. Fetch ticket from backend with lease.
2. Build bounded execution plan.
3. Ask Claude Agent SDK for next action proposal.
4. Route action through policy gate.
5. Execute via selected executor in sandbox.
6. Emit structured audit events.
7. Pause for human decision when required.
8. Persist checkpoint and update ticket state.

## Boundary Rules

- Orchestrator never calls executor modules directly.
- Policy evaluation occurs for every action and tool call.
- MCP tools are deny-by-default and capability-mapped.
