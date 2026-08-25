# responses-api-compat Delta

## ADDED Requirements

### Requirement: Explicit upstream previous-response denials retire proxy-injected anchors

When upstream answers an HTTP bridge request with a `previous_response_not_found` terminal frame, and the `previous_response_id` on that request was injected by the proxy onto a full-resend-shaped payload, the proxy MUST retire that anchor on the first denial rather than waiting for the eventless-failure poison threshold. The denial fact MUST be written durably for the logical session regardless of owner epoch so every replica and successor can observe it. The same transaction MUST clear the four anchor-bound fields only when the denied id is still the durable latest response and MUST delete only the matching response-id alias, preserving a newer anchor, turn-state, and sibling response aliases. The proxy MUST clear the matching in-memory session carrier even if durable cleanup or alias unregistering fails.

Before awaiting durable cleanup, the proxy MUST publish the denied id to the denied session generation and every live same-key successor. Each process-local publication MUST be serialized with that generation's final tombstone check and upstream send so either an already-started send finishes first or publication wins and fences that send. A successor registered after publication MUST inherit tombstones from detached same-key generations before becoming canonical. Successor publication MUST unregister the matching local alias and clear matching in-memory trim state. A replica that loads a durable anchor MUST check the durable denial before request-level anchor injection and MUST remove the anchor plus its count, fingerprint, and pending-tool metadata from that lookup before preparing the payload. Before publishing any reversible recovery alias or dispatching upstream, an already-prepared request carrying that id as a proxy-injected anchor MUST check both local and durable tombstones and fail closed without sending another upstream frame. Its final durable check MUST acquire the same durable session-row fence used by denial publication and MUST hold that fence through the WebSocket send, so a remote denial cannot commit between a clean check and dispatch. The fenced send MUST have a finite transport timeout; timeout or cancellation MUST release the database transaction and retire the ambiguous socket without penalizing account health. If fence entry fails after a reversible recovery alias was published, the proxy MUST roll that alias back before returning the 502; if rollback cannot be confirmed, it MUST close and retire the session. A local response-alias unregister failure after the final check observes a tombstone MUST be logged and contained so it cannot replace the intended `stream_incomplete` response or enter account-penalizing send-failure handling. These checks MUST NOT reject a client-supplied anchor merely because the same id is tombstoned for proxy injection.

The proxy MUST NOT retire the anchor when:

- the anchor was supplied by the client, because removing it changes the meaning of the client's own request;
- the anchor was injected onto a payload that is not full-resend shaped, because a delta-only request has no other way to convey prior context once its anchor is gone.

When the session's current anchor is no longer the denied id because a concurrent request advanced it, the proxy MUST NOT clear the newer anchor. It MUST still persist the denial fact and remove the denied response alias.

When the durable denial write and conditional clear cannot be confirmed or their await is cancelled, the proxy MUST NOT report the anchor as retired, and MUST still unregister the local response alias and clear the in-memory anchor, which strictly removes carriers that could re-inject or trim against the denied id. If the surviving durable record is read again on a later full-resend turn in the same live session, the proxy MUST retry durable retirement and MUST suppress that tombstoned id from session hydration, anchor injection, and prefix trimming regardless of the retry outcome.

Retirement is bookkeeping and MUST NOT change how the denial is delivered downstream. A failure while retiring MUST NOT propagate into terminal-event handling.

A denial that settles several requests sharing one anchor MUST retire that anchor once, on the same terms. For an unscoped grouped denial, all request states carrying a non-null `previous_response_id` MUST agree on one id before retirement eligibility is evaluated; if any distinct anchor is present, including a client-supplied or delta-only anchor, the proxy MUST skip retirement because the denied anchor is ambiguous.

The downstream error contract is unchanged: the denial is still reported to the client as `stream_incomplete`, so the client retains its own anchor and is not driven into a full-history resend.

#### Scenario: A denied proxy-injected anchor is retired immediately

- **GIVEN** an HTTP bridge session whose stored anchor was injected by the proxy
- **WHEN** upstream answers the anchored request with `previous_response_not_found`
- **THEN** the proxy durably records the denial independent of the session's owner epoch
- **AND** clears the matching durable continuity fields only if that id remains current
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
- **AND** it MUST still durably tombstone the denied id and remove its alias so another owner cannot dispatch it

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

#### Scenario: An ambiguous fan-out denial retires no anchor

- **GIVEN** an unscoped `previous_response_not_found` settles grouped requests carrying distinct anchors
- **AND** at least one anchor is client-supplied, delta-only, or otherwise ineligible for retirement
- **WHEN** no denied response id identifies one anchor
- **THEN** the proxy MUST NOT tombstone or clear either anchor

