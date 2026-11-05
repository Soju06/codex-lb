## MODIFIED Requirements

### Requirement: Trusted capability requirements are monotonic across lineage

Before dispatch, the proxy MUST persist an authenticated `trusted_cyber`
requirement as API-key-scoped, domain-separated opaque conversation hashes.
Marker writes MUST be monotonic and MUST NOT store raw lineage or account identifiers.

DBB restrictions MUST persist within the requesting conversation and its direct
response continuations. A specific conversation MUST use per-frame
`client_metadata["x-codex-window-id"]` before the compatibility window header,
then `thread-id` when no window exists. Shared parent-task identities MUST NOT
propagate restrictions to separate conversations. When a specific conversation
exists, shared process-session and turn-state markers MUST NOT merge its peers.
Without a specific conversation identity, existing session and turn-state
fallback identities MUST remain supported. Response aliases MUST remain
API-key-scoped and restore the requirement on direct continuations.

A later authenticated request MUST restore REQUIRED before account selection
when any presented alias matches under the same API-key scope. It MUST persist
that requirement onto newly generated aliases. A marker under one API key MUST
NOT establish REQUIRED under another key. Read or write uncertainty MUST fail
before ordinary dispatch.

When `response.created` first reveals a response ID for a durably REQUIRED
request, the proxy MUST persist the upstream and downstream-visible response
aliases before forwarding that created event. If this propagation fails, the
proxy MUST NOT expose the unpersisted response ID, replay the accepted request,
or penalize the upstream account.

#### Scenario: No-echo reconnect remains required
- **WHEN** a capability-bearing direct WebSocket turn persists an accepted
  session identity and a proxy-synthesized turn state
- **AND** a new connection presents the same session identity without the
  capability marker or generated turn state
- **THEN** REQUIRED is restored before its first account selection

#### Scenario: Echoed synthesized turn state remains required
- **WHEN** the reconnect instead echoes the accepted synthesized turn state
- **THEN** REQUIRED is restored before its first account selection

#### Scenario: Response-only reconnect remains required
- **WHEN** a capability-bearing turn exposes a response ID only after upstream
  acceptance
- **AND** a new connection presents only that `previous_response_id` under the
  same API key, without a matching session or turn state
- **THEN** REQUIRED is restored before its first account selection

#### Scenario: Requirement survives a fresh service instance
- **WHEN** a new repository and proxy service instance reads an alias marked by
  an earlier instance
- **THEN** the alias still restores REQUIRED

#### Scenario: API-key scope is isolated
- **WHEN** API key B presents the same visible lineage identifier previously
  marked under API key A
- **THEN** key A's marker does not establish REQUIRED for key B

#### Scenario: Persistence uncertainty cannot downgrade
- **WHEN** required lineage cannot be read or established durably
- **THEN** the request fails before ordinary account selection or dispatch

#### Scenario: Separate ordinary parent and sibling keep normal selection
- **WHEN** a DBB child shares a parent-task and process-session identity with ordinary parent and sibling conversations
- **THEN** its own later turns remain restricted even with a generic model or explicit standard intent
- **AND** the separate ordinary conversations retain normal routing including burn_first

#### Scenario: Frame metadata chooses its conversation
- **WHEN** a frame's window metadata differs from its compatibility window header
- **THEN** only the frame's conversation establishes or restores the requirement

#### Scenario: New conversation ignores legacy broad markers
- **WHEN** a fresh window shares only task or process identifiers with legacy capability markers
- **THEN** the new conversation retains normal selection
