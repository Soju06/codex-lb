# AGENTS

## Environment

- Python: `>=3.13` via `uv` (`uv sync --frozen`; interpreter at `.venv/bin/python`)
- GitHub auth for git/API is available via env vars: `GITHUB_USER`, `GITHUB_TOKEN` (PAT). Do not hardcode or commit tokens.
- For authenticated git over HTTPS in automation, use: `https://x-access-token:${GITHUB_TOKEN}@github.com/<owner>/<repo>.git`

## Execution and ownership

Carry authorized work through implementation, required verification and the requested delivery. Resolve routine choices from the repository and session context. When a decision still belongs to the user, complete the authorized preparation and present the concrete result they need to approve. Existing user authorization takes precedence over generic skill defaults.

Delegate independent work only when it helps. Give each worker a bounded outcome, write boundary and completion checks. Keep one owner per change or live operation, integrate returned evidence, and transfer remaining obligations before ending a worker task.

When a step fails, compare the candidate, failure and operating conditions before retrying. Do not repeat the same failed action when all three are unchanged. Change the diagnostic only when it can produce new evidence. Stop that line of work and escalate when no justified next step remains or progress requires external action; record the recovery point and exact blocker. Continue independent authorized work.

## Protect the running service

The contributor may be using CodexLB to run this agent. Use isolated test state and retain ownership of the processes you start. Identify exact process or container IDs before stopping anything. Live runtime changes belong to the explicitly designated deployment owner.

Before importing the app for tests, set `CODEX_LB_DATABASE_URL` and `CODEX_LB_TEST_DATABASE_URL` to the same dedicated disposable database. Verify foreground, background and fixture engines before any reset. Rehearse migrations against a consistent snapshot or restored backup, preserving the original data and keys. Keep secrets and request contents out of logs and published evidence.

## Code Conventions

The `/project-conventions` skill is auto-activated on code edits (PreToolUse guard).

| Convention | Location | When |
|-----------|----------|------|
| Code Conventions (Full) | `/project-conventions` skill | On code edit (auto-enforced) |
| Git Workflow | `.agents/conventions/git-workflow.md` | Commit / PR |

## OpenSpec and documentation

OpenSpec is the source of truth for behavior, API, schema, CLI, dashboard-visible, routing, operator and compatibility requirements.

1. Read the relevant capability under `openspec/specs/`.
2. Before changing its behavior or contract, create `openspec/changes/<slug>/` with proposal, design where needed, delta requirements and tasks.
3. Implement against the requirements. Keep testable MUST/SHALL requirements in `spec.md`; put rationale, constraints and concrete examples in `context.md` or change notes.
4. Sync stable requirements and context to the capability. Run strict change and canonical-spec validation before readiness.
5. Verify the completed change before archiving it under `openspec/changes/archive/`.

Use the installed `openspec-*` skills for the corresponding workflow step. The `/opsx:new`, `/opsx:continue`, `/opsx:ff`, `/opsx:apply`, `/opsx:verify`, `/opsx:sync` and `/opsx:archive` commands expose those steps.

User-facing feature documentation belongs in `docs/` and links to its owning OpenSpec capability. Keep one durable explanation per decision; update or remove stale guidance when the decision changes. Add feature docs through OpenSpec and `docs/`, never a new README feature section. Leave `CHANGELOG.md` to the release process.

## Verification

Start with regression coverage at the failing public entry point. Complete the repository checks required for the change. Broaden or repeat verification when a relevant edit, failure or unresolved risk warrants it; reuse evidence tied to an unchanged candidate and scope.

For routing or compatibility changes, account for each affected path: native and `/v1` routes, HTTP bridge, forced HTTP and direct WebSocket. Prove both the successful case and the ownership, error or cleanup constraint that must remain intact.

Record the reviewed base, candidate and diff boundary. Report local tests, hosted checks, live acceptance and upstream merge as separate states. A passing helper test or startup probe does not prove the end-to-end request worked.

## Contributing & Merge Gates

