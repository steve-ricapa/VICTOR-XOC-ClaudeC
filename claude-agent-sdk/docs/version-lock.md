# Version Lock

This file tracks the documentation baseline and lock policy for the local Claude Agent SDK knowledge base.

## Current baseline

- Baseline status: `locked`
- Lock date: `2026-04-16`
- Scope: `claude-agent-sdk/docs/**`

## Lock policy

- Update references only after validating changes against official Claude Agent SDK material.
- Keep breaking-change notes aligned with `docs/08-sdk-references/migration-guide.md`.
- When behavior changes in SDK docs, update this file and add an entry in `docs/changelog-notes.md`.
- Do not rewrite historical notes; append new lock events.

## Verification checklist

- Confirm API names and options in `docs/08-sdk-references/python.md`.
- Confirm API names and options in `docs/08-sdk-references/typescript.md` and `docs/08-sdk-references/typescript-v2.md`.
- Confirm session/tool/approval behavior in `docs/02-core-concepts` and `docs/04-tools`.
- Confirm deployment/security updates in `docs/07-deployment`.
