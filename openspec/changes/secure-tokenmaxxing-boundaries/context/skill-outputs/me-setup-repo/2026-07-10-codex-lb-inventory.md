# codex-lb setup inventory

## Target

- Repo root: `/code/codex-lb/.worktrees/me-setup-repo`
- Branch/worktree: `codex/me-setup-repo`, tracking `origin/main`
- Base revision: `65dc4b75be2de837968dcdf86ec233f5d5f0ad72`
- Initial dirty state: clean
- Upstream: `Soju06/codex-lb`, public, default branch `main`
- Deployment target: Hetzner Cloud `cpx21` in `ash`, Ubuntu 24.04, Tailscale-only steady state

## Standards source

The checklist was read from `/code/malaysian-engineering/skills/_shared/malaysian-repo-standards/README.md` and every Markdown file in that directory on 2026-07-10.

## Existing surfaces

| Surface | Evidence | Initial observation |
|---|---|---|
| Human/agent entry docs | `README.md`, `AGENTS.md`, `.github/CONTRIBUTING.md` | Present; `AGENTS.md` is 130 lines and `CLAUDE.md` links to it. |
| Architecture | `openspec/specs/**`, `scripts/check_proxy_architecture.py`, `DECISIONS.md` | No root `ARCHITECTURE.md`; architecture knowledge is distributed. |
| Setup | `uv.lock`, `frontend/bun.lock`, `.env.example`, Make targets | Dependencies are pinned; no one-command fresh setup surface found. |
| Start | README Docker/uvx examples, Compose files | Starts are documented; Compose requires `.env.local`; published ports default to all interfaces. |
| Logs | Console logging in `app/core/runtime_logging.py` | Structured/timestamped logging exists; no checkout-local aggregate log command found. |
| Proof | `Makefile`, `.github/workflows/ci.yml`, extensive tests | Strong native checks; no `bin/test` or diff-scoped aggregate found. |
| CI | GitHub Actions and active ruleset `11732824` | `pull_request` and `merge_group` exist; ruleset requires 18 individual checks rather than one fan-in. |
| Deploy | Docker, Compose, Helm chart, release workflows | Installation modes exist; no repo-native bootstrap/predeploy/deploy/smoke/rollback ladder for a VM target found. |
| Runtime health | health/metrics/logging implementation and specs | Runtime signals exist; approved read-only production source inventory not found. |
| Long operations | release, migrations, Helm smoke, load scripts | Native operations exist; no unified run/checkpoint/resume declaration found. |
| Worktrees | Git native only | No `bin/worktree`; `.worktrees/` is not yet confirmed ignored. |
| Enforcement | CI, architecture and release guards | Strong specialized guards; no guard covering the full repo-operability contract. |

## Probe results

| Probe | Result | Evidence limit |
|---|---|---|
| `git status --short` | verified clean at inventory start | Does not assess upstream state. |
| `gh api repos/Soju06/codex-lb/rulesets/11732824` | verified active ruleset, no bypass | No merge-queue rule was present in the response. |
| `gh api repos/Soju06/codex-lb/actions/runs?per_page=10` | verified recent CI success | Sampled recent runs only. |
| `make help` | verified documented targets exist | Help does not prove targets pass. |
| `docker compose config --quiet` | failed: `.env.local` missing | Setup/start not yet verified. |
| `uv --version`, `docker --version`, `docker compose version` | verified available | Does not prove repository compatibility. |
| Hetzner inventory via path-scoped Infisical injection | verified account access and existing resources | No resource was changed. |
| `herdr agent list` | verified nine Codex and two Claude panes | Agent goals/checkpoints not yet inventoried. |

## Capability map

