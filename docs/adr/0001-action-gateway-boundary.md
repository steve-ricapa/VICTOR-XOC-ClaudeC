# ADR 0001: Action Gateway Boundary

## Status

Accepted

## Context

The agent must prevent any execution path that bypasses policy checks.

## Decision

All execution requests pass through `core/actions/action_gateway.py`.
Only the gateway can call `core/execution/execution_service.py`.

## Consequences

- Stronger policy enforcement guarantees.
- Clear import and dependency boundaries.
- Slightly higher integration complexity for new actions.
