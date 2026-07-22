## Why

An API key's `enforced_service_tier` is an operator-wide default, but account selection treats it exactly like a tier the client asked for. When the enforced tier is `priority` and a model never advertises `priority`, the authoritative catalog answers "no account carries priority for this model" with an empty set, every account is filtered out, and the request fails with `no_plan_support_for_model`. The accounts do support the model; they support it at its default tier.

Reported in #1409: enforcing `Priority` on a key made `gpt-5.4-mini` and `codex-auto-review` unusable, while `gpt-5.5` and `gpt-5.6-sol` kept working. Clearing the enforced tier restored both.

The reported error is also misleading. `No accounts with a plan supporting model '<model>'` points an operator at a plan problem that does not exist.

## What Changes

- Ignore an API-key-enforced service tier for a model whose catalog never advertises that tier, so the model routes at its default tier instead of excluding every account.
- Keep rejecting a tier the client requested explicitly, including the quota-override path, so an explicit ask is never silently downgraded.
- Name the service tier in the selection error when the tier is what excluded the accounts.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-catalog-compat`: Distinguish an operator-enforced service tier from an explicitly requested one when an authoritative account catalog constrains routing.
- `responses-api-compat`: Preserve API-key tier enforcement while allowing the account-catalog fallback to remove an unsupported enforced tier from the effective upstream request.
