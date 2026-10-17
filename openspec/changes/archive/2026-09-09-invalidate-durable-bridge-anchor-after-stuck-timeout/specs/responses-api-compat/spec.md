# responses-api-compat Delta

## ADDED Requirements

### Requirement: A stuck eventless HTTP bridge reattach invalidates its durable anchor for full-resend clients

When a proxy-injected durable `previous_response_id` anchor causes an HTTP bridge `response.create` to reach the eventless client-safe timeout (`missing_response_created_timeout`) without producing `response.created` or any other response event, and the client's own incoming payload for that request already looked like a full conversation resend, the proxy MUST clear that durable session's stored anchor — `latest_response_id`, `latest_input_item_count`, `latest_input_full_fingerprint`, and `latest_pending_tool_calls_json` — before releasing durable ownership, fenced to the session's current owner epoch. A client-supplied `previous_response_id` MUST NOT be cleared by this path. A proxy-injected anchor on a payload that did not look like a full resend (a genuine delta-only continuation) MUST NOT be cleared by this path, because the client has no other way to convey prior conversation state once the anchor is gone. The durable session's turn-state and identity MUST remain intact so a later request can still reattach without the stale anchor.

#### Scenario: Full-resend proxy-injected anchor times out and is cleared

- **GIVEN** a durable HTTP bridge session has a stored `latest_response_id`
- **AND** a fresh reattach injects that response id as `previous_response_id` because the client sent none
- **AND** the client's incoming payload already looked like a full conversation resend
- **WHEN** the resulting `response.create` reaches the eventless client-safe deadline with no `response.created` or other response event
- **THEN** the terminal `missing_response_created_timeout` failure is delivered as before
- **AND** the durable session's `latest_response_id`, input fingerprint, and pending tool-call manifest are cleared under the current owner epoch
- **AND** the durable session's turn-state alias remains available for reattachment

#### Scenario: Next reattach takes the fresh no-anchor path

- **GIVEN** a durable session's anchor was cleared after a stuck eventless timeout on a full-resend payload
- **WHEN** a later request reattaches to the same durable session with no `previous_response_id`
- **THEN** the proxy does not inject a `previous_response_id` anchor for that request
- **AND** the request proceeds on the existing unanchored full-resend/fresh path instead of repeating the cleared anchor

#### Scenario: Delta-only proxy-injected anchor is left intact

- **GIVEN** a fresh reattach injects a durable `previous_response_id` anchor because the client sent none
- **AND** the client's incoming payload did not look like a full conversation resend
- **WHEN** the resulting `response.create` reaches the eventless client-safe deadline with no `response.created` or other response event
- **THEN** the terminal `missing_response_created_timeout` failure is delivered as before
- **AND** the durable session's `latest_response_id` is not cleared
- **AND** the next reattach on that session still injects the same anchor, preserving the client's only way to convey prior context

#### Scenario: Client-supplied anchor is left untouched

- **GIVEN** a request supplied its own `previous_response_id` rather than receiving a proxy-injected one
- **WHEN** its `response.create` reaches the eventless client-safe timeout
- **THEN** the durable session's `latest_response_id` is not cleared
- **AND** later requests may still resolve that alias per existing continuity rules

#### Scenario: Fenced anchor-clear loses to a newer owner

- **GIVEN** a durable session's owner epoch has advanced past the retiring session's epoch before the anchor-clear write executes
- **WHEN** the stuck-timeout handling attempts to clear the anchor
- **THEN** the write is a no-op
- **AND** the newer owner's durable state is left untouched
