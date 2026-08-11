# Quarantine silent HTTP bridge sessions

## Summary

An HTTP bridge session that has proven silent/wedged must stop attracting new attach attempts. Two shapes prove it: a reattached stream (proxy-injected `previous_response_id`) that delivers upstream response events but never gets `response.created` assigned, and a session key that hits repeated eventless `missing_response_created_timeout` retires. Mark such a session key quarantined — bounded, in-memory, session-scoped, never account-scoped — so that later requests neither reuse the session nor rebuild the same durable-anchor reattach, and instead complete on the existing fresh session/no-anchor path that production evidence in [#1534](https://github.com/Soju06/codex-lb/issues/1534) shows already works.

This is the maintainer-chosen direction from [#1405](https://github.com/Soju06/codex-lb/pull/1405) (quarantine, chosen over the account-health penalty alternative #1574), revived post-#1394/#1563 as a first-party takeover with the original review findings addressed.

## Why

The merged silent-upstream machinery recovers the request that is currently stuck, and one flavor of the durable state behind it:

- `recover-codex-desktop-idle-bridge` / #1394 bound the eventless `response.create` wait, added the bounded pre-created retry, and back off repeated failures through the durable retry circuit.
- `invalidate-durable-bridge-anchor-after-stuck-timeout` / #1563 clears the durable anchor after a *fully eventless* timeout, for full-resend-shaped payloads.

Both key on `response_event_count == 0`. The production wedge in #1534 is different: the reattached stream delivered response events whose `response.created` was never assigned. That shape never trips the eventless deadline (it disarms once events flow), never reaches the fenced anchor clear, and refuses in-place replay because model output was seen — so the request fails terminally, the durable anchor survives, and the next turn rebuilds the identical anchored reattach to the same wedged state. The client can only recover by starting a fresh session; the recoverable path exists but the running session can never reach it.

The original #1405 attacked this with a 5s `response.created` timeout plus a raw-HTTP fallback window. The maintainer review found the timeout arms only while `response_event_count == 0` (so the observed production wedge never trips it) and that 5s false-positives retire healthy sessions. This change keeps #1405's core idea — a bounded per-key quarantine window — and re-triggers it on proof rather than on a racy timeout: the wedge shape is only ever evaluated when a request is already being failed or its session retired, never against a live owned turn, so deferred-reasoning streams with long legitimate event gaps (the P1 documented on #1580) cannot be quarantined; any request whose `response.created` was observed is excluded by construction.

## What Changes

- Add a bounded in-memory quarantine registry keyed by HTTP bridge session key (the same key the retry circuit uses). No new settings; fix-class, default-on, zero-config.
- Trigger 1 (immediate): when a pending request being failed or retired proves the wedge shape — HTTP transport, proxy-injected `previous_response_id`, `response.create` sent, upstream response events observed, `response.created` never assigned — quarantine the session key. Hooked at the reader failure/retire funnel and the stale-gate-holder cleanup.
- Trigger 2 (repeats): count consecutive eventless `missing_response_created_timeout` retires per session key; quarantine at the second. The first stays on the merged #1394 recovery path (bounded retry, #1563 anchor clear).
- Effect 1: a quarantined live/retained session is excluded from re-attach/session-reuse selection (`_http_bridge_session_reusable_for_lookup`), so a new request detaches it and creates a fresh session instead.
- Effect 2: while a key is quarantined, the fresh-reattach durable-anchor injection is skipped for full-resend-shaped payloads: the client's own payload already carries the whole conversation, so it goes upstream unanchored on the existing fresh path. Delta-only payloads keep the anchor — it is their only way to convey prior context — matching the scope boundary of `invalidate-durable-bridge-anchor-after-stuck-timeout`.
- Recovery/expiry (bounded, no leak): a completed response on the key clears the quarantine and its strike counter; otherwise entries expire after a 600-second TTL (aligned with the retry circuit's max backoff) and the registry is pruned and size-capped. In-memory only — no durable rows, no janitor involvement, and process restart clears it.
- Account-neutral: quarantine decisions never write account health and never move or exclude accounts; durable continuity ownership is preserved on the fresh path.
- Observability: low-cardinality `session_quarantined`, `session_quarantine_cleared`, and `fresh_reattach_anchor_skipped_quarantined` bridge events.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `responses-api-compat`: An HTTP bridge session that proved silent/wedged (reattached stream with response events but no `response.created`, or repeated eventless timeouts) is quarantined for a bounded window: later requests do not reuse it and full-resend reattaches skip the durable anchor, taking the existing fresh path instead.

## Non-Goals

- No change to the eventless watchdog, its deadline, the bounded pre-created retry, or the durable retry circuit (#1394) — quarantine acts only on *later* requests, after those have run for the in-flight one.
- No change to the fenced durable-anchor clear (#1563); quarantine does not write the durable session row at all.
- No change for delta-only payloads' anchor injection: same boundary as #1563, their proxy-injected anchor is load-bearing and stays. A wedged delta-only client still benefits from Trigger/Effect 1 (no reuse of the wedged session) and from #1394's bounded failure, but its reattach anchor is not stripped.
- No account-health, routing, or account-selection changes.
- No new settings, no persistence, no dashboard surface.
