# AGENTS

## Environment

- Python: .venv/bin/python (uv, CPython 3.13.3)
- GitHub auth for git/API is available via env vars: `GITHUB_USER`, `GITHUB_TOKEN` (PAT). Do not hardcode or commit tokens.
- For authenticated git over HTTPS in automation, use: `https://x-access-token:${GITHUB_TOKEN}@github.com/<owner>/<repo>.git`

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

## Contributing & Merge Gates

When authoring or merging a PR (as a human contributor, a collaborator,
or an AI assistant acting on behalf of either), the binding workflow is
in [`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md). The sections
an AI assistant most often needs are:

- [Merge gates](.github/CONTRIBUTING.md#merge-gates) — CI green +
  `@codex review` clean (or findings addressed) + `mergeable=CLEAN` +
  OpenSpec change folder for behavior changes + `Fixes #N` /
  `Closes #N` for issue cover.
- [Collaborator rules](.github/CONTRIBUTING.md#collaborator-rules) —
  no self-merge by default; large PRs get split (≈1-concern per PR,
  ~800 net lines / scoped capability ceiling).
- [Bus factor escape hatch](.github/CONTRIBUTING.md#bus-factor-escape-hatch)
  — self-merge allowed after **14 days** with all gates met and a
  comment invoking the clause.

An assistant preparing a merge MUST verify the gates against the
actual GitHub state (status check rollup, codex review submissions,
`mergeable` field) rather than asserting them from local history.
Local `uv run pytest` / `uv run ruff` / `codex review --base origin/main`
are encouraged but not substitutes for the cloud gates.
