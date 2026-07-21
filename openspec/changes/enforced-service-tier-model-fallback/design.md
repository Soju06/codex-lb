## Context

`Complete account catalogs constrain pooled routing` is written in terms of an
**explicit** non-default service tier: "requests that omit a tier or use the
omit-equivalent `auto` or `default` tiers MUST use model-only account
filtering", and its Fast-tier scenario is scoped to "**WHEN** a request
explicitly asks for priority".

An API-key-enforced tier is neither of those. `apply_api_key_enforcement`
overwrites the payload's `service_tier` with the key's enforced value, so by the
time selection runs, an operator default and a client request are the same
string with no way to tell them apart. The enforced case therefore fell into the
explicit-request rule by accident rather than by decision.

## Decision

Carry the distinction into selection rather than changing what an explicit
request means.

- `_service_tier_is_api_key_enforced` in the proxy derives the signal from the
  `ApiKeyData` that `_select_account_with_budget` already receives, so no new
  plumbing crosses the proxy boundary.
- `select_account(..., service_tier_enforced=False)` passes it to the account
  filter. The default preserves current behavior for every other caller.
- `ModelRegistry.model_advertises_service_tier` answers whether the catalog
  lists the tier for the model at all, which is deliberately different from "no
  account carries it". It returns `True` whenever the answer is unknown (no
  snapshot, non-authoritative catalogs) so an unknown catalog never triggers the
  fallback.
- Only when the tier is enforced **and** the model does not advertise it is the
  tier constraint dropped.

The selection-inputs cache key gains the flag: the same tier string now resolves
different account pools depending on its origin, so the two must not share an
entry.

## Alternatives considered

**Fall back for any unadvertised tier.** Rejected. It inverts the behavior
pinned by `test_select_account_rejects_quota_override_for_unadvertised_service_tier`,
where an explicit `flex` request against a model with no advertised tiers must
select nothing. A caller who names a tier should be told it is unavailable, not
quietly served a different one.

**A new error code for the tier-excluded case.** Rejected for now. It widens the
external error envelope for a case the corrected message already explains. The
tier is now named in the message instead.