#### Scenario: A prepared denied anchor is rejected before dispatch

- **GIVEN** a request was prepared with a proxy-injected anchor
- **AND** another request receives `previous_response_not_found` for that anchor before the prepared request reaches the upstream send
- **WHEN** the prepared request reaches its final dispatch check
- **THEN** the proxy fails it closed as `stream_incomplete`
- **AND** the proxy MUST NOT publish a reversible recovery alias for that undispatched request
- **AND** the proxy MUST NOT send that denied anchor upstream again

#### Scenario: Denial publication wins against a prepared dispatch

- **GIVEN** a request is prepared with a proxy-injected anchor while another request receives `previous_response_not_found` for that anchor
- **WHEN** denial publication acquires session lifecycle ownership before the prepared request's final send section
- **THEN** the denied id is tombstoned before the prepared request revalidates
- **AND** the prepared request fails closed without sending an upstream frame

#### Scenario: A detached denial reaches the live successor

- **GIVEN** a denied request belongs to a detached generation
- **AND** a same-key successor advanced the durable owner epoch and became live
- **WHEN** the detached generation handles the denial
- **THEN** the proxy MUST tombstone the denied id on both generations
- **AND** MUST clear matching alias and trim state from the successor
- **AND** a later successor MUST inherit the tombstone before canonical registration

#### Scenario: A durable denial reaches another replica before payload preparation

- **GIVEN** one replica durably records a denied proxy-injected anchor
- **AND** another replica resolves the same logical session with that anchor
- **WHEN** the second replica prepares a request-level continuity payload
- **THEN** it MUST remove the denied anchor and all anchor-bound trim metadata before injection
- **AND** the first request on that replica remains eligible for unanchored full-history dispatch

#### Scenario: A remotely denied prepared request is rejected before dispatch

- **GIVEN** a replica prepared a request with a proxy-injected anchor
- **AND** another replica durably tombstoned that anchor before the first replica dispatches
- **WHEN** the prepared request reaches its final dispatch check
- **THEN** it MUST fail closed before recovery alias publication and upstream send

#### Scenario: Cross-replica denial publication is ordered with dispatch

- **GIVEN** one replica has acquired a clean final dispatch fence for a proxy-injected anchor
- **AND** another replica begins durably publishing a denial for that same anchor before the first replica sends
- **WHEN** the first replica hands the frame to the WebSocket transport
- **THEN** the denial publication MUST remain blocked until that send releases the fence
- **AND** if denial publication acquires the fence first, the prepared request MUST observe the tombstone and MUST NOT send

#### Scenario: A wedged fenced send releases durable coordination

- **GIVEN** a prepared proxy-injected anchor passed the final durable denial check
- **AND** its WebSocket transport send stops making progress while the durable dispatch fence is held
- **WHEN** the bounded transport-send timeout expires
- **THEN** the proxy MUST release the durable transaction and session-row fence
- **AND** MUST retire the ambiguous socket without penalizing the selected account

#### Scenario: Dispatch-fence entry failure rolls back recovery routing

- **GIVEN** a reversible recovery turn-state alias was published for a prepared anchored request
- **WHEN** the final durable dispatch fence cannot be entered before any upstream send
- **THEN** the proxy MUST roll back the recovery alias before returning a 502
- **AND** if rollback cannot be confirmed, the proxy MUST close and retire the session

#### Scenario: Late-denial local cleanup failure stays bookkeeping-only

- **GIVEN** the final durable check observes that the proxy-injected anchor was denied
- **WHEN** unregistering the matching process-local response alias raises
- **THEN** the proxy MUST still clear matching in-memory trim state
- **AND** MUST return the intended `stream_incomplete` response without penalizing account health

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

#### Scenario: A surviving durable tombstone is retried without redispatch

- **GIVEN** the first conditional durable clear of a denied proxy-injected anchor failed
- **AND** the durable lookup returns that tombstoned id on the next full-resend turn in the same live session
- **WHEN** the proxy prepares the next turn
- **THEN** it MUST retry the conditional durable clear under the owner fence
- **AND** MUST NOT hydrate, inject, or trim against the tombstoned id even if that retry fails
- **AND** the full-resend turn MUST remain eligible for unanchored upstream dispatch

#### Scenario: Durable cleanup cancellation still clears memory

- **GIVEN** a denied proxy-injected anchor has been tombstoned
- **WHEN** cancellation interrupts its conditional durable clear
- **THEN** the proxy MUST unregister the local response alias
- **AND** MUST clear the matching in-memory anchor and trim metadata before cancellation propagates

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
