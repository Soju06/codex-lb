# Why

`retry-accepted-output-free-capacity-failures` (#2127) replays an accepted,
output-free native Codex turn once within the single response lifecycle the
client is reading. On the HTTP bridge the replay reuses the fresh hard-request
path of `_retry_http_bridge_precreated_request`, which moves an
account-neutral request off the failing account by excluding it from the
reconnect selection. Every native Codex bridge session carries a hard session
key (`session_header` / `thread_header` / `turn_state_header`) and the
reconnect selects with the session affinity; when that affinity resolves a
hard `CODEX_SESSION` owner -- a turn-state row, or the raw legacy compatibility
row an old replica persisted for the bare session header
(`legacy_selection_key`) -- sticky selection is narrowed to that owner. With
the owner excluded every re-selection reports `hard_affinity_saturated`, which
the reconnect loop treats as a transient owner outage: it sleeps
`_HARD_AFFINITY_RECOVERY_SLEEP_SECONDS` per attempt until the bridge request
budget (7200s by default) is spent and only then fails the turn (`503` ->
`stream_incomplete`). `main` before #2127 failed the accepted shape closed
immediately; the direct WebSocket surface already refuses this exclusion
(`_websocket_affinity_may_resolve_hard_owner`, #2127 round 7).

# What Changes

- The HTTP bridge accepted-lifecycle replay (`replay_downstream_response_id`
  captured) on a hard session key MUST NOT exclude the account that accepted
  the turn when the session affinity may resolve a hard `CODEX_SESSION` owner
  (`CODEX_SESSION` kind, or any affinity consulting a raw legacy compatibility
  row). The replay reconnects through selection without an exclusion, exactly
  as the created-only transport-close replay did before accepted replays
  existed on the direct WebSocket surface: the hard row resolves to the owner
  again and the request is re-sent to it within the single lifecycle.
- The reconnect loop builds the replacement handshake before its
  post-connect account-change cleanup. It now offers the retained turn state
  only when selection returned the same account
  (`_http_bridge_reconnect_turn_state` in `http_bridge/helpers.py`). The
  unexcluded accepted replay skips the exclusion-driven turn-state cleanup of
  the retry path, so when its soft namespaced row moved the replay to another
  account (owner unselectable at reconnect time) the owner's turn state reached
  the replacement handshake -- a regression against `main`, which cleared it
  through the exclusion, and a violation of "Cross-account bridge retries clear
  turn-state". No cross-account bridge reconnect carries a retired account's
  turn state now, excluded or not; same-account reconnects are unchanged.
- The "may resolve a hard owner" predicate is shared by both surfaces
  (`_affinity_may_resolve_hard_owner` in `_service/support.py`); the direct
  WebSocket helper delegates to it.
- Unchanged (main parity): the created-only (pre-created) bridge replay keeps
  excluding the silent account; soft bridge session keys keep moving the
  accepted replay to another account; model-fallback replays keep excluding
  the rejecting account; hard keys whose affinity cannot resolve an owner keep
  the exclusion.
- Not changed: the reconnect loop's `hard_affinity_saturated` recovery wait is
  not capped. The only cheap cap ("repeats with an unchanged exclusion set")
  would also fail the legitimate wait for a briefly unavailable owner (the
  reason the short recovery sleep exists), and the precise cap (the resolved
  owner is in the exclusion set) needs the selector to report the owner and a
  change to `http_bridge/mixin.py` at its line ceiling.

# Capabilities

## Modified Capabilities

- `responses-api-compat`: HTTP bridge accepted replays on hard session keys
  keep a hard-capable Codex session owner eligible instead of excluding it.
- `responses-api-compat`: "Cross-account bridge retries clear turn-state"
  covers every reconnect that lands on a different account, not only the
  exclusion-driven replay.

# Impact

Native Codex bridge sessions whose bare session header still resolves a raw
legacy hard owner recover an accepted output-free capacity failure or abrupt
close on the owner within the single lifecycle, instead of spinning on
`hard_affinity_saturated` for up to the bridge request budget before a 502.
Soft session keys, created-only replays and the direct WebSocket surface keep
their existing behaviour.
A bridge reconnect that lands on a different account -- through an exclusion
or through a soft row moving an unexcluded replay -- opens the replacement
socket without the retired account's turn state; same-account reconnects keep
offering it.
