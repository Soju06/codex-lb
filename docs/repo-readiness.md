# Repository readiness

This is the short operational entry point for a checkout. Product behavior and
operator contracts remain authoritative in `openspec/`.

## Quick path

```bash
bin/setup
bin/test --diff origin/main
bin/dev
bin/logs
```

`bin/setup` is idempotent, prepares dependencies and dashboard assets, and does
not generate secrets.
`bin/dev` binds locally and writes the combined checkout-local service log that
`bin/logs` follows. Stop the service with the cleanup instruction printed by
`bin/dev`.

Before delivery, run the full aggregate gate and the repository guard:

```bash
bin/test
bin/check-operability
```

## Where to look

- `AGENTS.md`: agent workflow, change routing, and merge gates.
- `ARCHITECTURE.md`: code boundaries, trust boundaries, and load-bearing paths.
- `.github/CONTRIBUTING.md`: development and pull-request workflow.
- `openspec/specs/`: normative observable requirements.
- `Makefile`: native proof targets wrapped by `bin/test`.

## Worktrees

Keep the root checkout on the default branch and do task work in one worktree
per branch. Use `bin/worktree` for the supported create/list/remove flow. When a
task depends on an unmerged branch, base its child worktree on that branch and
target the child pull request at its parent.

## Known external gates

Repository commands can prove local behavior, but cannot prove GitHub ruleset,
merge-queue, or a real deployment's health. Verify those from their live control
planes before claiming delivery or production readiness.
