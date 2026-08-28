# v1-usage-limits-upstream-fallback

## Why

Legacy `/v1/usage` consumers (for example, OpenCodeBar-style status widgets) only read the `limits[]` array and ignore `upstream_limits[]`. When an API key has no self-service limits configured, those clients render "no limits" even though the aggregate upstream Codex quota windows are already computed and exposed to the same caller via `upstream_limits[]`. The information is visible either way; only the legacy field stays empty.

## What Changes

- `GET /v1/usage` populates the legacy `limits[]` array with the already-visible aggregate upstream quota windows (`upstream_limits[]`) when the authenticated API key has no limits of its own.
- Explicit API-key limits stay preferred: when the key has configured limits, `limits[]` contains only those limits, exactly as before.
- `upstream_limits[]` itself is unchanged, and visibility rules are unchanged: when upstream quota details are hidden from the key (for example via `hide_upstream_quota_from_api_keys`), `limits[]` stays empty rather than leaking hidden data.

## Scope

- Backend `/v1/usage` response assembly only.
- No database schema changes.
- No frontend changes.
- No new data exposure: the fallback only mirrors quota windows the caller can already read from `upstream_limits[]`.

## Impact

- Modified capability: `api-keys` (requirement "API keys can read their own `/v1/usage`").
- Code: `app/modules/proxy/api.py` (`v1_usage` handler).
- Tests: `tests/integration/test_v1_usage.py` regression coverage for the mirrored `limits[]` payload.
