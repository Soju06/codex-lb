# Relax HTTP Bridge Owner Enforcement

## Why

The HTTP `/responses` bridge currently treats prompt-cache-derived bridge keys as hard replica ownership keys. In front of gateways such as LiteLLM, repeated requests often arrive on different replicas even when the logical prompt-cache key is stable. That turns a locality miss into `409 bridge_instance_mismatch`, which is stricter than the continuity guarantee prompt-cache routing actually needs.

## What Changes

- distinguish hard bridge continuity keys from soft prompt-cache locality keys
- keep strict owner enforcement for `x-codex-turn-state` and explicit session headers
- add an opt-in gateway-safe mode that tolerates prompt-cache locality misses and allows local bridge create/reuse
- add observability for prompt-cache locality misses and local soft rebinds

## Impact

- improves compatibility with upstream gateways that do not preserve codex-lb replica ownership
- keeps strict continuity semantics unchanged for turn-state and explicit session-id flows
- adds one dashboard/runtime setting and one dashboard_settings migration
