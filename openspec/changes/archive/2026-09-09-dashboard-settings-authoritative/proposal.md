# Dashboard settings are authoritative over the environment

## Why

Two settings that live in both the environment and the dashboard resolve in
the wrong direction, so an operator edit in one place is silently ignored by
the other:

- The four account-capacity overrides (`proxy_account_response_create_limit`,
  `proxy_account_stream_limit`, `proxy_account_stream_recovery_reserve`,
  `proxy_api_key_fair_share_congestion_threshold_pct`) are documented as
  "NULL inherits the environment", but the first-boot seed copies the
  environment value into the row as a non-NULL override. On every fresh
  install the inherit state never exists: later `CODEX_LB_PROXY_ACCOUNT_*`
  changes do nothing, while the API and UI keep labelling the (new)
  environment value as the inherited baseline.
- `CODEX_LB_TELEMETRY_ENABLED` overrides a persisted dashboard telemetry
  decision. `PUT /api/settings/telemetry` still returns 200 and stores the
  decision, but it has no effect while the variable is set, and the dashboard
  toggle is disabled.

The configuration policy for this repository is one fixed precedence:
code default < environment < dashboard. The environment is a fallback, never
a seed that is copied into the database and never a value that overrides a
persisted dashboard decision.

## What Changes

- First-boot seed: the settings row is created with `NULL` for the four
  account-capacity overrides so the effective value inherits the process
  environment until an operator stores an override. No data migration: a
  migration cannot know the environment value the row was seeded from, so
  existing rows keep their stored override and the API/UI now report it
  truthfully as a dashboard override (the operator can clear it with the
  existing explicit-`null` update).
- Telemetry consent resolution becomes `persisted decision > environment >
  default`. `CODEX_LB_TELEMETRY_ENABLED` applies only while the persisted
  state is `undecided`, which keeps the headless opt-out before the first
  dashboard visit working. A saved decision is authoritative and is reported
  with `source: persisted` even when the variable is set.
- The dashboard telemetry toggle stays usable while the environment value is
  in effect; the notice explains that the environment value is the current
  fallback and that saving a choice takes precedence.
- An environment-active to dashboard-disabled transition is now a
  dashboard-driven opt-out and sends the single opt-out notice like any other
  dashboard-driven active-to-inactive transition.

## Relationship to other changes

This change supersedes the "A settings row created for the first time MUST
persist the process environment values" sentence in `proxy-admission-control`
wherever it appears:

- in the main spec,
- in the pending `clear-dashboard-capacity-overrides` change (its delta for
  `Dashboard-configurable account concurrency caps` is otherwise kept in full:
  the tri-state update paragraph and all of its scenarios are carried into
  this change's delta so archive order cannot regress either side), and
- in the `codify-configuration-tiers` change, which rewrites the same sentence
  as part of the general configuration-tier policy.

It also supersedes, in the pending `add-telemetry-optout-signal` change, the
clause "MUST NOT be sent for an environment-controlled consent path" and the
"Environment kill switch stays silent" scenario of `Dashboard opt-out
notification`. Under this change the environment only controls consent while
no decision is persisted, so persisting a decision ends environment control
and an environment-active to dashboard-disabled transition is a dashboard-driven
opt-out that sends the single notice. The environment-only opt-out path
(`CODEX_LB_TELEMETRY_ENABLED=false`, nothing persisted) stays completely
silent. This change's telemetry delta carries the full rewritten requirement;
it MUST be archived after `add-telemetry-optout-signal` (or together with it)
so the MODIFIED block has a requirement to replace.

## Impact

- `GET /api/settings` on a fresh install reports `null` for the four
  `*Override` fields and the environment value as the effective value.
- `GET/PUT /api/settings/telemetry` report `source: persisted` for a saved
  decision regardless of `CODEX_LB_TELEMETRY_ENABLED`; `source: env` now
  means "no decision saved yet, the environment decides".
- Frontend: telemetry toggle no longer disabled for `source === "env"`;
  notice copy updated (en, ko, zh-CN).
- Docs: `docs/telemetry.md` consent section, telemetry spec context,
  `docs/deployment/kubernetes.md` cap bullet, `proxy-admission-control`
  spec context.

## Non-goals

- No change to the admission arithmetic or to how the cache snapshot is read.
- No migration of existing capacity override rows.
- No change to `upstream_stream_transport` (handled by a separate change).
