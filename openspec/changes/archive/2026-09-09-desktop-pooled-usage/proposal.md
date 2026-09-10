## Why

Codex Desktop can route inference through codex-lb while its native usage poll still sees only the signed-in ChatGPT account. An exhausted primary account can therefore restrict model selection despite available pool capacity. Keeping that ChatGPT identity and its connected capabilities is the reason to use the proxy.

## What Changes

- Add an opt-in, Desktop relay with loopback host exposure on the existing client-compatible port 8000. Route native usage through a strict LB pool projection and preserve the original caller on other fixed-destination ChatGPT backend requests.
- Preserve original account identity, plan, credits, spend restrictions, billing and reset-credit ownership. Replace only quota fields supported by fresh pool evidence and remove only superseded quota-exhaustion markers.
- Reject unavailable or stale pool evidence without inventing capacity. Keep existing inference and native API-key usage behavior compatible.
- Document Desktop launch configuration, version limits, coordinated activation, recovery and rollback. Verify with isolated tests before an approved real Desktop trial.

## Capabilities

### New Capabilities

- `desktop-pooled-usage`: Native Desktop quota composition, fixed-destination relay, original identity preservation, opt-in launch setup and acceptance evidence.

### Modified Capabilities

None. Existing `/api/codex/usage` and inference contracts remain unchanged.

## Impact

Python CLI, an optional standalone or lifespan-owned relay, a strict quota endpoint and pool projection, route-level tests, `docs/client-setup.md`, and a linked Desktop setup page. Uses the established HTTP stack. No database migration, default service listener change, app modification, new required base-install setting, or automatic Desktop restart.