| Capability | Native owner | Command/procedure | Verdict/artifact | Preconditions | Cleanup/redaction | Limit |
|---|---|---|---|---|---|---|
| Static/unit/package proof | `Makefile` | `make ci-fast` | Exit code and tool output | Python 3.13, uv, Bun | Build artifacts removed by package target | Omits integration/Postgres/Docker/Helm. |
| Full proof | `Makefile` | `make ci` | Exit code and CI-equivalent slices | Docker, Postgres, Helm, kind, kubeconform, Trivy | Removes kind cluster only if scripts succeed | Not yet run; likely long and resource-heavy. |
| Service start | README/Compose | `docker compose up` or `uvx codex-lb` | HTTP service on port 2455 | `.env.local` for Compose | Named volume contains credentials | Default host publishing is unsafe on this VPS. |
| Runtime logs | application console | Docker/platform logs | Timestamped structured records | running service | Must redact OAuth and API material | No single checkout-local file declaration. |
| Deployment | Helm/Compose | documented install commands | running service | target-specific infra and secrets | platform-specific teardown required | No complete VM deploy ladder. |
| Session migration | Herdr + Codex session IDs | inventory/checkpoint/stop/restart/resume | agent list and successful continued goal | codex-lb proven, session ID known | preserve panes and worktrees | Most panes do not currently report a session ID. |

## Inferences

- Stack: FastAPI/Python 3.13 backend, Bun/Vite frontend, SQLite or PostgreSQL state, Docker/Helm delivery.
- The repository is mature product code but is brownfield relative to the requested operability standards: several strong existing systems conflict with or only partially satisfy the checklist.
- A dedicated deployment adapter must remain generic and provider-safe; ME-specific routing text must not enter host-owned instruction files.
- The user selected the closest Ashburn option after live inventory showed `cpx22` unavailable there: `cpx21` in `ash` at the observed price of about USD 37.49/month.
- The credential boundary is a migration boundary: codex-lb becomes the sole durable refresh-token writer after cutover.

## Repo gestalt

- Boundary: application, dashboard, specs, tests, packaging, CI, Docker/Compose, and Helm live in this repository; OpenAI, GitHub, databases, observability backends, clients, and Hetzner are external.
- Load-bearing entry points: `app/main.py` composes lifespan and routers; `app/modules/proxy/` owns admission, routing, forwarding, continuity, and settlement; account/balancer/usage modules own eligibility; `app/db/` and Alembic own persistence; `frontend/src/` owns the dashboard; OpenSpec is the behavioral source of truth.
- Operating logic: optimize resilient Codex/OpenAI-compatible execution across account quotas while preserving session locality, accounting correctness, and graceful degradation. Operator settings choose routing, while OpenSpec, architecture ratchets, CI, and merge rules constrain implementation.
- Trust boundaries: OAuth grants and encryption keys, API/dashboard auth, first-run bootstrap token, callback port 1455, persistent account/usage/audit data, multi-replica bridge traffic, and telemetry export.
- Native proof: Make aggregates, architecture guard, test slices, migration/package/container/Helm targets, OpenSpec validation, health endpoints, structured logs, Prometheus metrics, and drain status.
- Holes: no canonical architecture overview, no generic VM deployment ladder, no checkout-local aggregate log command, no approved production-source inventory, and no pinned target topology before this run.
- Highest-stakes contradiction: upstream Compose/README examples publish ports on all interfaces, while the CLI defaults to loopback and this host forbids steady-state public inbound ports.
- Fitness: sufficient for standards assessment and deploy-readiness analysis; not proof that checks pass or that the production topology is safe.

## Standards assessment dispatch plan

Topology: fan-out-and-synthesize. The official checklist has independent documentation, proof/CI, and runtime/deployment clusters that benefit from fresh read-only contexts.

| Lane | Scope | Mode | Output |
|---|---|---|---|
| docs-map | agent markdown, architecture, setup, worktree/git | subagent | structured verdict per standard |
| proof-ci | aggregate proof, CI parity, proof declaration, repo guard | subagent | structured verdict per standard |
| runtime-ops | start, logs, deploy ladder, production health, long operations | subagent | structured verdict per standard |

All lanes are read-only, may not spawn children, and must cite live commands/files. The coordinator owns classification, edits, external writes, and synthesis.
