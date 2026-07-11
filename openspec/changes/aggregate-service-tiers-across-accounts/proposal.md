## Why

When codex-lb serves multiple ChatGPT/Codex accounts, the model registry
fetches the catalog per plan/account and merges same-slug models
last-writer-wins. An account without Fast (speed-tier) entitlement returns empty
`service_tiers`/`additional_speed_tiers` for a shared slug, and that empty list
overwrites the richer metadata contributed by a Fast-capable account. The shared
`GET /backend-api/codex/models` then advertises `service_tiers: []` for that
slug, so the Codex app hides the Fast option for every account even though at
least one configured account supports it (issue #1100).

## What Changes

- Aggregate speed/service-tier metadata as a union across plans/accounts when
  merging same-slug models in the model registry, instead of overwriting
  last-writer-wins.
- A slug's `service_tiers`, `additional_speed_tiers`, and `default_service_tier`
  reflect the union of every contributing account's entitlement, so Fast stays
  visible while any account supports it and an account lacking Fast cannot strip
  it. Union entries are de-duplicated.
- All other model fields keep their existing last-writer-wins behavior.

## Impact

- Backend: `app/core/openai/model_registry.py` (`ModelRegistry.update` merge)
- Endpoints: `GET /backend-api/codex/models`, `GET /v1/models` (tier metadata is
  surfaced from the merged registry snapshot)
- Specs: `openspec/specs/model-catalog-compat/spec.md`
