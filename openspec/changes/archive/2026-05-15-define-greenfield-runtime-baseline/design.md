# define-greenfield-runtime-baseline

## Overview

The greenfield runtime baseline is the recommended shape for a new codex-lb instance that will handle sustained Codex traffic. It is not another optional mode; it is the sane default for serious new deployments. The zero-config SQLite path remains available for local smoke usage.

## Goals

- Preserve the operator's chosen Codex CLI tier setting while making requested-vs-actual tier verification explicit.
- Keep SQLite available for simple local startup while steering new sustained, write-heavy, multi-worker serving to PostgreSQL.
- Ensure bridge-enabled multi-worker serving uses addressable bridge workers, preserving Responses continuity.
- Make the minimum production observability explicit enough that slow-tail and upstream-tier issues can be diagnosed without ad hoc DB spelunking.

## Non-Goals

- Do not add a daily fast mode.
- Do not add an automatic daily prompt-budget, reasoning-effort, output-token, or model-routing policy.
- Do not remove, discourage, or override ultrafast; existing ultrafast support remains available as the operator's usual requested tier.
- Do not require PostgreSQL for zero-config local startup.

## Decisions

### Tier verification, not tier replacement

The greenfield baseline should not change the operator's Codex CLI tier preference. If the operator currently requests `ultrafast`, keep requesting `ultrafast`. The operational problem is verification: request logs or metrics must show whether upstream actually serves the requested tier or returns a different actual tier.

Alternative considered: recommend switching the baseline to priority. Rejected because the operator wants Codex CLI to keep requesting ultrafast; the correct spec is to verify whether ultrafast is legit, not to replace it.

### PostgreSQL before workers

The greenfield baseline should not tell operators to add worker concurrency on SQLite. SQLite remains supported for simple local serving with one request worker or equivalent write serialization. Sustained multi-worker serving should be documented and configured around PostgreSQL via CODEX_LB_DATABASE_URL.

Alternative considered: keep SQLite and rely on retry/backoff. Rejected for the greenfield baseline because retries reduce transient lock failures but do not turn SQLite into the preferred high-write-concurrency backend.

### Bridge-safe worker model

When the HTTP Responses bridge is enabled, multi-worker serving must preserve addressable bridge ownership. Plain Uvicorn multi-worker mode with a shared bridge instance id is not a valid greenfield baseline because each process has separate in-memory bridge sessions. The baseline should use the addressable bridge worker pool path or disable the bridge before plain multi-worker serving.

### Metrics and alerts

The greenfield baseline needs an operator-visible health and performance surface, not just local /health/ready checks. Metrics and alert guidance should cover container/runtime health, readiness, request success rate, p95 latency or equivalent histogram, upstream failure classes, stream incomplete/timeout errors, database lock pressure, and service-tier mismatch counts.

This can be implemented with existing Prometheus support plus documentation and tests. If the current metrics surface lacks a required signal, add a small metric rather than forcing operators to parse raw logs or copy SQLite databases for routine operations.

## Failure Modes

- Upstream accepts an ultrafast request but returns default or auto; this is not automatically a local proxy failure, but it must be visible in request logs and metrics.
- SQLite sustained serving may look healthy at /health/ready while request logging or bridge writes suffer lock pressure; the greenfield baseline must steer that case to PostgreSQL.
- Multiple workers with bridge-enabled plain Uvicorn can lose bridge continuity; the greenfield baseline must use the addressable worker path.
- Health endpoints can stay green while slow-tail latency degrades; the greenfield baseline must include request-level latency and error observability.

## Rollout Notes

Implementation should update OpenSpec requirements, env examples, README/operator guidance, and focused tests first. Runtime config changes for any specific host should be separate and explicit; the spec should not silently mutate host config or live Docker env files.
