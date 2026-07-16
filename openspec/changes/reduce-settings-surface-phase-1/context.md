# Context: reduce-settings-surface-phase-1

## Rationale

Phase 1 of issue #1340. PRINCIPLES.md P2 treats every setting the operator
never needs to touch as a default in disguise. This batch was selected
because removal is provably zero-risk: every removed field kept its exact
previous default as the new fixed value, none is referenced by
`openspec/specs/**`, `.env.example`, `docs/`, or the Helm chart, and the only
behavioral seam (the removed-settings warning) is additive.

Capability choice: `deployment-installation` owns the operator env-var
contract at settings-load time (see the data-directory resolution
requirement), so the fixed-constants + removal-warning requirement lives
there rather than in `contribution-simplicity`, which governs the
contribution/review process, not runtime behavior.

## Removed fields (env names)

OAuth protocol constants (now module constants in
`app/core/config/settings.py`):

- `CODEX_LB_AUTH_BASE_URL` (fixed: `https://auth.openai.com`)
- `CODEX_LB_OAUTH_CLIENT_ID` (fixed: `app_EMoamEEZ73f0CkXaXp7hrann`)
- `CODEX_LB_OAUTH_ORIGINATOR` (fixed: `codex_chatgpt_desktop`)
- `CODEX_LB_OAUTH_SCOPE` (fixed: `openid profile email`)
- `CODEX_LB_OAUTH_REDIRECT_URI` (fixed: `http://localhost:1455/auth/callback`)
- `CODEX_LB_OAUTH_CALLBACK_PORT` (fixed: `1455`; OpenAI dislikes changes)

Auth guardian tuning (now constants in `app/core/auth/guardian.py`):

- `CODEX_LB_AUTH_GUARDIAN_INTERVAL_SECONDS` (fixed: 21600)
- `CODEX_LB_AUTH_GUARDIAN_MAX_REFRESH_AGE_SECONDS` (fixed: 43200)
- `CODEX_LB_AUTH_GUARDIAN_BATCH_SIZE` (fixed: 100)
- `CODEX_LB_AUTH_GUARDIAN_CONCURRENCY` (fixed: 3)
- `CODEX_LB_AUTH_GUARDIAN_JITTER_SECONDS` (fixed: 300.0)
- `CODEX_LB_AUTH_GUARDIAN_FAILURE_BACKOFF_BASE_SECONDS` (fixed: 300.0)
- `CODEX_LB_AUTH_GUARDIAN_FAILURE_BACKOFF_MAX_SECONDS` (fixed: 3600.0)

Debug log booleans (replaced by `CODEX_LB_TRACE` channels):

- `CODEX_LB_LOG_PROXY_REQUEST_SHAPE` -> channel `shape`
- `CODEX_LB_LOG_PROXY_REQUEST_SHAPE_RAW_CACHE_KEY` -> channel
  `shape_raw_cache_key`
- `CODEX_LB_LOG_PROXY_REQUEST_PAYLOAD` -> channel `payload`
- `CODEX_LB_LOG_PROXY_SERVICE_TIER_TRACE` -> channel `service_tier`
- `CODEX_LB_LOG_UPSTREAM_REQUEST_SUMMARY` -> channel `upstream_summary`
- `CODEX_LB_LOG_UPSTREAM_REQUEST_PAYLOAD` -> channel `upstream_payload`

Bulkhead per-class overrides (always derived from
`CODEX_LB_BULKHEAD_PROXY_LIMIT`):

- `CODEX_LB_BULKHEAD_PROXY_HTTP_LIMIT` (= proxy limit)
- `CODEX_LB_BULKHEAD_PROXY_WEBSOCKET_LIMIT` (= proxy limit)
- `CODEX_LB_BULKHEAD_PROXY_COMPACT_LIMIT` (= min(http, 16); 0 when http is 0)

Token-refresh claim polling (now constants in
`app/modules/accounts/auth_manager.py`):

- `CODEX_LB_TOKEN_REFRESH_CLAIM_WAIT_SECONDS` (fixed: 8.0)
- `CODEX_LB_TOKEN_REFRESH_CLAIM_POLL_SECONDS` (fixed: 0.25)

Kept deliberately: `CODEX_LB_OAUTH_TIMEOUT_SECONDS`,
`CODEX_LB_OAUTH_CALLBACK_HOST` (container-dependent),
`CODEX_LB_TOKEN_REFRESH_CLAIM_TTL_SECONDS` (its floor validation against
`proxy_admission_wait_timeout_seconds + 2 * token_refresh_timeout_seconds`
protects the cross-replica single-use refresh-token invariant and references
settings that remain configurable), `CODEX_LB_AUTH_GUARDIAN_ENABLED`,
`CODEX_LB_BULKHEAD_PROXY_LIMIT`, `CODEX_LB_BULKHEAD_DASHBOARD_LIMIT`.

## Deprecation policy

`extra="ignore"` on `Settings` means removed env vars were already inert the
moment the fields were deleted; the startup WARN
(`warn_removed_settings()` in `app/core/config/settings.py`, called from the
`app/main.py` lifespan) is one release of courtesy so operators notice stale
configuration. The warning lists names only, never values, and is removed
together with `_REMOVED_SETTINGS` in a later release.

## Example

An operator running `CODEX_LB_LOG_UPSTREAM_REQUEST_PAYLOAD=true` upgrades:
startup logs

```
removed setting(s) ignored: CODEX_LB_LOG_UPSTREAM_REQUEST_PAYLOAD — values are now fixed; see PRINCIPLES.md P2 / issue #1340
```

and the equivalent incident-debugging behavior is re-enabled interactively
with `CODEX_LB_TRACE=upstream_payload`.
