- [x] Delete `http_responses_session_bridge_ambiguous_continuation_recovery_mode`
  from `Settings`; add its env name to `_REMOVED_SETTINGS`; drop the
  `SETTING_TIERS` and `MIGRATING` rows.
- [x] `http_bridge/helpers.py`: delete `_http_bridge_server_anchored_replay_enabled`;
  make the ambiguous transport class (`stream_incomplete`,
  `stream_idle_timeout`, `upstream_request_timeout`) return `False`
  unconditionally from `_http_bridge_should_attempt_local_previous_response_recovery`.
- [x] `http_bridge/request_submit.py`: delete the client-full-history predicate,
  the hard-continuity operation fence and its cooldown-bypass lookup, the
  hard-continuity full-history error, the UNKNOWN recovery-claim branch and the
  `allow_operation_fenced_continuity_replay` retry-circuit bypass; keep the
  fail-closed `operation_already_recorded_no_status_proof` 503.
- [x] `http_bridge/streaming.py`: delete the client-full-history predicate and
  its four 400 fallbacks, the eventless 503 hold-open, the operation-fenced
  cooldown wait/budget helpers, and the write-only durable-recovery marker.
- [x] `proxy/api.py`: delete the server recovery loop, its cap constant, the
  recovery stream factory, the recovery-eligibility helpers and the
  `allow_client_full_history_once` parameter chain.
- [x] `_service/support.py`: drop `_WebSocketRequestState.operation_recovery_claimed`.
- [x] Regenerate `docs/reference/settings.md`; lower `[settings_fields].max` to 95.
- [x] Add the removed env name to the Helm chart README's 1.24 -> 1.25
  "Removed environment variables" register and correct the stale
  `..._SERVER_RECOVERY_MAX_ATTEMPTS` replacement text.
- [x] Delete the tests that configured a non-default mode; keep and extend the
  fail-closed coverage; extend `_REMOVED_SETTINGS` counts.
- [x] Spec delta: seven REMOVED and one ADDED requirement on `responses-api-compat`.
- [x] Verification: `make lint`, `uv run ty check`, `make migration-check`, the
  bridge/settings test files, `uv run pytest tests/unit`, the bridge and
  settings integration tests, simplicity budgets, `openspec validate --strict`
  and an archive simulation against a copy of main's `openspec/`.
