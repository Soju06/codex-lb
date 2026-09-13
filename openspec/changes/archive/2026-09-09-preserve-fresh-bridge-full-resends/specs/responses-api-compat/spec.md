## ADDED Requirements

### Requirement: Fresh durable HTTP bridge preserves client-unanchored full resends

The service MUST preserve a client-unanchored full resend as the first request
on a fresh durable HTTP bridge. This applies when the request resolves a hard
durable conversation, has no client-supplied `previous_response_id`, has a
stored prefix matching that durable conversation, and has neither a reusable
local bridge nor a forwardable remote owner. The input projected through the
existing fresh-replay bookkeeping filter MUST also either retain completed
assistant output before fresh user input or have a suffix consisting only of
complete, self-contained direct tool call/output pairs that exactly match a
durable manifest of every call ID and call type emitted by the prior response.
The manifest MUST include every observed tool-call `output_item.added` event,
MUST require a matching `output_item.done` event with the same call ID and type,
MUST reconcile any tool calls present in terminal response output, and MUST be
persisted atomically with that response's durable alias. An incomplete,
conflicting, or malformed lifecycle MUST persist an unknown manifest rather
than a partial one. Duplicate call IDs in added, done, or terminal output MUST
invalidate the manifest even when their call types match. The serialized
manifest MUST bind its call map to the exact durable response ID, and a response
ID mismatch MUST be treated as an unknown manifest so rolling-upgrade writers
that do not know the manifest column cannot leave stale calls on a newer
response.
If a response contains a client-settled call type that the direct tool-loop
proof cannot represent, including `computer_call` or `mcp_approval_request`,
the service MUST treat the entire manifest as unknown rather than persist a
partial manifest for any parallel supported calls.
The service MUST submit that safe original full resend without adding
`previous_response_id`, MUST retain the durable preferred owner and hard
affinity, MUST NOT move the request through account-neutral replay, and MUST NOT
trim the stored prefix before that first send.

If the matching cumulative input omits prior output, contains an incomplete or
orphaned tool call/output sequence, omits any call in the durable manifest,
reuses a stored-prefix call ID, has no known durable manifest, or otherwise
lacks either safe context shape, the service MUST retain the existing
durable-anchor injection and prefix trimming behavior.

The service MUST NOT seed the newly created local session with the old durable
response in a way that re-injects the anchor before the original full resend is
submitted. Once the fresh request completes, ordinary live-session continuity
and trimming MAY resume from the newly completed response. Incremental requests
that rely on durable history, client-supplied anchors, owner-unavailable
handling, and existing account-neutral replay eligibility remain unchanged.

#### Scenario: Full resend opens a fresh bridge without a durable anchor

- **GIVEN** a client-unanchored full resend has a verified stored prefix and retained completed assistant output for a hard durable conversation
- **AND** no reusable local bridge or forwardable remote owner exists
- **WHEN** the service creates a fresh upstream WebSocket on the durable owner
- **THEN** its first `response.create` omits `previous_response_id`
- **AND** its input contains the original full resend
- **AND** its hard session affinity is retained

#### Scenario: Tool-loop resend does not require an assistant-message replay boundary

- **GIVEN** a verified client-unanchored full resend continues a tool loop with complete self-contained direct call/output pairs but no completed assistant-message boundary
- **AND** those calls and outputs exactly settle the durable prior-response call manifest
- **WHEN** it starts on a fresh durable bridge
- **THEN** the service submits the original request once on the durable owner
- **AND** retained-output checks used for cross-account replay do not block or rewrite that first send

#### Scenario: Omitted parallel tool call remains anchored

- **GIVEN** the durable prior-response manifest contains two parallel call IDs
- **WHEN** a matching cumulative input carries a complete call/output pair for only one ID
- **THEN** the service retains the durable `previous_response_id`
- **AND** it does not classify the suffix as a complete tool-loop resend

#### Scenario: Incomplete tool-call lifecycle keeps manifest unknown

- **GIVEN** a response emits added events for two parallel tool calls
- **AND** only one call reaches a matching done event before `response.completed`
- **WHEN** the durable response alias is persisted
- **THEN** its tool-call manifest is unknown rather than a one-call partial manifest
- **AND** a later direct tool-loop full resend remains anchored

#### Scenario: Unsupported parallel client-settled call keeps manifest unknown

- **GIVEN** a response emits a supported direct call and a parallel client-settled call that the replay proof cannot represent
- **WHEN** the durable response alias is persisted
- **THEN** its tool-call manifest is unknown rather than a partial supported-call manifest
- **AND** a later resend that settles only the supported call remains anchored

#### Scenario: Legacy durable row remains anchored

- **GIVEN** a durable response row predates the call-manifest migration or otherwise has an unknown manifest
- **WHEN** a matching cumulative input contains direct tool call/output items without a completed assistant-message boundary
- **THEN** the service retains the durable `previous_response_id`

#### Scenario: Older writer advances response without manifest

- **GIVEN** a durable row has a response-bound tool-call manifest
- **WHEN** an older rolling-upgrade writer advances `latest_response_id` without updating the manifest column
- **THEN** readers treat the mismatched manifest as unknown
- **AND** a later direct tool-loop full resend remains anchored

#### Scenario: Cumulative prompt without prior output remains anchored

- **GIVEN** a matching cumulative input contains fresh user input but omits the prior assistant output
- **WHEN** no reusable bridge exists for its hard durable conversation
- **THEN** the service retains the durable `previous_response_id`
- **AND** it trims the verified stored prefix through the existing anchored path
- **AND** it does not classify the original unanchored cumulative input as a safe fresh-upstream retry

#### Scenario: Failed owner forwarding preserves omitted response context

- **GIVEN** a matching cumulative input omits prior assistant output and initially resolves to a forwardable durable owner
- **WHEN** owner forwarding fails before any downstream output and the service performs local takeover
- **THEN** the local recovery request retains the durable `previous_response_id`
- **AND** it trims the verified stored prefix instead of submitting the cumulative input unanchored
- **AND** the injected anchor is not eligible for an unanchored fresh-upstream retry

#### Scenario: Refreshed takeover context no longer matches the resend

- **GIVEN** owner forwarding fails and the refreshed durable takeover row has different stored-input metadata
- **WHEN** the cumulative input cannot prefix-match that refreshed row
- **THEN** the service fails closed instead of pairing the refreshed response ID with stale prefix metadata

#### Scenario: Refreshed takeover account replaces stale routing

- **GIVEN** owner forwarding fails and the refreshed durable takeover row names a different account
- **WHEN** the service performs local takeover
- **THEN** the recovery session requires the refreshed account rather than the initial stale account
- **AND** a refreshed account that conflicts with another required owner fails closed

#### Scenario: Live bridge trimming remains unchanged

- **GIVEN** the durable conversation still has a reusable live bridge
- **WHEN** a trimmable full resend continues that live session
- **THEN** the existing session-level anchor and prefix-trimming behavior remains available
