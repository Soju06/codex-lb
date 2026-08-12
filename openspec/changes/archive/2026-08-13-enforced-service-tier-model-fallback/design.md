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

Capture the distinction before API-key enforcement mutates the request, then
resolve the account-route effective tier after model-source selection.

- `apply_api_key_enforcement` reports whether it supplied the tier by observing
  the original request value before the enforced value is written. Equal values
  and canonical aliases therefore remain client-explicit when the client sent
  them.
- Source-routed requests keep the enforced tier. The fallback is an
  authoritative subscription-account catalog decision and does not describe an
  external model source's capabilities.
- `ModelRegistry.model_advertises_service_tier` answers whether the catalog
  lists the tier for the model at all, which is deliberately different from "no
  account carries it". It returns `True` whenever the answer is unknown (no
  snapshot, non-authoritative catalogs, or a model absent from the account
  catalog) so an unknown catalog never triggers the fallback.
- Only when the tier is enforced **and** the model does not advertise it is the
  tier removed from the account-routed request. Account selection, HTTP bridge
  compatibility, API-key reservation and settlement, request logging, and the
  upstream wire payload then all consume that same effective request tier.

## Alternatives considered

**Fall back for any unadvertised tier.** Rejected. It inverts the behavior
pinned by `test_select_account_rejects_quota_override_for_unadvertised_service_tier`,
where an explicit `flex` request against a model with no advertised tiers must
select nothing. A caller who names a tier should be told it is unavailable, not
quietly served a different one.

**A new error code for the tier-excluded case.** Rejected for now. It widens the
external error envelope for a case the corrected message already explains. The
tier is now named in the message instead.
