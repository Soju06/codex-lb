# Invalidate Bridge Anchors Upstream Has Denied

## Why

The HTTP responses session bridge keeps re-injecting a `previous_response_id` that upstream has already said does not exist (issue #1852).

When upstream answers an anchored bridge request with `previous_response_not_found`, that is a verdict about the anchor. If the proxy injected the anchor itself, no client asked for the id, it came from the proxy's own durable record, and upstream has now refused it. Nothing in the bridge acts on that verdict:

1. Anchor poisoning cannot see it. `_http_bridge_anchor_poison_detail` only scores reader failures whose detail is `stream_incomplete` or `stream_idle_timeout`. A denial arrives as a terminal upstream event, not a reader failure, so it contributes nothing at any value of `http_responses_session_bridge_anchor_poison_failure_threshold`. Lowering the threshold does not help.
2. The dead id therefore survives in both carriers, the durable `latest_response_id` row and the in-memory `session.last_completed_response_id`, and the fresh-reattach path injects it into the next turn.
3. On that next turn the store-context trim matches the stored prefix and strips it, because the trim consults the stored fingerprint and never whether the anchor is still alive. Upstream then receives a few items instead of the conversation, never emits `response.created`, and the attempt presents as an eventless failure rather than as a stale anchor.

Two of those eventless failures open the retry circuit, so the client sees `503 ... cooling down`. Measured over 12.7 h on one host: 163 `continuity_fail_closed` rejections, 29 circuit opens, and a worst-case trim of `original_items=602 trimmed_to=3`.

The second half of this change is why the first half currently could not fire even if it existed. The anchored recovery replays the proxy's own anchor but never copies `proxy_injected_previous_response_id` onto the retry state, so a denial of the replayed anchor is not attributable to the proxy. The same gap misreports `previous_response_source=client_supplied` for ids no client sent and keeps `_http_bridge_request_state_wedged_reattach` from recognising the reattach shape it exists to catch.

## What Changes

- Retire only the denied `previous_response_id` on the first explicit upstream denial when the proxy injected it, instead of waiting for a counter that this failure class never increments. The fenced durable write clears the four anchor-bound columns only while that id is still the durable latest response, deletes only that response alias, and preserves turn-state plus other response aliases. The in-memory carrier is cleared in a `finally` path even when alias unregistering fails. The retirement is skipped when a concurrent request has already advanced the anchor past the denied id.
- Mark the denied id before the durable await and revalidate prepared requests immediately before dispatch. A request that already captured the denied proxy-injected id is failed closed without another upstream send, closing the retirement/dispatch race.
- Retain denial provenance in a bounded process-local generation ledger. A request that captured the durable anchor before a canonical live session existed is still failed closed if a detached predecessor denies that id while owner lookup or successor session creation is suspended.
- Treat an already-recorded denial as a hard observation for later durable recaptures and owner-forward recovery injections, so failed durable cleanup or an initially unanchored recovery request cannot redispatch the same id at equal generation.
- Carry `proxy_injected_previous_response_id` onto the anchored recovery retry state, so a denial of the replayed anchor is attributable, diagnostics report the real provenance, and the wedge classifier sees the reattach. Anchor-free recovery paths keep the flag false because they send no anchor.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: An explicit upstream previous-response denial retires a proxy-injected anchor immediately, and anchored recovery retries retain the provenance of the anchor they replay.

## Impact

- HTTP bridge terminal-event handling (`app/modules/proxy/_service/http_bridge/upstream_events.py`) and anchored recovery retry state (`app/modules/proxy/_service/http_bridge/streaming.py`).
- Denial-fence lifecycle state (`app/modules/proxy/_service/http_bridge/helpers.py`), service protocol members (`app/modules/proxy/_service/http_bridge/protocol.py`), single-alias unregistration (`app/modules/proxy/_service/http_bridge/session_registry.py`), and the fenced durable anchor clear (`app/modules/proxy/durable_bridge_repository.py`, `app/modules/proxy/durable_bridge_coordinator.py`).
- No API, schema, migration, dependency, configuration, or dashboard changes. The poison threshold setting and its default of seven are untouched, and the downstream error contract is unchanged: the denial is still masked to `stream_incomplete` and still surfaces as 502, so clients keep their anchor and do not resend full history (the invariant from #397).
- The session's turn-state and unrelated response-id aliases remain routable after retirement; only the denied response-id alias is removed.

## Non-Goals

- Adding another upstream dispatch. This change never resends the turn. The next turn is the client's own, with the history the client sends, so no forked child response can be created against a parent the proxy cannot observe. Retrying the turn server-side without the anchor is what #1857 and #1863 propose and is deliberately out of scope here.
- Changing the poison threshold arithmetic that issue #1852 is titled after.
- Exposing the stale-anchor classifier downstream on the bridge path.
