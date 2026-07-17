## Context

Direct WebSocket stale-anchor incidents currently log `continuity_owner_resolution` and `continuity_fail_closed`, but not the fields needed to classify the root cause. Operators can see that owner lookup hit and upstream rejected the anchor, but cannot tell whether the rejected anchor came from the downstream client or from proxy session-continuity injection, whether a no-anchor fresh replay body existed, or whether the anchor crossed Codex turn sessions.

## Goals / Non-Goals

**Goals:**

- Add structured stale-anchor diagnostics to existing logs and request-log failure metadata.
- Make post-incident SQL/log analysis able to classify owner lookup, anchor source, replay availability, anchor age, and same-session relationship.
- Keep diagnostics bounded and privacy-safe by logging hashes/booleans/enums, not raw response ids or payload bodies.

**Non-Goals:**

- No retry-policy or routing behavior changes.
- No new database columns or dashboard UI changes.
- No raw payload capture.

## Decisions

- Reuse existing `failure_phase` / `failure_detail` request-log fields for durable metadata, encoding detail as compact key-value text. This avoids a migration while making queries immediately more useful.
- Add structured logger fields at the `continuity_fail_closed` site because that log line is already the operator anchor for stale incidents.
- Derive `previous_response_source` from request-state flags: `proxy_injected` when codex-lb rewrote the upstream body by trimming/replacing a client full resend, `client_supplied` when the inbound payload already carried `previous_response_id`, and `unknown` only when state is insufficient.
- Derive `fresh_replay_available` from the existing retry-safe fresh upstream request state rather than re-inspecting payloads.
- Derive age and same-session relationship from owner lookup metadata when available; missing values stay explicit as `unknown`/`None` instead of guessed.
- Treat account-only request-cache hits as owner-session metadata unknown. The cache does not retain the matched request-log row, so inferring `same_session=true` from the current lookup scope would produce false evidence after a cross-session fallback.

## Risks / Trade-offs

- `failure_detail` becomes a compact diagnostic string instead of a single reason token for stale-anchor failures. This improves incident analysis but requires consumers to treat it as structured text, not a fixed enum.
- Without new columns, SQL filtering on individual metadata fields is text-based. A later dashboard/reporting change can promote stable fields to first-class schema if needed.
- Source classification depends on existing request-state bookkeeping. Tests cover client-supplied and proxy-injected paths so regressions are visible.
