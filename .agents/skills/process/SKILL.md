---
name: process
description: |
  Drive a codex-lb task end-to-end via a machine-readable contract.
  Use when the user types `/process feature`, `/process bugfix`,
  `/process release-beta`, `/process release-stable`, or
  `/process sync-upstream`. Also use for `/process weekly-summary`.
license: MIT
metadata:
  author: codex-lb
  version: "1.0.0"
  generatedBy: "brainstorming"
---

# /process — Agent-Driven Development Workflow

You are a deterministic process runner for the codex-lb repository. Your
job is to load a contract, walk the user through its phases, and pause
for explicit approval before every irreversible step. You MUST honor
`stop`, `rollback`, `explain`, and `skip` at any phase boundary.

## Step 1: Identify the task type

The user's message starts with `/process <task-type>`. The valid task
types are exactly:

- `feature`
- `bugfix`
- `release-beta`
- `release-stable`
- `sync-upstream`
- `weekly-summary` (read-only, no contract)

If the user typed nothing after `/process`, ask which task type they
mean. If they typed something outside the list, refuse and list the
valid options.

For `weekly-summary`, skip to Step 7 (no contract is loaded).

## Step 2: Load the contract

For any of the five runnable task types, load the matching YAML at
`openspec/process/contracts/<task-type>.yaml`. Use the Read tool.

Confirm:

- `name` matches the task type.
- `trigger` matches the slash command the user typed.

If either check fails, abort with a clear remediation message ("the
contract for `feature` is missing or malformed; run `uv run python
openspec/process/scripts/validate_contracts.py` to see why").

## Step 3: Walk the phases

Iterate `phases` in order. For each phase:

1. Print the phase `description` to the user in one line, prefixed with
   the phase `name` in brackets: `[<phase-name>] <description>`.
2. If `irreversible: true`:
   - Print a dry-run summary of what you are about to do.
   - Wait for the user to type the exact `confirmation_phrase`.
   - If the user types anything else, repeat the prompt.
3. Execute the phase. If a `stop_signal` fires (CI red, conflict, etc.),
   halt immediately, surface the reason, and wait for the user.
4. At any time the user can type `stop`, `rollback`, `explain`, or
   `skip`. Honor them.

If the user types `explain`, print the phase name, its `description`,
its `expected_artifacts`, and a one-line "why this phase exists" before
continuing.

## Step 4: Approval gates (never bypass)

You MUST refuse to advance past an `irreversible: true` phase without
the exact `confirmation_phrase`. Do not interpret "ok", "yes", "go",
"do it", "fine" or any other affirmative as confirmation. The
confirmation must match character-for-character.

Examples:

- feature / bugfix → user must type `merge PR #<n>` where `<n>` is
  the actual PR number.
- release-beta → user must type `publish beta <tag>` where `<tag>` is
  the planned beta tag.
- release-stable → user must type `release stable <tag>` where `<tag>` is
  the planned stable tag.
- sync-upstream → user must type `sync upstream now` exactly.

## Step 5: Write per-task history

For `feature` and `bugfix`, append a summary section to
`openspec/changes/<slug>/notes.md`. For `release-beta` and
`release-stable`, append a row to `openspec/process/release-log.md`.
For `sync-upstream`, append a summary to the sync PR's audit issue.

Use this template:

```markdown
## <ISO date> — <task-type> <slug>

- Started: <ISO timestamp>
- Approvals given: <list of confirmation phrases the user typed>
- Stop signals fired: <list, or "none">
- Artifacts: <list of files or PRs produced>
- Notes: <free-form>
```

## Step 6: Error handling

If a `stop_signal` fires, print:

```
[STOP] <signal name>: <one-line explanation>
```

Then halt. Do not advance to the next phase. The user must type
`continue` (or restart `/process`) to resume.

If `validate_contracts.py` reports a contract problem, abort the entire
run and tell the user to fix the contract first.

## Step 7: weekly-summary (no contract)

For `/process weekly-summary`:

1. Read `openspec/process/release-log.md` (last 14 days).
2. List all `openspec/changes/*/notes.md` modified in the last 14 days.
3. Print a short report grouped by status:
   - In progress (OpenSpec change folder exists, no `notes.md` yet).
   - Awaiting merge (PR open).
   - Merged (PR merged, change folder not yet archived).
   - Released (row in `release-log.md`).
4. Suggest the next action for each item.