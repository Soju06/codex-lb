## MODIFIED Requirements

### Requirement: Native Codex preserves upstream failure lifecycle

For a native Codex HTTP/SSE Responses request, an upstream transport timeout or
stream EOF without a terminal Responses event MUST terminate the downstream
stream without synthesizing `response.failed`, `error`, or `[DONE]`. The proxy
MUST still execute reservation, request-log, account-health, and owned-resource
cleanup before propagating the termination. Non-native and OpenAI-compatible
clients MUST retain the existing stable terminal-error shaping.

This rule applies only to failures the proxy observed on the upstream leg. A
failure the proxy manufactured itself before any upstream frame was sent for the
request, and which it records as a local pre-dispatch refusal, MUST NOT be
delivered as a silent transport termination to a native Codex client. When the
downstream response has not committed, such a refusal MUST be returned as its
HTTP status with the error envelope in the body. When the downstream response
has already committed, it MUST be delivered as a terminal `response.failed`
event carrying the same error code, followed by `[DONE]`, and that terminal MUST
NOT be marked as a synthetic transport failure. A native Codex client MUST NOT
receive a committed `200` with zero bytes and no terminal event for a recorded
local pre-dispatch refusal.

The proxy MUST record that provenance at least on the HTTP bridge's
denied-anchor before-dispatch fence, and MUST carry it across an internal bridge
owner forward so the origin instance reaches the same verdict as the owner. The
other local refusals that reuse the transport error codes are not recorded as
such yet and keep the transport lifecycle until they are; that bound is
deliberate, because not all of them are provably pre-dispatch.

#### Scenario: Native Codex sees a truncated SSE lifecycle

- **GIVEN** a native Codex HTTP request has received a non-terminal SSE event
- **WHEN** upstream closes without a terminal event
- **THEN** downstream closes without a synthetic terminal event or `[DONE]`
- **AND** proxy cleanup and failure accounting still complete

#### Scenario: Non-native client keeps the terminal umbrella

- **GIVEN** an OpenAI SDK or other non-native client receives the same upstream
  truncation
- **WHEN** codex-lb normalizes the stream
- **THEN** the client receives the existing terminal `response.failed` shape

#### Scenario: Native Codex sees a local pre-dispatch refusal before commit

- **GIVEN** a native Codex HTTP Responses request
- **WHEN** the proxy refuses it before sending any upstream frame and the
  downstream response has not committed
- **THEN** the client receives the refusal's HTTP status and error envelope
- **AND** the body is not an empty event stream

#### Scenario: Native Codex sees a local pre-dispatch refusal after commit

- **GIVEN** a native Codex HTTP Responses request
- **WHEN** the proxy refuses it before sending any upstream frame and the
  downstream response has already committed
- **THEN** the client receives a terminal `response.failed` with the same error
  code
- **AND** `[DONE]`
- **AND** the terminal is not marked as a synthetic transport failure

### Requirement: Explicit upstream previous-response denials retire proxy-injected anchors

When upstream answers an HTTP bridge request with a `previous_response_not_found` terminal frame, and the `previous_response_id` on that request was injected by the proxy onto a full-resend-shaped payload, the proxy MUST retire that anchor on the first denial rather than waiting for the eventless-failure poison threshold. Retirement MUST clear the durable anchor only when the denied id is still the durable latest response and the session owner fence still matches; the write MUST clear the four anchor-bound fields and delete only the matching response-id alias, preserving turn-state and sibling response aliases. The proxy MUST clear the in-memory session carrier even if durable cleanup or alias unregistering fails.

Before awaiting durable cleanup, the proxy MUST publish the denied id to the live session. Publication MUST be serialized with the submitter's final tombstone check and upstream send so either an already-started send finishes first or publication wins and fences that send. Immediately before dispatch, any already-prepared request carrying that id as a proxy-injected anchor MUST fail closed without sending another upstream frame. This revalidation MUST close the retirement/dispatch race; it MUST NOT reject a client-supplied anchor merely because the same id is tombstoned for proxy injection.

The proxy MUST also retain the denied-id generation in a bounded process-local ledger independent of the canonical live-session registry. A request that captured the durable anchor before a live session existed MUST fail closed when that generation advances during owner lookup or successor session creation. Active requests MUST pin their ledger entries until finalization so pruning cannot remove a fence that is still needed.

When a request captures a proxy-injected anchor after this process has already recorded a denial for that id, the request MUST retain that denial observation and fail closed before dispatch even when the captured generation equals the current denial generation. Owner-forward recovery that injects a durable anchor MUST perform the same capture and denial observation; it MUST NOT rely on provenance copied from an initially unanchored request.

The proxy MUST NOT retire the anchor when:

- the anchor was supplied by the client, because removing it changes the meaning of the client's own request;
- the anchor was injected onto a payload that is not full-resend shaped, because a delta-only request has no other way to convey prior context once its anchor is gone;
- the session's current anchor is no longer the denied id, because a concurrent request may have completed and advanced it.

