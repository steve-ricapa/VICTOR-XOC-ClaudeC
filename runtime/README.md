# Runtime Data

Ephemeral runtime state, artifacts, and audit outputs are written under this tree.

Directory intent:
- `runs/` per-run state metadata
- `workspaces/` isolated run workspaces
- `checkpoints/` pause/resume snapshots
- `audit/` structured JSONL audit logs
- `decisions/` decision payload state
- `artifacts/` generated outputs
- `locks/` local coordination locks
