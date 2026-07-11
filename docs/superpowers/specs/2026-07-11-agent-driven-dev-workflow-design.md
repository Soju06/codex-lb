# Agent-Driven Development Workflow — Design

- **Date:** 2026-07-11
- **Status:** Approved (pending user review of written spec)
- **Author:** brainstormed with user, drafted by Claude
- **Related:** `CLAUDE.md`, `.github/CONTRIBUTING.md`, `openspec/`, `.agents/conventions/git-workflow.md`

## Context

The user is a non-professional programmer who delegates nearly all code work
to Claude Code. They reported confusion around the order of git/GitHub
operations (commit, push, PR, review, merge, release) and around the
distinction between beta releases, stable releases, and tags. They want
two things at once:

1. **Automation** — Claude should drive the workflow end-to-end.
2. **Correct sequence** — the user must never be surprised by a
   non-reversible action; they should approve before irreversible steps
   and stop Claude if something is off.

The repository already has a strong automation baseline:

- OpenSpec-first workflow with `opsx:*` commands.
- `scripts/sync_upstream.sh` wrapper around the `/sync-upstream` skill.
- CI workflows: `ci.yml`, `release-please.yml`, `release.yml`,
  `prepare-beta-release.yml`, `publish-beta-release.yml`.
- Local guards: `guard_stable_release.py`, `guard_beta_release.py`,
  `verify_release_version.py`.
- Documentation: `CONTRIBUTING.md`, `git-workflow.md`, project-conventions.

What's missing is **an explicit contract** between the user and Claude
about *how* a typical task flows. The repo automates CI, but does not
automate the conversation pattern the user has with Claude.

## Goal

Define and implement a lightweight, machine-readable contract that
encodes the workflow for the five recurring task types
(`feature`, `bugfix`, `release-beta`, `release-stable`, `sync-upstream`),
expose it as a `/process` skill for Claude Code, and gate PRs with a
GitHub Action that enforces the same rules from the CI side.

## Non-Goals (YAGNI)

- Slack/Telegram notifications.
- A metrics dashboard.
- Cron-driven auto-merge or auto-release.
- A custom GUI for approvals.

## Approach

**Hybrid autonomy** (option C from brainstorming): Claude runs the
process end-to-end, but **pauses for explicit user approval before every
irreversible step** (merge PR, push tag, run release workflow, delete a
protected branch, amend a spec post-merge). All routine steps are
autonomous. All history is captured in OpenSpec artifacts.

## Architecture

Four artifacts, each with a single responsibility:

### 1. `openspec/process/contracts/<task>.yaml`

Machine-readable contract for a task type. Each contract defines:

- `trigger` — the slash command the user invokes
- `phases` — ordered list of phases, each with:
  - `name`
  - `description` (what Claude does, what user sees)
  - `irreversible` (boolean — if true, Claude pauses for approval)
  - `stop_signals` (conditions that abort the phase and surface to user)
  - `expected_artifacts` (files or PRs the phase must produce)
- `commands` — interruption vocabulary (`stop`, `rollback`, `explain`,
  `skip`)

The contract is the **single source of truth**. The skill and the
GitHub Action both read from it.

### 2. `openspec/process/process-map.md`

Human-readable companion. Mermaid diagram of all flows + short prose
descriptions. This is the user's "cheat sheet". It references the
contracts but is generated/maintained by hand.

### 3. `.claude/skills/process/SKILL.md`

Claude Code skill. When the user types `/process <task-type>`, the skill:

1. Loads the matching contract.
2. Walks the user through phases.
3. Before each `irreversible` phase, prints a dry-run summary and waits
   for the explicit confirmation phrase defined in the contract.
4. Honors interruption commands (`stop`, `rollback`, etc.).
5. Writes a per-task history to `openspec/changes/<slug>/notes.md`
   (for feature/bugfix) or to `openspec/process/release-log.md`
   (for release).

### 4. `.github/workflows/process-check.yml`

CI-side enforcement. On every PR, runs:

- `openspec validate --specs` (already part of `opsx:*` workflow)
- Checks that PR has an `openspec/changes/<slug>/` folder if the PR
  changes behavior, API, schema, CLI, dashboard-visible behavior, or
  proxy-routing (per the OpenSpec gate list in `CONTRIBUTING.md`)
- Checks that `uv run pytest` and `uv run ruff` pass
- Fails the PR if any contract-required artifact is missing

This is the **defense-in-depth**: even if Claude skips a step, CI
catches it.

## Responsibility Split

| Who | Owns |
|---|---|
| **Claude** | Following the contract, logging history, dry-run before irreversible steps, explicit stop signals |
| **User** | Final approval of irreversible steps, task prioritization, contract updates when process changes |
| **GitHub / CI** | CI, auto-merge after green + approve, release workflows |

