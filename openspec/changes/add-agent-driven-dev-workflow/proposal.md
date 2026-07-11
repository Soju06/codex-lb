# Add Agent-Driven Development Workflow — Proposal

## Why

`codex-lb` is a fork maintained by a non-professional programmer who delegates
nearly all code work to Claude Code. Today there is no explicit contract
between the user and Claude about *how* a typical task flows: the order of
operations (OpenSpec → branch → commit → push → PR → review → merge →
release), the trigger phrases that should kick off each task type, and the
non-reversible steps where the user must be asked before Claude proceeds.

The repo already automates the CI/release side (`ci.yml`, `release-please.yml`,
`release.yml`, `prepare-beta-release.yml`, `publish-beta-release.yml`,
`scripts/sync_upstream.sh`, `guard_*_release.py`). What's missing is an
explicit conversational contract that ties the user, Claude, and CI together
into a single predictable flow.

Concretely, the user reports confusion around:

- The order of git/GitHub operations and which steps Claude can do without
  asking versus which need explicit approval.
- The distinction between beta releases, stable releases, and tags.

This change defines that contract.

## What Changes

- **New** `openspec/process/contracts/` directory holding five machine-readable
  contracts (`feature`, `bugfix`, `release-beta`, `release-stable`,
  `sync-upstream`) plus a JSON Schema that validates them.
- **New** `openspec/process/process-map.md` — human-readable mermaid cheat
  sheet that mirrors the contracts.
- **New** `openspec/process/release-log.md` — release history written by the
  release contracts.
- **New** `openspec/process/contracts/README.md` — explains the contract
  shape and authoring rules.
- **New** `.claude/skills/process/SKILL.md` — a Claude Code skill that loads
  a contract by task type, walks the user through phases, pauses before
  every irreversible step, and writes per-task history to
  `openspec/changes/<slug>/notes.md` or `openspec/process/release-log.md`.
- **New** `openspec/process/scripts/validate_contracts.py` — Python CLI
  that loads every contract and validates it against the JSON Schema. Used
  by tests and by the CI workflow.
- **New** `.github/workflows/process-check.yml` — GitHub Action that runs on
  every PR, validates all contracts, and enforces the OpenSpec gate for
  behavior/API/schema/CLI/dashboard-visible/proxy-routing changes.
- **New** `tests/unit/process/test_contracts_schema.py` and
  `tests/integration/process/test_process_check_workflow.py` covering the
  schema, the validator CLI, and the contract set.
- **Modified** `pyproject.toml` — adds `jsonschema` to test dependencies.
- **Modified** `CLAUDE.md` — adds a short pointer to
  `openspec/process/process-map.md`.
- **Modified** `.github/CONTRIBUTING.md` — adds a paragraph about the
  `/process` skill alongside the existing OpenSpec workflow section.

## Capabilities

This adds one new capability: `dev-workflow`. Its spec lives at
`openspec/changes/add-agent-driven-dev-workflow/specs/dev-workflow/spec.md`.

## Impact

- **Operator workflow**: explicit trigger phrases and approval gates.
  Low cognitive load — the cheat sheet is one document.
- **Claude Code**: gains the `/process <task-type>` slash command and an
  interrupt vocabulary (`stop`, `rollback`, `explain`, `skip`).
- **CI**: one extra workflow (`process-check.yml`) that runs in seconds.
- **No runtime impact** on the proxy, dashboard, or data layer.

## Out of Scope (YAGNI)

- Slack/Telegram notifications.
- A metrics dashboard for workflow adherence.
- Cron-driven auto-merge or auto-release.
- A custom GUI for approvals.
- Hooks that auto-block Claude on certain operations (the contracts and
  the explicit user `ok` are sufficient for v1).

## Predecessor

None. This is a greenfield capability inside the fork. The closest analog is
the `sync-upstream` skill (added in #13) and the OpenSpec `opsx:*` skills,
both of which this change builds on.