When a durable clear raises, the proxy MUST NOT report the anchor as retired, MUST still clear the in-memory anchor, and MUST retain the bounded cleanup retry so a transient failure is not lost. When the durable clear returns no matching row, the proxy MUST treat that no-match as a terminal fenced outcome for this cleanup attempt and MUST NOT spend the retry budget on it; it MUST preserve the local alias and denial fence because the durable owner or latest anchor may have advanced. A durable record that still carries the denied id can then be retired by the matching owner rather than by a stale epoch.

Retirement is bookkeeping and MUST NOT change how the denial is delivered downstream. A failure while retiring MUST NOT propagate into terminal-event handling.

A denial that settles several requests sharing one anchor MUST retire that anchor once, on the same terms.

The downstream error contract is unchanged: the denial is still reported to the client as `stream_incomplete`, so the client retains its own anchor and is not driven into a full-history resend.

The before-dispatch fail-closed MUST reach the client. It MUST be surfaced as the `stream_incomplete` error with its HTTP status when the downstream response has not committed, and as an unmarked terminal `response.failed` carrying `stream_incomplete` when it has, including for native Codex clients. When the fail-closed happens on an internal bridge forward target, that instance MUST identify its error response as a local pre-dispatch refusal, and the origin instance MUST honour that identification when it rebuilds the failure, so a forwarded turn reaches the same delivery as a local one.

#### Scenario: A denied proxy-injected anchor is retired immediately

- **GIVEN** an HTTP bridge session whose stored anchor was injected by the proxy
- **WHEN** upstream answers the anchored request with `previous_response_not_found`
- **THEN** the proxy clears the durable continuity record under the session's owner epoch
- **AND** clears the in-memory session anchor and its stored input count and prefix fingerprint
- **AND** the next turn on that session dispatches without a `previous_response_id`

#### Scenario: The following turn is not trimmed against a denied anchor

- **GIVEN** a proxy-injected anchor was denied by upstream on the previous turn
- **WHEN** the client sends a full resend of the conversation on the next turn
- **THEN** the request MUST NOT be trimmed against the denied anchor's stored prefix
- **AND** upstream receives the resent conversation rather than a suffix of it

#### Scenario: A concurrent completion protects the current anchor

- **GIVEN** a proxy-injected anchor is denied by upstream
- **AND** another request on the same session completed first and advanced the session anchor to a different response id
- **WHEN** the denial is handled
- **THEN** the proxy MUST NOT clear the session anchor
- **AND** it MUST still tombstone the denied id so an already-prepared proxy-injected request cannot dispatch it

#### Scenario: Client-supplied anchors are left alone

- **GIVEN** an HTTP bridge request carries a `previous_response_id` the client supplied
- **WHEN** upstream answers it with `previous_response_not_found`
- **THEN** the proxy MUST NOT retire the anchor on the client's behalf

#### Scenario: A delta-only payload keeps its injected anchor

- **GIVEN** the proxy injected an anchor onto a payload that is not full-resend shaped
- **WHEN** upstream answers that request with `previous_response_not_found`
- **THEN** the proxy MUST NOT clear the anchor
- **AND** the request keeps the only reference it has to its prior context

#### Scenario: A fan-out denial retires the shared anchor once

- **GIVEN** several pending requests on one session share a proxy-injected anchor
- **WHEN** upstream answers with a single `previous_response_not_found` that settles all of them together
- **THEN** the proxy retires that anchor before the grouped settlement completes

#### Scenario: A prepared denied anchor is rejected before dispatch

- **GIVEN** a request was prepared with a proxy-injected anchor
- **AND** another request receives `previous_response_not_found` for that anchor before the prepared request reaches the upstream send
- **WHEN** the prepared request reaches its final dispatch check
- **THEN** the proxy fails it closed as `stream_incomplete`
- **AND** the proxy MUST NOT send that denied anchor upstream again

#### Scenario: Denial publication wins against a prepared dispatch

- **GIVEN** a request is prepared with a proxy-injected anchor while another request receives `previous_response_not_found` for that anchor
- **WHEN** denial publication acquires session lifecycle ownership before the prepared request's final send section
- **THEN** the denied id is tombstoned before the prepared request revalidates
- **AND** the prepared request fails closed without sending an upstream frame

#### Scenario: A detached predecessor fences an absent-session capture

- **GIVEN** a request captures a proxy-injected durable anchor before a canonical live session exists
- **AND** a detached predecessor receives `previous_response_not_found` for that anchor while the request is resolving ownership
- **WHEN** successor session creation completes and the request reaches final dispatch
- **THEN** the process-local denial generation MUST fail the request closed as `stream_incomplete`
- **AND** the successor MUST NOT send the denied anchor upstream

#### Scenario: A stale durable recapture remains fenced after cleanup failure

