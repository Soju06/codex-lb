# define-greenfield-runtime-baseline

## Why

Live operation now shows a difference between a proxy that answers health checks and a proxy that is suitable as a new always-on Codex gateway. The project needs one greenfield sustained-runtime baseline for new serious deployments instead of a separate pile of optional modes.

## What Changes

- Preserve current Codex CLI tier behavior; if an operator usually requests `ultrafast`, the greenfield baseline keeps that request unchanged.
- Require an operator-facing verification path that compares requested tier to upstream actual served tier.
- Make PostgreSQL the greenfield baseline before recommending sustained multi-worker serving.
- Keep bridge-enabled multi-worker serving on the addressable bridge worker pool path rather than a plain shared-id worker group.
- Require an operator-facing metrics and alerting bundle for the greenfield baseline, including health, request success, latency, upstream failures, database lock pressure, and container restart/OOM signals.
- Do not add a daily fast mode, daily prompt-budget routing policy, or automatic reasoning/output downgrade policy.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- database-backends: define PostgreSQL as the greenfield backend baseline for sustained multi-worker serving while preserving SQLite for local/smoke startup.
- deployment-installation: define the Docker/operator greenfield baseline and its worker/database constraints.
- proxy-runtime-observability: define the minimum metrics and alerting surface for the greenfield baseline.

## Impact

- Affects operator guidance, env examples, Docker/runtime baseline docs, and potentially deployment helpers.
- Affects service-tier verification documentation and greenfield observability.
- Does not change public route shapes or require PostgreSQL for simple local startup.
- Does not remove existing ultrafast support and does not recommend changing the current Codex CLI tier setting.
