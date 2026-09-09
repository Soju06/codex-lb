# Warm the Codex Client Version on Every Replica

## Why

Non-native SDK requests are forwarded with the Codex CLI fingerprint `codex_cli_rs/<version>`; upstream gates newer models on that version. The version cache (`CodexVersionCache`) was only ever filled inside the leader's model refresh (`fetch_models_for_plan`). A replica that is not the scheduler leader therefore never fetched it and fell back to `model_registry_client_version` (`0.144.0`) forever. On soju07 the live color (`green`, 1.25.0-beta.5) was exactly that: the standby (`blue`) still held the lease, `green` logged no version fetch for an hour, and 346 of 14,904 non-native `gpt-6-astra` requests in 24 h were rejected with "The 'gpt-6-astra' model requires a newer version of Codex" while native `codex_cli_rs/0.153.4` traffic was never rejected (#2170).

## What Changes

- The model refresh loop warms the Codex client version cache on every tick, before the leader-gated refresh, on every replica. The lookup is the existing public GitHub/npm release lookup (no account credential), cached for an hour, and a failure is logged without stopping the tick.
- The fallback `model_registry_client_version` moves from `0.144.0` to the current public release `0.153.4`, so even a cold cache no longer presents a version upstream already gates.

## Capabilities

### Modified Capabilities

- `outbound-http-clients`: the cached fingerprint version is warmed and refreshed per replica regardless of leadership; the fallback tracks the current release.

## Impact

- `app/core/openai/model_refresh_scheduler.py` (`_warm_codex_version_cache`, called from `_run_loop`), `app/core/config/settings.py` (default bump), `docs/reference/settings.md` regenerated, tests in `tests/unit/test_model_refresh_scheduler.py` and `tests/unit/test_codex_version.py`.
- No new settings, schema, API or dashboard changes. One extra public HTTPS lookup per replica per hour.

Fixes #2170.
