## Context

See proposal.md. Durable alias resolution can return a `turn_state_header`
row while `_get_or_create_http_bridge_session` uses `_turn_keys` to choose a
thread/session fallback after a generated turn-state alias misses in memory.
The retry circuit and poison quarantine belong to the latter key.

## Goals / Non-Goals

Apply known fallback fences before serializing a proxy-injected anchor. Keep
same-owner input unchanged. Do not merge historical rows, alter alias ownership,
weaken submit-time race fences, or implement cross-account replay.

## Decisions

- Reuse `_turn_keys` so planning checks the same scoped fallback identity as
  acquisition; consult it only for full resends with generated turn states.
- Load its circuit before anchor planning and include it in quarantine and
  abandonment checks. Reuse the existing suppression flag through hydration
  and session-level injection rather than reconstructing requests after trim.
- Keep delta-only and explicit-anchor behavior on existing paths. Leave the
  submit-time guard intact for a circuit changing during planning.
- Use real routes, durable rows/aliases, authentication, and account selection
  in regression tests; stub only upstream transport/token refresh.

## Risks / Trade-offs

- One extra scoped key lookup on eligible reconnects: existing circuit-load
  cache bounds it; no fan-out or spawned task is introduced.
- Evidence may change after planning: existing submit-time checks remain.
- Two historical rows can persist: this fixes request planning without a
  destructive migration or unproven ownership merge.
