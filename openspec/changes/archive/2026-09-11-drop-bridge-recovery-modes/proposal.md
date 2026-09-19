## Why

`http_responses_session_bridge_ambiguous_continuation_recovery_mode` shipped
with four values, of which only the default `fail_closed` has ever run. All
four landed in one commit (`7a0b67192`, #1657, 2026-08-12); the env name
appears in no Helm chart, no `.env.example`, no operator doc, no issue and no
incident record. The claim that a non-default mode was a live mitigation
during the 2026-09-04 bridge 502 incident is refuted: that incident took the
`required_continuity_owner_missing` / `owner_account_unavailable` path, which
never reads this setting.

The three unused values are the expensive ones. Each is an explicitly
at-least-once delivery semantic — the bridge either asks the client to drop an
ambiguous anchor and resend its full history, or replays an anchored
`response.create` on a fresh upstream socket without any upstream idempotency
or status proof. Keeping them costs a fleet-global delivery-semantics selector
read at 12 sites on the request/stream hot path, a server-owned SSE recovery
loop in `app/modules/proxy/api.py`, a cooldown-wait state machine in the
bridge streaming generator, and ~14 test files' worth of coverage for
configurations nobody runs. The owner decided (2026-09-09 triage §5.1,
`NO — 기능 삭제`): keep `fail_closed`, delete the rest.

## What Changes

- **Behaviour at the shipped default is byte-identical.** Every deleted branch
  was reachable only when the setting held one of the three non-default
  values.
- The `Settings` field, its `Literal` type, its `SETTING_TIERS` row and its
  `MIGRATING` row are deleted;
  `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_AMBIGUOUS_CONTINUATION_RECOVERY_MODE`
  joins `_REMOVED_SETTINGS` for the one-release startup WARN.
- Deleted request-path machinery (all unreachable under `fail_closed`):
  - `helpers._http_bridge_server_anchored_replay_enabled`
  - `streaming._http_bridge_client_full_history_recovery_enabled` and its
    `request_submit` twin
  - `request_submit._http_bridge_operation_fence_for_hard_continuity_enabled`,
    `_http_bridge_operation_fenced_continuity_replay_allowed` and
    `_http_bridge_hard_continuity_full_history_recovery_error`, plus the
    `allow_operation_fenced_continuity_replay` cooldown bypass they fed in
    `retry_circuit._http_bridge_precreated_retry_allowed`
  - the `server_anchored_replay_once` / `server_indefinite_recovery` UNKNOWN
    claim branch in the submit path (the fail-closed
    `operation_already_recorded_no_status_proof` 503 is now the only outcome)
  - the streaming `operation_fenced_cooldown_wait_enabled` /
    `wait_through_operation_fenced_startup_cooldown` /
    `operation_fenced_request_budget_terminal_event` trio
  - `api.HTTP_BRIDGE_SERVER_RECOVERY_MAX_ATTEMPTS` (constantized by
    `constantize-session-bridge-tunables`; it bounded only the deleted loop),
    `_stream_response_error_events`'s `recovery_stream_factory` /
    `require_durable_recovery_fence` / `allow_client_full_history_once` /
    `scheduler` parameters and the recovery loop they fed,
    `build_recovery_response_stream`, `_http_bridge_recovery_request_eligible`
    and `_is_previous_response_not_found_recoverable_error`
  - the now write-only `http_bridge_durable_recovery_eligible` exception
    marker and `_http_bridge_durable_recovery_predecessor_proven`
  - the `_WebSocketRequestState.operation_recovery_claimed` field, which no
    code path could set to `True` any more
- `docs/reference/settings.md` regenerated; `[settings_fields].max` 96 -> 95.
- Tests: the cases that exercised the deleted modes are removed; the
  fail-closed ones stay, and
  `test_http_bridge_ambiguous_transport_never_attempts_local_recovery`
  is added to pin the classification for all three ambiguous transport codes.

## Impact

- Affected capability: `responses-api-compat`. Seven REMOVED requirements (all
  of which existed only to describe the deleted modes) and one ADDED
  replacement: "Eventless server-owned bridge recovery is bounded" cannot be a
  `MODIFIED` block because both its title and its only scenario state the
  deleted retry cap, so its still-live second paragraph (the terminal
  `response.failed` carries a stable `response.id`) moves verbatim into
  "Eventless bridge failures terminate with a stable response id".
- Operators: an environment that still sets the variable gets one startup WARN
  (values are never logged) and keeps today's behaviour, because today's
  behaviour was already `fail_closed` everywhere. A deployment that had
  actually set `client_full_history_once`, `server_anchored_replay_once` or
  `server_indefinite_recovery` loses an at-least-once delivery mode it opted
  into; there is no replacement, by design (issue: no upstream idempotency or
  status endpoint exists to make the retry safe).
- Out of scope, deliberately: the durable-bridge storage primitives that only
  the deleted dispatch path called
  (`DurableBridgeRepository.claim_unknown_operation_for_recovery`, its
  `max_recovery_dispatches` bound, the `restore_recovery_dispatch_claim` flag
  of `mark_operation_unknown`, and the `expected_recovery_dispatch_count` CAS
  fences that now always compare a constant 0) stay for now with their
  repository-level tests. Retiring them touches the storage layer and the
  `recovery_dispatch_count` column semantics, so it is a separate follow-up
  rather than a second concern in this PR. That follow-up also owns
  `openspec/specs/responses-api-compat` "Fenced one-shot recovery dispatch",
  which specifies exactly those primitives and stays accurate until they go.
