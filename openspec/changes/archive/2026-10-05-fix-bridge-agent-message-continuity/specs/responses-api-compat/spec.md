## ADDED Requirements

### Requirement: Durable same-owner context proof accepts agent-message follow-ups

For a prefix-verified durable HTTP bridge full resend, the service SHALL accept
well-formed `agent_message` items as fresh same-owner input after retained
completed assistant output, or after a complete direct tool-call/output suffix
that exactly settles the durable prior-response manifest. Agent messages MUST
NOT substitute for missing prior output, missing tool calls, or missing tool
outputs. Unknown or malformed agent-message shapes MUST NOT authorize replay.

The fresh bridge and bounded explicit stale-anchor recovery paths SHALL retain
the original input items and their order, including reasoning and encrypted
agent-message content, while omitting the stale `previous_response_id`.
The request MUST remain pinned to the proven durable account and MUST fail
closed if that account is unavailable. This proof MUST NOT make an agent-message
payload eligible for account-neutral replay or relax existing operation fences,
replay limits, or the ban on replay after visible response output.

#### Scenario: Subagent message follows a settled tool loop on a replacement socket
- **GIVEN** the full input matches the durable stored prefix and settles every call in its manifest
- **AND** a new agent message follows those tool outputs
- **WHEN** the client resumes on a fresh HTTP bridge
- **THEN** the original full input is sent to the durable owner without a previous-response anchor
- **AND** encrypted content and item identifiers are preserved

#### Scenario: Same owner explicitly rejects the anchor
- **GIVEN** a prefix-verified full resend containing a valid agent follow-up
- **WHEN** upstream rejects its anchor before any response output
- **THEN** the existing fenced one-shot replacement sends the original full input without the rejected anchor to the same owner

#### Scenario: Agent message cannot cover incomplete history
- **GIVEN** a resend omits a manifest call or its output, lacks a known manifest and retained assistant output, or contains a malformed agent message
- **WHEN** the bridge verifies same-owner context
- **THEN** the agent message does not authorize unanchored replay

#### Scenario: Unavailable owner cannot move encrypted agent context
- **GIVEN** a full resend passes the same-owner proof and contains an agent message
- **WHEN** its durable owner is unavailable
- **THEN** no alternate account receives the request
