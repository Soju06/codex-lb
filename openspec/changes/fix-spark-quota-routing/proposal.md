## Why

Separately metered Codex models can remain usable through fresh per-account quota telemetry even when the general upstream model catalog omits them. `codex-lb` currently treats that catalog omission as definitive account-level non-support and rejects `gpt-5.3-codex-spark` with `no_plan_support_for_model` before evaluating fresh Spark quota data.

## What Changes

- Allow fresh additional-quota telemetry to provide account-specific eligibility evidence for a mapped, separately metered model when the general per-account catalog omits that model.
- Preserve model plan and service-tier filtering before additional-quota availability, exhaustion, health, cooldown, and routing gates are evaluated.
- Carry the exact normalized quota-backed catalog-omission admission decision into HTTP bridge sessions so every compatible reuse entry point can return the selected upstream without synchronous quota I/O, while an incompatible request forks or fails without mutating another request's live session.
- Add regression coverage for fresh, missing, stale, and exhausted Spark quota evidence, two-turn HTTP bridge reuse, incompatible concurrent prompt-cache requests, and live alias preservation.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-catalog-compat`: Define how routing reconciles authoritative general account catalogs with fresh account-specific additional-quota evidence for separately metered models.

## Impact

- Account candidate filtering and admission provenance in `app/modules/proxy/load_balancer.py`.
- HTTP bridge request compatibility in `app/modules/proxy/_service/support.py`, `app/modules/proxy/_service/http_bridge/helpers.py`, and `app/modules/proxy/_service/http_bridge/mixin.py`.
- Spark and future separately metered model routing that uses the additional-quota registry.
- Focused load-balancer and bridge regression coverage; no API schema or database migration changes.
