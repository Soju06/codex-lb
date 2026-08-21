# responses-api-compat Delta

## ADDED Requirements

### Requirement: Explicit upstream previous-response denials retire proxy-injected anchors

When upstream answers an HTTP bridge request with a `previous_response_not_found` terminal frame, and the `previous_response_id` on that request was injected by the proxy rather than supplied by the client, the proxy MUST retire that anchor on the first denial rather than waiting for the eventless-failure poison threshold. Retirement MUST clear the durable continuity record under the session's owner epoch and the in-memory session anchor together, so no later turn can re-inject the denied id from either carrier. The proxy MUST NOT retire the anchor when the session's current anchor is no longer the denied id, because a concurrent request may have completed and advanced it. A client-supplied `previous_response_id` MUST NOT be retired by this path.

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
