# Architecture

This document is a map of the implementation, not a behavioral specification.
Observable requirements live in [`openspec/specs/`](openspec/specs/), and proposed
behavior changes live in [`openspec/changes/`](openspec/changes/).

## System boundary

codex-lb is a FastAPI service and bundled dashboard that accepts Codex and
OpenAI-compatible requests, selects an eligible authenticated account, proxies
the request upstream, and records operational state. The repository includes
the application, dashboard, persistence layer, specifications, tests, packaging,
Docker/Compose assets, and a Helm chart. OpenAI, client applications, databases,
observability backends, and deployment platforms are outside the repository.

## Request and control flow

```text
Codex/OpenAI client                  Browser/operator
         |                                 |
         +---------- FastAPI --------------+
                         |
              middleware and auth
                         |
       +-----------------+------------------+
       |                                    |
 proxy API and routing              dashboard/control APIs
       |                                    |
 account eligibility, quota,         settings, accounts,
 affinity, admission, settlement     keys, audit, runtime
       |                                    |
       +----------- persistence ------------+
                         |
             OpenAI/Codex upstream APIs
```

[`app/main.py`](app/main.py) is the composition root. Its lifespan initializes
the database, shared HTTP client, caches, schedulers, runtime health, and
shutdown behavior, then mounts routers and the dashboard assets.

## Load-bearing areas

| Area | Owner paths | Responsibility |
|---|---|---|
| Proxy request path | `app/modules/proxy/`, `app/core/upstream_proxy/`, `app/core/openai/` | Compatibility endpoints, admission, account selection, forwarding, streaming, continuity, and settlement. |
| Accounts and routing | `app/modules/accounts/`, `app/core/balancer/`, `app/core/usage/`, `app/modules/sticky_sessions/` | Credential-backed account eligibility, quota state, routing strategies, and session locality. |
| Authentication and policy | `app/modules/oauth/`, `app/modules/api_keys/`, `app/modules/dashboard_auth/`, `app/modules/firewall/`, `app/core/auth/` | OAuth lifecycle, client and dashboard authentication, and request policy boundaries. |
| Persistence | `app/db/`, `app/db/alembic/` | SQLite/PostgreSQL sessions, models, migrations, backup, and recovery. |
| Operations | `app/modules/health/`, `app/modules/runtime/`, `app/core/metrics/`, `app/core/runtime_logging.py` | Health/readiness, drain state, metrics, and structured logs. |
| Dashboard | `frontend/src/`, `app/modules/dashboard/` | Vite/React user interface and its backend surface. |
| Delivery | `Dockerfile*`, `docker-compose*.yml`, `deploy/helm/codex-lb/` | Container and Kubernetes packaging. |
| Contracts and proof | `openspec/`, `tests/`, `Makefile`, `scripts/check_proxy_architecture.py` | Normative behavior, regression proof, and architecture constraints. |

## Trust and persistence boundaries

- OAuth access/refresh tokens and the encryption key are secrets. They cross
  the upstream boundary only through the authenticated account and proxy path.
- API-key and dashboard authentication guard independent client and operator
  surfaces. A first-run dashboard bootstrap token can appear in process logs,
  so logs are sensitive until initialization is complete.
- SQLite state is local by default; PostgreSQL is the shared-state option.
  Database files and the matching encryption key must be backed up and restored
  as a pair.
- OAuth callback port `1455`, application port `2455`, metrics, replica bridge
  traffic, and telemetry export are separate network surfaces. Their exposure
  is a deployment decision; development commands bind the application to
  loopback.
- Multi-replica continuity depends on ring membership and the HTTP response
  session bridge. The corresponding requirements live under deployment
  networking and runtime-portability OpenSpec capabilities.

## Change routing

1. Locate the relevant capability in `openspec/specs/`.
2. For behavior, API, schema, compatibility, or operator-contract changes,
   create an OpenSpec change before implementation.
3. Keep dependency direction toward the module or core owner above; do not put
   new proxy-domain behavior in `app/main.py`.
4. Run `bin/test --diff <base>` while iterating and `bin/test` before delivery.
   `bin/check-operability` verifies that the repository-level entry points stay
   present and connected.

## Repository operations

| Need | Canonical command |
|---|---|
| Prepare a checkout | `bin/setup` |
| Start locally | `bin/dev` |
| Follow the aggregate local log | `bin/logs` |
| Run all proof | `bin/test` |
| Run merge-base-aware proof | `bin/test --diff <base>` |
| Create/list/remove worktrees | `bin/worktree` |
| Check operability invariants | `bin/check-operability` |

Deployment-specific behavior is documented by the relevant OpenSpec capability
and delivery asset; this map intentionally does not promote one infrastructure
provider to product architecture.
