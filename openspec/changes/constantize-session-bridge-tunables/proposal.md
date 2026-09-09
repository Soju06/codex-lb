## Why

Seven HTTP session bridge tunables were never tuned in any deployment, yet
each one costs a `Settings` field, a tier row, a `MIGRATING` row, a docs row,
a Helm key and a `getattr(..., default)` seam at every read site
(PRINCIPLES.md P2: a value that is never changed is a default, not a setting).
The anchor-poison threshold was already clamped to the circuit threshold at
every one of its 13 read sites, so its shipped default of 7 was unreachable by
construction.

## What Changes

- Fixed module constants replace the following `Settings` fields; the
  `CODEX_LB_*` env names join `_REMOVED_SETTINGS` for the one-release WARN:
  - `http_responses_session_bridge_idle_ttl_seconds` -> `HTTP_BRIDGE_IDLE_TTL_SECONDS` (120s)
  - `http_responses_session_bridge_codex_idle_ttl_seconds` -> `HTTP_BRIDGE_CODEX_IDLE_TTL_SECONDS` (900s)
  - `http_responses_session_bridge_stuck_gate_retire_after_seconds` -> `HTTP_BRIDGE_STUCK_GATE_RETIRE_AFTER_SECONDS` (300s)
  - `http_responses_session_bridge_anchor_poison_failure_threshold` -> the circuit threshold `_HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD` (2); the capping helper and its 13 seams are gone
  - `http_responses_session_bridge_server_recovery_max_attempts` -> `HTTP_BRIDGE_SERVER_RECOVERY_MAX_ATTEMPTS` (6)
  - `http_responses_session_bridge_clean_close_retry_jitter_max_seconds` -> `_HTTP_BRIDGE_CLEAN_CLOSE_RETRY_JITTER_MAX_SECONDS` (2.0s)
  - `http_responses_session_bridge_operation_ledger_enabled` -> always on; the three `if ledger enabled` branches are deleted
- `http_responses_session_bridge_enabled` is retiered T3 -> T4: it is the
  request-path kill switch, not a tunable, so it leaves `MIGRATING`.
- Helm: the two idle-TTL configmap keys and values are removed; pod identity
  env (`POD_NAME`/`POD_NAMESPACE`/`POD_IP`, bridge instance id, advertise URL)
  is injected unconditionally because it is replica topology, independent of
  the kill switch.
- `docs/reference/settings.md` regenerated; `[settings_fields].max` 130 -> 123.

## Impact

- Affected capability: `responses-api-compat` (four MODIFIED requirements:
  the jitter maximum, the server recovery cap, the stuck-gate budget and the
  operation-ledger scenario are fixed values instead of runtime settings).
- Behaviour at shipped defaults is unchanged. An operator who had set one of
  the seven env names sees one startup WARN (values are never logged) and the
  fixed default applies.
- Tests keep every injection seam by monkeypatching the module constants
  (`helpers.HTTP_BRIDGE_*`, `retry_circuit._HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD`,
  `api.HTTP_BRIDGE_SERVER_RECOVERY_MAX_ATTEMPTS`).
- Out of scope, deliberately: `http_responses_session_bridge_operation_spool_retention_seconds`
  stays a setting (privacy/retention, future Data retention card).
