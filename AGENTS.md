# AGENTS

## Environment

- Python: .venv/bin/python (uv, CPython 3.13.3)
- GitHub auth for git/API is available via env vars: `GITHUB_USER`, `GITHUB_TOKEN` (PAT). Do not hardcode or commit tokens.
- For authenticated git over HTTPS in automation, use: `https://x-access-token:${GITHUB_TOKEN}@github.com/<owner>/<repo>.git`

## Working Directory & Parallel Sessions

When this clone is shared with other Claude Code sessions or human
contributors running in parallel, every session MUST work in its own
git worktree. Sharing the main working tree races for `.git/index`,
`.git/HEAD`, untracked files, and reflog — observed symptoms include
PRs contaminated with unrelated files, branch tips carrying the wrong
commits, and stashes that don't belong to the agent.

### Pattern (per session)

Each session gets its own physical working directory over the shared
`.git/`:

```bash
# At the start of a session or sub-agent task:
git worktree add /Users/amalov/codex-lb-<task-tag> -b <branch> origin/main
cd /Users/amalov/codex-lb-<task-tag>

# ... work, commit, push ...
git push -u origin <branch>
gh pr create ...

# After the PR is merged:
cd /Users/amalov/codex-lb && git worktree remove /Users/amalov/codex-lb-<task-tag>
```

`<task-tag>` must be unique and descriptive (e.g.
`claude-account-summary-fix`, `openspec-sticky-threading`). Generic
names like `fix` or `wip` collide across sessions.

**Pros:** zero contention on `.git/index`/`.git/HEAD`/untracked files;
each session is invisible to others until `git fetch`.
**Cons:** ~5s extra setup per session; worktree must be cleaned up
after merge.

### Alternative for one-shot tasks

A fresh clone in `/tmp` is acceptable for small, self-contained work
that does not need to see other sessions' local state:

```bash
git clone /Users/amalov/codex-lb /tmp/codex-lb-<task-tag>
cd /tmp/codex-lb-<task-tag>
# ... work, push branch ...
rm -rf /tmp/codex-lb-<task-tag>
```

### Stop-and-report triggers for sub-agents

A sub-agent MUST pause and ask the user (not improvise a Plan B) when
any of these appear mid-task:

- `git status` lists files the agent did not edit.
- `git push` is rejected with non-fast-forward on a branch the agent
  just created (likely a name collision with a parallel session).
- The working tree accumulates unrelated commits between the agent's
  own `git add` and `git commit`.

## Code Conventions

The `/project-conventions` skill is auto-activated on code edits (PreToolUse guard).

| Convention | Location | When |
|-----------|----------|------|
| Code Conventions (Full) | `/project-conventions` skill | On code edit (auto-enforced) |
| Git Workflow | `.agents/conventions/git-workflow.md` | Commit / PR |

## Workflow (OpenSpec-first)

This repo uses **OpenSpec as the primary workflow and SSOT** for change-driven development.

### How to work (default)

1) Find the relevant spec(s) in `openspec/specs/**` and treat them as source-of-truth.
2) If the work changes behavior, requirements, contracts, or schema: create an OpenSpec change in `openspec/changes/**` first (proposal -> tasks).
3) Implement the tasks; keep code + specs in sync (update `spec.md` as needed).
4) Validate specs locally: `openspec validate --specs`
5) When done: verify + archive the change (do not archive unverified changes).

### Source of Truth

- **Specs/Design/Tasks (SSOT)**: `openspec/`
  - Active changes: `openspec/changes/<change>/`
  - Main specs: `openspec/specs/<capability>/spec.md`
  - Archived changes: `openspec/changes/archive/YYYY-MM-DD-<change>/`

## Documentation & Release Notes

- **Do not add/update feature or behavior documentation under `docs/`**. Use OpenSpec context docs under `openspec/specs/<capability>/context.md` (or change-level context under `openspec/changes/<change>/context.md`) as the SSOT.
- **Do not edit `CHANGELOG.md` directly.** Leave changelog updates to the release process; record change notes in OpenSpec artifacts instead.

### Documentation Model (Spec + Context)

- `spec.md` is the **normative SSOT** and should contain only testable requirements.
- Use `openspec/specs/<capability>/context.md` for **free-form context** (purpose, rationale, examples, ops notes).
- If context grows, split into `overview.md`, `rationale.md`, `examples.md`, or `ops.md` within the same capability folder.
- Change-level notes live in `openspec/changes/<change>/context.md` or `notes.md`, then **sync stable context** back into the main context docs.

Prompting cue (use when writing docs):
"Keep `spec.md` strictly for requirements. Add/update `context.md` with purpose, decisions, constraints, failure modes, and at least one concrete example."

### Commands (recommended)

- Start a change: `/opsx:new <kebab-case>`
- Create artifacts (step): `/opsx:continue <change>`
- Create artifacts (fast): `/opsx:ff <change>`
- Implement tasks: `/opsx:apply <change>`
- Verify before archive: `/opsx:verify <change>`
- Sync delta specs → main specs: `/opsx:sync <change>`
- Archive: `/opsx:archive <change>`

### /process skill

For end-to-end task automation, see
[`openspec/process/process-map.md`](openspec/process/process-map.md). The
cheat sheet describes the five runnable task types
(`/process feature`, `/process bugfix`, `/process release-beta`,
`/process release-stable`, `/process sync-upstream`) plus the
`/process weekly-summary` read-only report. Machine-readable contracts
live under `openspec/process/contracts/`; the validator at
`openspec/process/scripts/validate_contracts.py` is also wired into the
`process-check` GitHub Action.

## Contributing & Merge Gates

When authoring or merging a PR (as a human contributor, a collaborator,
or an AI assistant acting on behalf of either), the binding workflow is
in [`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md). The sections
an AI assistant most often needs are:

- [Merge gates](.github/CONTRIBUTING.md#merge-gates) — CI green +
  `mergeable=CLEAN` + OpenSpec change folder for behavior changes +
  `Fixes #N` / `Closes #N` for issue cover.
- [Collaborator rules](.github/CONTRIBUTING.md#collaborator-rules) —
  no self-merge by default; large PRs get split (≈1-concern per PR,
  ~800 net lines / scoped capability ceiling).
- [Bus factor escape hatch](.github/CONTRIBUTING.md#bus-factor-escape-hatch)
  — self-merge allowed after **14 days** with all gates met and a
  comment invoking the clause.

An assistant preparing a merge MUST verify the gates against the
actual GitHub state (status check rollup, `mergeable` field) rather
than asserting them from local history. Local `uv run pytest` /
`uv run ruff` are encouraged but not substitutes for the cloud gates.

## PR Readiness / Review Trapdoors

These rules encode recurring review blockers observed across codex-lb PRs.

- OpenSpec is a hard gate for behavior, API, schema, CLI,
  dashboard-visible, proxy-routing, operator-contract, and compatibility
  changes. Create or update `openspec/changes/<slug>/` before coding, keep
  `spec.md` normative with MUST/SHALL-style requirements, put rationale and
  examples in `context.md` or change notes, and run strict OpenSpec validation
  before calling the PR ready. Code/tests alone are not enough when OpenSpec is
  required.
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
  existing data.
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