When authoring or merging a PR (as a human contributor, a collaborator,
or an AI assistant acting on behalf of either), the binding workflow is
in [`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md). The sections
an AI assistant most often needs are:

- [Merge gates](.github/CONTRIBUTING.md#merge-gates) — CI green +
  actionable CodeRabbit findings addressed + `mergeable=CLEAN` +
  OpenSpec change folder for behavior changes + `Fixes #N` /
  `Closes #N` for issue cover + the six simplicity rules
  (PRINCIPLES.md P1-P6; see
  [Simplicity gates](.github/CONTRIBUTING.md#simplicity-gates)).
- [Collaborator rules](.github/CONTRIBUTING.md#collaborator-rules) —
  no self-merge by default; large PRs get split (≈1-concern per PR,
  ~800 net lines / scoped capability ceiling).
- [Bus factor escape hatch](.github/CONTRIBUTING.md#bus-factor-escape-hatch)
  — self-merge allowed after **14 days** with all gates met and a
  comment invoking the clause.

An assistant preparing a merge MUST verify the gates against the
actual GitHub state (status check rollup, current-head CodeRabbit review
threads, `mergeable` field) rather than asserting them from local history.
Local `uv run pytest` / `uv run ruff` / `codex review --base origin/main`
are encouraged but not substitutes for the cloud gates.

## PR Readiness / Review Trapdoors

These rules encode recurring review blockers observed across codex-lb PRs.

- CodeRabbit review state must come from current-head GitHub evidence.
  Unresolved, non-outdated actionable review threads block readiness until
  their findings are fixed or explicitly addressed or dismissed in-thread;
  a top-level summary does not override active thread evidence.
- Proxy failover and retry patches must prove account ownership and settlement
  invariants. File-pinned requests must not cross accounts; API-key reservations
  must settle before error-health writes; excluded accounts must actually leave
  the selection loop; idle disconnects must not mark otherwise healthy accounts
  unhealthy; security/trusted-access routing must degrade only along the
  documented path.
- Async, fan-out, and session-lifecycle patches must prove task ownership and
  cleanup. Do not share one `AsyncSession` across concurrent tasks; cancel or
  await spawned tasks on failure; preserve finalization/settlement paths after
  partial errors; bound fan-out; and test partial-failure behavior, not only
  the all-success path.
- Database migrations must prove Alembic graph and data hygiene. New revisions
  must sit on the current intended parent with a single-head upgrade path, have
  downgrade/upgrade coverage where the project expects it, and include
  historical-row backfills or compatibility handling when new fields affect
  existing data. Fetch `main` and run `make lint`
  (`scripts/check_migration_topology.py`) after adding a revision: it fails on a
  forked graph, on a revision whose parent `main` has already built on, and on a
  timestamp slot another revision already took.
- Issue-resolving PRs must name the exact `Fixes #N` / `Closes #N`, or state
  that they are partial. Keep PRs one concern wide. Revive stale work by making
  a focused branch on current `main`; do not drag an old broad/conflicted branch
  forward unless the maintainer explicitly wants that shape.
- Bug fixes need regression coverage at the externally failing product path:
  route, bridge, websocket, CLI, schema, dashboard UI, or migration path as
  applicable. Helper-only tests are not enough when the failing surface is
  elsewhere.
- Compatibility work must verify canonical and equivalent paths, trailing slash
  behavior, external error envelopes, env-var semantics, and response-schema
  contracts. Update OpenSpec/context and tests together so docs cannot promise
  behavior the code does not implement.
- Simplicity gates are a merge gate (`PRINCIPLES.md` +
  [CONTRIBUTING.md Simplicity gates](.github/CONTRIBUTING.md#simplicity-gates)).
  New features must default off or work zero-config; new `CODEX_LB_*` settings
  need a why-not-a-default justification in the PR body; README top-level
  sections, `.env.example`, and dashboard core-nav items are budgeted per
  `.github/simplicity-budgets.toml` and exceptions need the maintainer-applied
  `simplicity-budget-approved` label; feature documentation goes to `docs/` +
  openspec (never new README sections); dashboard-visible PRs include
  before/after screenshots.
