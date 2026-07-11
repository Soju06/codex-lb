# Repository standards report

Assessment target: branch `codex/me-setup-repo`, based on
`65dc4b75be2de837968dcdf86ec233f5d5f0ad72`. Verdicts below describe the
worktree at the time of this report; a referenced command is not considered
verified until it exists and passes. `report-only` means the standard needs an
external control plane or a separately scoped operational design rather than a
safe repository-only change.

| Standard | Verdict | Classification | Evidence | Residual owner |
|---|---|---|---|---|
| Agent markdowns accurate and present | verified | fixed here | `AGENTS.md` names OpenSpec as behavioral SSOT, routes contributors to architecture and canonical commands, and `CLAUDE.md` links to it. | Maintainers keep paths and gates current. |
| Architecture doc matches code | verified | fixed here | `ARCHITECTURE.md`; direct inspection of `app/main.py`, `app/modules/`, `app/db/`, `frontend/`, delivery assets, and `openspec/specs/`. | Maintainers update the map when boundaries move. |
| Fast single-environment setup | gap | fix-now | Initial probe found no `bin/setup`; Compose failed because `.env.local` was missing. Planned proof: run `bin/setup` twice plus its missing-prerequisite probe. | Setup implementation lane. |
| Opinionated worktrees and git flow | gap | fix-now | Initial tree had no `bin/worktree`; contributor flow used in-place `git checkout -b`. Guidance is corrected here; executable helper proof remains pending. | Worktree implementation lane. |
| `bin/test` full and diff | gap | fix-now | Native `make ci-fast`/`make ci` exist, but initial tree had no aggregate `bin/test` or merge-base-aware `--diff`. | Proof implementation lane. |
| CI matches local exactly | report-only | external policy | `.github/workflows/ci.yml` has pull-request and merge-group fan-in, while live ruleset `11732824` required 18 individual checks; sampled runs exceeded two minutes. | Repository owner/GitHub administrator. |
| Declared reproducible proof surfaces | gap | fix-now | Inventory maps native proof surfaces; canonical `bin/test`, failure proof, and replay evidence remain pending. | Proof implementation lane. |
| One-command service start | gap | fix-now | README/Compose paths existed, but Compose required `.env.local` and published all interfaces; planned proof is loopback listener, health, idempotence, and cleanup through `bin/dev`. | Runtime implementation lane. |
| All logs in a single file | gap | fix-now | `app/core/runtime_logging.py` provides structured console logs, but initial tree had no checkout-local aggregate file or `bin/logs`. | Runtime implementation lane. |
| Deploy ladder commands verified | report-only | target operations | Docker, Compose, and Helm delivery assets exist; no generic, fully exercised bootstrap/predeploy/deploy/smoke/rollback ladder was present. A target-specific ladder must be proved against its real environment. | Deployment owner. |
| Production health queryable | report-only | production evidence | Health, runtime, metrics, and structured log surfaces exist, but no approved production-source inventory was available in the repository assessment. | Production operator. |
| Observable resumable long operations | report-only | separate design | Release, migration, Helm smoke, and load scripts exist, but no unified run/checkpoint/resume contract was found. Adding one requires operation-specific ownership and failure semantics. | Owners of each long-running operation. |
| Repo enforces all this | gap | fix-now | Specialized CI and architecture guards exist; initial tree had no guard for repository operability entry points. Planned proof: `bin/check-operability` success and a deliberate fixture failure. | Operability implementation lane. |

## Evidence limits

- `uv run pytest -q tests/unit/test_account_mappers.py` passed six tests during
  inventory; it is representative proof, not the aggregate gate.
- `docker compose config --quiet` failed initially because `.env.local` did not
  exist; it must be replayed after setup remediation.
- GitHub ruleset and action observations are stale-sensitive and must be
  refreshed through the GitHub REST API before publication or landing.
- No deployment or credential mutation is evidenced by this repository report.