- **GIVEN** a detached predecessor records a denial for a proxy-injected anchor
- **AND** durable anchor cleanup fails, leaving the durable row unchanged
- **WHEN** a later request captures that same durable anchor after the denial was recorded
- **THEN** the request MUST retain the existing denial observation
- **AND** it MUST fail closed as `stream_incomplete` before dispatch

#### Scenario: Owner-forward recovery observes an existing denial

- **GIVEN** owner-forward recovery injects a durable proxy anchor into a successor request
- **AND** this process has already recorded a denial for that anchor
- **WHEN** the recovery retry request state is prepared
- **THEN** the retry MUST retain the denial observation
- **AND** it MUST fail closed before sending the denied anchor upstream

#### Scenario: Sibling response aliases survive retirement

- **GIVEN** a session has a denied response alias and another valid response alias
- **WHEN** the denied anchor is retired
- **THEN** only the denied response alias is removed
- **AND** the valid response alias and turn-state aliases remain routable

#### Scenario: An unconfirmed durable clear still drops the in-memory anchor

- **GIVEN** a denied proxy-injected anchor whose durable clear is fenced or fails
- **WHEN** the denial is handled
- **THEN** the proxy MUST clear the in-memory session anchor
- **AND** MUST NOT report the anchor as retired

If alias unregistering raises after the durable clear, the same in-memory cleanup MUST still occur.

The process-local denial fence MUST retain its positive generation while any
request still pins the denied id, even after durable cleanup succeeds. Such a
prepared request MUST remain fenced until its final pin is released; the fence
may then be removed. Fence state MUST be bounded to one current denial slot per
active durable or local owner plus active request pins, and a session close or
durable-owner epoch change MUST retire the old owner's unpinned slot without
clearing a successor epoch's slot. A close MAY retain an otherwise unpinned
durable denial slot while that session still records an unresolved durable
cleanup, so a stale row cannot be recaptured; the slot MUST be retired when
cleanup succeeds or the durable row is confirmed absent.

When several late predecessor denials arrive for one durable owner, only the
newest unpinned predecessor slot MUST be retained while its durable cleanup is
unresolved. Older predecessor slots MUST remain fenced while request pins are
active, then MUST be retired when those pins release or when a newer owner
confirms durable cleanup. This keeps predecessor churn bounded without
allowing an already-prepared request to redispatch a denied anchor.

#### Scenario: Late predecessor churn remains bounded

- **GIVEN** one durable owner advances through more than the process-local
  denial-ledger bound
- **AND** each successor denial is followed by a late predecessor denial
- **WHEN** no predecessor request retains an active ledger pin
- **THEN** the ledger retains the current denial and at most the newest
  unresolved predecessor denial for that owner
- **AND** a current-owner durable clear retires the unresolved predecessor
  slot

#### Scenario: Pinned predecessor survives bounded churn until release

- **GIVEN** a late predecessor denial still has an active prepared-request pin
- **WHEN** a newer predecessor denial is recorded for the same durable owner
- **THEN** the pinned predecessor remains fenced until its request finalizes
- **AND** releasing the final pin removes that superseded predecessor slot

#### Scenario: A retirement failure cannot change the denial delivered downstream

- **GIVEN** the bookkeeping performed while retiring a denied anchor raises
- **WHEN** the denial is handled
- **THEN** the error MUST NOT propagate into terminal-event handling

#### Scenario: Durable cleanup preserves a prepared request's denial fence

- **GIVEN** a request has pinned a proxy-injected anchor while another request receives `previous_response_not_found`
- **WHEN** the durable clear succeeds
- **THEN** the denied fence keeps its positive generation until the prepared request releases its pin
- **AND** the prepared request remains fenced during that interval
- **AND** the fence is removed after the final pin is released

#### Scenario: A successor epoch survives predecessor fence cleanup

- **GIVEN** a successor owns the same durable session id at a newer epoch
- **WHEN** the predecessor closes or its durable clear completes
- **THEN** cleanup removes only the predecessor's unpinned fence state
- **AND** the successor's current denial slot remains active

#### Scenario: A native client sees the before-dispatch refusal

- **GIVEN** a native Codex HTTP bridge request prepared with a proxy-injected anchor another request had denied
- **WHEN** it reaches its final dispatch check
- **THEN** the proxy fails it closed as `stream_incomplete`
- **AND** the client receives that error rather than a committed empty stream
- **AND** `codex_lb_continuity_fail_closed_total{surface="http_bridge",reason="denied_proxy_anchor_before_dispatch"}` is still incremented

#### Scenario: A forwarded before-dispatch refusal reaches the origin's client

- **GIVEN** a native Codex HTTP bridge turn whose session is owned by another instance in the ring
- **WHEN** the owner instance fails it closed at its before-dispatch check
- **THEN** the owner's error response identifies the failure as a local pre-dispatch refusal
- **AND** the origin instance delivers that `stream_incomplete` error to the client rather than a committed empty stream
