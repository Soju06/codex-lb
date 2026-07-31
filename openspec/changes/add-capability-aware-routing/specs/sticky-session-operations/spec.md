## ADDED Requirements

### Requirement: Trusted capability requirements are monotonic across lineage

Before dispatch, the proxy MUST persist an authenticated `trusted_cyber`
requirement as API-key-scoped, domain-separated opaque hashes for every known
session, accepted or synthesized turn-state, previous-response, and Codex task
lineage alias. Marker writes MUST be monotonic and MUST NOT store raw lineage
or account identifiers.

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
