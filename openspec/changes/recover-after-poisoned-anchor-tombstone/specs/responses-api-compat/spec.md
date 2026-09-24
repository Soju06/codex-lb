## ADDED Requirements

### Requirement: Submit-time abandonment tombstones release rejected proxy anchors

When the submit-time gate rejects a proxy-injected `previous_response_id` because an abandonment tombstone proves that anchor dead, the bridge MUST retire that exact anchor from its current in-memory and durable continuity carriers before returning the existing `404 bridge_previous_response_not_found` response. Retirement MUST be conditional on the rejected anchor still being current and MUST NOT clear a successor anchor or a client-supplied anchor.

#### Scenario: First tombstone refusal retires the rejected proxy anchor

- **WHEN** a request reaches submission with a proxy-injected anchor and the bridge adopts an `anchor_abandoned` tombstone for its session key
- **THEN** the bridge returns `404 bridge_previous_response_not_found`, conditionally clears that anchor from durable continuity, and removes the same anchor from the live session carrier

#### Scenario: Full resend escapes the rejected anchor

- **WHEN** the client follows that refusal with a full-conversation resend on the same bridge key and does not supply a `previous_response_id`
- **THEN** the bridge preserves the complete resend input, does not inject the retired anchor, and permits the request to proceed through normal dispatch admission while the tombstone remains until replacement continuity is registered

#### Scenario: Delta-only follow-up remains fail-closed

- **WHEN** the client follows that refusal with a delta-only request on the same bridge key and supplies no `previous_response_id`
- **THEN** the bridge retains the abandonment tombstone and returns `404 bridge_previous_response_not_found` rather than dispatching the request without its missing context

#### Scenario: Concurrent successor continuity is preserved

- **WHEN** another request registers a successor anchor before retirement of the rejected anchor completes
- **THEN** the retirement operation leaves the successor anchor and its continuity metadata unchanged

#### Scenario: Client-supplied anchors are not retired by the submit-time gate

- **WHEN** a client-supplied `previous_response_id` is present while an abandonment tombstone exists for the bridge key
- **THEN** the submit-time proxy-anchor retirement behavior does not clear or rewrite that client-supplied anchor
