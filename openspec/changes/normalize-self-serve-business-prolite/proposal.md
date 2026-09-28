## Why

OpenAI usage payloads can identify a Business Premium account with the
upstream value `self_serve_business_prolite`. Codex-lb does not recognize that
identifier, so the workspace-less identity guard treats a legitimate paid-plan
transition as a mismatch and discards both the plan update and the usage
sample. The account therefore remains displayed with its previous plan even
though the refresh payload describes its current entitlement.

Codex-lb already models the corresponding entitlement as `prolite`, including
its capacity and Pro-equivalent model access. The upstream identifier is an
alias of that existing canonical plan, not a new local tier.

## What Changes

- Canonicalize the upstream plan identifier `self_serve_business_prolite` to
  the existing `prolite` account plan.
- Apply that canonicalization consistently to account metadata, usage capacity,
  rate-limit plan parsing, and model-plan eligibility.
- Add product-path regression coverage showing that a workspace-less Team
  account accepts the alias, persists `prolite`, and writes the usage sample.
- Preserve the current behavior for genuinely unknown future plan identifiers.

## Impact

- Affected capability: `usage-refresh-policy`.
- Business Premium accounts using the upstream alias refresh normally without
  reauthentication or manual account removal/re-addition.
- Existing stored `prolite` values, capacity values, and Pro-equivalent model
  access remain unchanged.
- No account, credential, or deployment mutation is required.
