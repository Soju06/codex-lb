# responses-api-compat Delta

## ADDED Requirements

### Requirement: Explicit upstream previous-response denials retire proxy-injected anchors

When upstream answers an HTTP bridge request with a `previous_response_not_found` terminal frame, and the `previous_response_id` on that request was injected by the proxy onto a full-resend-shaped payload, the proxy MUST retire that anchor on the first denial rather than waiting for the eventless-failure poison threshold. Retirement MUST clear the durable anchor only when the denied id is still the durable latest response and the session owner fence still matches; the write MUST clear the four anchor-bound fields and delete only the matching response-id alias, preserving turn-state and sibling response aliases. The proxy MUST clear the in-memory session carrier even if durable cleanup or alias unregistering fails.

Before awaiting durable cleanup, the proxy MUST publish the denied id to the live session. Immediately before dispatch, any already-prepared request carrying that id as a proxy-injected anchor MUST fail closed without sending another upstream frame. This revalidation MUST close the retirement/dispatch race; it MUST NOT reject a client-supplied anchor merely because the same id is tombstoned for proxy injection.

The proxy MUST NOT retire the anchor when:

- the anchor was supplied by the client, because removing it changes the meaning of the client's own request;
- the anchor was injected onto a payload that is not full-resend shaped, because a delta-only request has no other way to convey prior context once its anchor is gone;
- the session's current anchor is no longer the denied id, because a concurrent request may have completed and advanced it.

When the durable clear cannot be confirmed, the proxy MUST NOT report the anchor as retired, and MUST still clear the in-memory anchor, which strictly removes one carrier that could re-inject the denied id. A durable record that survives re-injects the id on a later turn, which is denied in turn and re-enters this path, so the clear is re-attempted rather than lost.

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

#### Scenario: A retirement failure cannot change the denial delivered downstream

- **GIVEN** the bookkeeping performed while retiring a denied anchor raises
- **WHEN** the denial is handled
- **THEN** the error MUST NOT propagate into terminal-event handling

### Requirement: Anchored recovery retries retain the provenance of the anchor they replay

When the HTTP bridge dispatches an anchored recovery retry that replays a `previous_response_id` the proxy injected, the retry request state MUST record that the anchor is proxy-injected. A recovery path that dispatches without an anchor MUST leave that provenance false, because there is no anchor for it to describe.

#### Scenario: An anchored recovery retry is attributable to the proxy

- **GIVEN** a request whose `previous_response_id` was injected by the proxy fails and enters anchored recovery
- **WHEN** the recovery retry replays the same anchor
- **THEN** the retry request state records the anchor as proxy-injected
- **AND** continuity diagnostics for the retry report `previous_response_source=proxy_injected` rather than `client_supplied`

#### Scenario: Anchor-free recovery retries claim no provenance

- **GIVEN** a recovery path dispatches without a `previous_response_id`
- **WHEN** the retry request state is prepared
- **THEN** it MUST NOT record a proxy-injected anchor