## Triggers and Flow

### Triggers

| Slash command | Loads contract |
|---|---|
| `/process feature` | `feature.yaml` |
| `/process bugfix` | `bugfix.yaml` |
| `/process release-beta` | `release-beta.yaml` |
| `/process release-stable` | `release-stable.yaml` |
| `/process sync-upstream` | `sync-upstream.yaml` |
| `/process weekly-summary` | read-only summary across `release-log.md` and `openspec/changes/`. Not a contract — no approval points. |

### Reference flow: `feature`

1. User: `/process feature`
2. Claude: "Describe the feature in 1-2 sentences."
3. User: "I want X so that Y."
4. Claude: creates `openspec/changes/<slug>/{proposal.md, tasks.md, spec.md}`.
5. Claude: "OpenSpec draft is ready. Verify and say 'ok' to continue." (approval point)
6. User: "ok" (or requests edits).
7. Claude: creates worktree, writes code + tests, commits, pushes, opens PR.
8. Claude: "PR #N opened. I will respond to review comments as they come."
9. CI green + review passed.
10. Claude: "Ready to merge. This is irreversible. Confirm: 'merge #N'." (approval point)
11. User: "merge #N"
12. Claude: squash-merge, remove worktree, mark OpenSpec change as `applied`.

The same pattern applies to other task types, with phase content varying
per contract.

## Approval Points and Stop Signals

### Approval points (Claude waits)

| Phase | Why wait | What Claude says |
|---|---|---|
| Merge PR | Irreversible; hard to back out | "PR #N ready. CI ✅. Confirm: 'merge #N'" |
| Push tag / trigger release | Public artifact; users see it | "Dry-run: tag v1.21.0, 12 commits, changelog updated. Confirm: 'release v1.21.0'" |
| Delete protected branch (main, release/*) | Work loss | "Want to delete branch X. This removes N commits. Confirm: 'delete X'" |
| Amend `openspec/specs/<cap>/spec.md` after merge | Compatibility break | "Want to edit spec — this breaks compat. Confirm: 'amend spec'" |
| Revert merged PR | Public "this was wrong" signal | "Want to revert PR #N. Confirm: 'revert #N'" |

### Stop signals (Claude halts and surfaces to user)

| Signal | Claude behavior |
|---|---|
| CI red | Halts, sends log link, proposes fix |
| Merge conflict on upstream sync | Halts, files audit issue, waits for user decision (our code vs upstream) |
| `openspec validate` failed | Halts, shows diff, proposes fix |
| Force-push to main or release/* | Blocked at skill level — Claude physically cannot |
| Secrets in diff (gitleaks) | Halts, requires secret removal |
| Ambiguous scope (feature vs bug) | Asks explicitly, does not guess |

### Interruption commands (user can stop at any time)

- `stop` — halt at current phase, do not advance
- `rollback` — undo the last step if reversible
- `explain` — describe what Claude is doing and why
- `skip` — skip current non-required phase

## History and Observability

| Data | Where | Why |
|---|---|---|
| Process contracts | `openspec/process/contracts/*.yaml` | Machine-readable source of truth for skill + CI |
| Process map | `openspec/process/process-map.md` | Cheat sheet for the user |
| Per-task history | `openspec/changes/<slug>/notes.md` | What Claude did, what approvals were given, what stop signals fired |
| Release history | `openspec/process/release-log.md` | Which tags, when, which PRs |
| Audit issues (sync upstream) | GitHub Issues with label `audit/sync-upstream` | Trail of upstream conflicts |

The user stays oriented through three mechanisms:

1. **At the start of each task** — Claude says: "This is task type X.
   Contract Y. Agree?"
2. **On every phase** — one-line description of what is about to happen.
3. **At the end** — short report: what was done, what is in OpenSpec,
   which links matter.

## Definition of Done

This design is "done" when **all of the following** are true:

- [ ] The five contracts exist and validate against a JSON Schema
      (`openspec/process/contracts/schema.json`) —
      `openspec/process/contracts/{feature,bugfix,
      release-beta,release-stable,sync-upstream}.yaml`
- [ ] `process-map.md` is published and links to each contract
- [ ] `/process` skill is installed in `.claude/skills/process/` and
      loads a contract by task type
- [ ] `process-check.yml` is added under `.github/workflows/` and
      runs on every PR
- [ ] A test scenario exists: user runs `/process feature`, approves
      both approval points, and a clean PR lands in `main`
- [ ] `CLAUDE.md` is updated with a short pointer to
      `openspec/process/process-map.md`
- [ ] `CONTRIBUTING.md` is updated to mention the `/process` skill
      alongside the existing OpenSpec workflow section

## Open Questions

None at design time. Will surface during implementation if/when a
contract turns out to be ambiguous.
