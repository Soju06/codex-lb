## ADDED Requirements

### Requirement: Fresh durable HTTP bridge preserves client-unanchored full resends

The service MUST preserve a client-unanchored full resend as the first request
on a fresh durable HTTP bridge. This applies when the request resolves a hard
durable conversation, has no client-supplied `previous_response_id`, has a
stored prefix matching that durable conversation, and has neither a reusable
local bridge nor a forwardable remote owner. The service MUST submit the
original full resend without adding `previous_response_id`, MUST retain the
durable preferred owner and hard affinity, MUST NOT move the request through
account-neutral replay, and MUST NOT trim the stored prefix before that first
send.

The service MUST NOT seed the newly created local session with the old durable
response in a way that re-injects the anchor before the original full resend is
submitted. Once the fresh request completes, ordinary live-session continuity
and trimming MAY resume from the newly completed response. Incremental requests
that rely on durable history, client-supplied anchors, owner-unavailable
handling, and existing account-neutral replay eligibility remain unchanged.

#### Scenario: Full resend opens a fresh bridge without a durable anchor

- **GIVEN** a client-unanchored full resend has a verified stored prefix for a hard durable conversation
- **AND** no reusable local bridge or forwardable remote owner exists
- **WHEN** the service creates a fresh upstream WebSocket on the durable owner
- **THEN** its first `response.create` omits `previous_response_id`
- **AND** its input contains the original full resend
- **AND** its hard session affinity is retained

#### Scenario: Tool-loop resend does not require an assistant-message replay boundary

- **GIVEN** a verified client-unanchored full resend continues a tool loop without a completed assistant-message boundary
- **WHEN** it starts on a fresh durable bridge
- **THEN** the service submits the original request once on the durable owner
- **AND** retained-output checks used for cross-account replay do not block or rewrite that first send

#### Scenario: Live bridge trimming remains unchanged

- **GIVEN** the durable conversation still has a reusable live bridge
- **WHEN** a trimmable full resend continues that live session
- **THEN** the existing session-level anchor and prefix-trimming behavior remains available
