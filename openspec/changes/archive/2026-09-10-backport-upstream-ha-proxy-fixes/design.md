## Context

See proposal.md for motivation. Baseline is fork commit 7ecb38de; upstream is 0f6a31c5. Production already contains the usage-group migration 20260910_000000_add_api_key_usage_group. This change is a selective backport, not a beta.6 merge.

## Goals / Non-Goals

Preserve fork dispatch-owner checks, byte-bounded native streaming, cancellation seams, and settlement-before-health invariants while adapting four upstream patches. No upstream transport redesign, migration reparenting, config removals, dependency updates, or deployment.

## Decisions

- Apply the upstream version warmup before leader election in the existing scheduler, using the existing hourly version cache. Preserve cancellation instead of detaching new tasks. Keep the explicit fallback override.
- Carry trusted subscription provenance through optional transport arguments. Derive hints only from normalized payload or request state; never copy inbound hints. Preserve requested versus actual tier separation.
- Sanitize HTTP headers before native identity detection. Remove fixed and Connection-nominated hop fields, then regenerate mandatory proxy-owned headers. Preserve existing WebSocket policies.
- Adapt upstream burst retry into the fork's current owner-binding and settlement flow. Burst cooldown is an immediate, replica-local admission signal; deferred transient health writes remain after reservation settlement and must not extend cooldowns. Successful same-owner retry has no deferred transient penalty.
- Keep bounded retries on the existing scheduler/clock seams. Own and cancel the extra startup-probe wait task, allowing consecutive waits to retain HTTP error propagation.

## Risks / Trade-offs

- Different upstream/fork retry baselines can erase ownership guards: apply hunks narrowly and test file, turn-state, payload, and single-account owners, post-refresh errors, cancellation, and settlement failures.
- Header nomination can spoof native identity or remove required credentials: classify sanitized identity and regenerate mandatory headers, with product-path compact tests.
- Mixed replicas have different local burst cooldowns: intentional short-lived state; no shared schema or persistent status mutation is introduced.
- Cache warmup is asynchronous and disabled with the scheduler: the updated fallback covers cold/disabled lookup; no network fetch is added to request handling.

## Migration Plan

No schema change. Validate locally with synthetic SQLite databases and mocked transports, including existing key-dashboard migration coverage. Preserve the deployed migration byte-for-byte. Commit, further push, and HA surge deployment remain separate operator actions; no production state is mutated here.
