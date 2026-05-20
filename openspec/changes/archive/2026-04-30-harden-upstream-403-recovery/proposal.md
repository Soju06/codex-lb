# harden-upstream-403-recovery

## Why

`codex-lb` now has deterministic pre-commit failover for several upstream failure classes, but ambiguous upstream `403 Forbidden` responses still fall into an awkward gap:

- websocket connect failures can stay client-visible even when another healthy account could have served the request
- the affected account is not immediately cooled down, so reconnect loops can keep picking the same bad candidate
- background model-registry refresh keeps retrying ambiguous `403` responses with low-context logs, which creates operator noise without improving serving health

The long-term contract should be narrower than the historical "surface websocket `403` loudly" rule. `codex-lb` must still avoid silently downgrading transport, but it should treat pre-commit ambiguous `403` failures as account-scoped connect failures, retry another eligible account on the same transport, and emit enough telemetry to explain what happened.

## What Changes

- add a `responses-api-compat` requirement for pre-commit ambiguous websocket `403` failover on the same transport, plus short account cooldown
- add a new `model-registry-refresh` capability so background model fetch follows the same cooldown discipline as usage refresh instead of hammering ambiguous `401`/`403` failures
- extend `proxy-runtime-observability` with structured upstream `403` diagnostics and counters for failover, drain transitions, and client-exposed pre-commit errors

## Impact

- Specs: `responses-api-compat`, `proxy-runtime-observability`, `model-registry-refresh`
- Runtime: websocket connect failover, account runtime cooldowns, model registry refresh resilience, structured logging, Prometheus metrics
- Tests: proxy failover coverage, load-balancer cooldown coverage, model refresh cooldown coverage, metrics coverage
