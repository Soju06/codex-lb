## Why

Three environment variables were consumed by ad-hoc `os.environ` reads outside `app/core/config/settings.py` — `CODEX_LB_CONNECT_ADDRESS` (dashboard connect-address override), `CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE` (additional quota registry path), and `FORWARDED_ALLOW_IPS` (Uvicorn proxy-projection trust list). None appeared in the generated settings reference, none could be set through `.env` / `.env.local`, and the removed-settings warning and reference generator could not see them. This is the config-silo slop catalogued as S9/S14 in the 0908 slop-removal inventory.

## What Changes

- Promote the three env names to `Settings` fields (`connect_address`, `additional_quota_registry_file`, `forwarded_allow_ips`) with the same env names. `FORWARDED_ALLOW_IPS` keeps its bare Uvicorn name as the primary alias and gains `CODEX_LB_FORWARDED_ALLOW_IPS` as the prefixed alias; its trust semantics are unchanged.
- Consumers (`TrustedProxyHeadersMiddleware`, the connect-address resolver, the additional quota registry loader) read `get_settings()` instead of the process environment.
- The settings reference generator renders alias env names and adds a "Process-level environment variables (not settings)" section documenting the remaining sanctioned bare env reads (Uvicorn launch knobs, outbound proxy family, `TZ`, `PROMETHEUS_MULTIPROC_DIR`, `GITHUB_TOKEN`, `REQUEST_METHOD` httpoxy guard, Kubernetes pod identity, Codex CLI home discovery, `CODEX_LB_TEST_DATABASE_URL`, frozen Alembic migration reads).
- The settings-surface ratchet moves from 135 to 138 for the three promoted fields; they are existing knobs made visible, not new tunables.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: proxy projection trust is sourced from the `forwarded_allow_ips` setting (env `FORWARDED_ALLOW_IPS`, alias `CODEX_LB_FORWARDED_ALLOW_IPS`) with Uvicorn semantics preserved; the "MUST NOT introduce a new setting" clause scoped to the original capture-before-projection change is replaced by the setting-sourced contract.
- `user-documentation`: the generated settings reference renders alias env names for settings whose primary env name is unprefixed and documents process-level environment conventions that are intentionally not settings.

## Impact

Affected code: `app/core/config/settings.py`, `app/core/middleware/trusted_proxy_headers.py`, `app/modules/settings/api.py`, `app/modules/usage/additional_quota_keys.py`, `scripts/generate_settings_reference.py`, `docs/reference/settings.md`, and the tests that set these env names (they now clear the `get_settings` cache). No API schema, database, dependency, or default-behaviour change: unset values resolve exactly as before. The Alembic backfill migration `20260312_000000` keeps its direct env read because migrations must not depend on `Settings`.
