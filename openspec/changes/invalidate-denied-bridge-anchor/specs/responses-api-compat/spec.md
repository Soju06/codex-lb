# responses-api-compat Delta

## ADDED Requirements

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

### Requirement: Anchored recovery retries retain the provenance of the anchor they replay

When the HTTP bridge dispatches an anchored recovery retry that replays a `previous_response_id` the proxy injected, the retry request state MUST record that the anchor is proxy-injected. A recovery path that dispatches without an anchor MUST leave that provenance false, because there is no anchor for it to describe.
When a hard turn-state operation-ledger lookup injects an anchor after the request state was initially prepared without a `previous_response_id`, the request state MUST preserve the original payload's full-resend classification and use it when deciding whether a later denial may retire that injected anchor.

#### Scenario: An anchored recovery retry is attributable to the proxy

- **GIVEN** a request whose `previous_response_id` was injected by the proxy fails and enters anchored recovery
- **WHEN** the recovery retry replays the same anchor
- **THEN** the retry request state records the anchor as proxy-injected
- **AND** continuity diagnostics for the retry report `previous_response_source=proxy_injected` rather than `client_supplied`

#### Scenario: Anchor-free recovery retries claim no provenance

- **GIVEN** a recovery path dispatches without a `previous_response_id`
- **WHEN** the retry request state is prepared
- **THEN** it MUST NOT record a proxy-injected anchor

### Requirement: Denied proxy-injected bridge anchors have fenced lifecycle cleanup

When upstream rejects a proxy-injected `previous_response_id` with
`previous_response_not_found`, the HTTP bridge MUST publish a positive
process-local denial generation before any cleanup await. A prepared request
that captured that anchor before the denial MUST fail closed without another
upstream dispatch, including when the request was admitted before its session
was closed. The generation MUST remain available while any request pins it.

An owner transition MUST NOT allow a stale predecessor to replace a newer
durable owner's denial fence. An ownerless or process-local predecessor MUST
NOT overwrite a durable owner entry for the same response id. When local alias
cleanup fails without a durable owner, the bridge MUST retain a tracked retry
that can remove the alias and fence rather than abandoning an unbounded local
tombstone.

An unpinned stale-predecessor denial fence MUST remain available until a
current owner confirms that the matching durable anchor has been cleared;
releasing the stale request's final pin alone MUST NOT drop that fence.

When a sibling has already advanced the current response, its denial fence is
historical rather than unresolved cleanup. Closing the session MUST retire an
unpinned historical fence once durable ownership is released (including an
ownerless release result), while preserving unresolved current-anchor cleanup
and any pinned generations for their final request release.

#### Scenario: An admitted denial still cleans up after session close

- **GIVEN** a request was admitted with a proxy-injected anchor
- **AND** the bridge session is marked closed before upstream returns
  `previous_response_not_found`
- **WHEN** the terminal denial is handled
- **THEN** the denial generation is published and the request receives the
  existing downstream error contract
- **AND** the denied anchor is not re-injected by a later request

#### Scenario: A stale local predecessor cannot replace a durable fence

- **GIVEN** a durable owner holds a positive denial fence for response id `A`
- **WHEN** a late process-local predecessor records denial for the same `A`
- **THEN** the durable owner and generation remain authoritative
- **AND** the predecessor does not remove the durable owner mapping

#### Scenario: Local alias cleanup failure remains tracked

- **GIVEN** a denied anchor belongs only to a process-local session
- **WHEN** local alias unregistering fails transiently
- **THEN** the bridge tracks a bounded cleanup retry
- **AND** a successful retry removes the local alias and denial fence

#### Scenario: Sibling-advanced fence retires on ownerless close

- **GIVEN** a denial arrives after a sibling has advanced the session's current
  response
- **AND** no request still pins the denied generation
- **WHEN** closing the session releases durable ownership with no owner
- **THEN** the historical denial fence and owner mapping are removed
- **AND** unresolved current-anchor cleanup fences remain retained
