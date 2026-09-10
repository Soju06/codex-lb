## ADDED Requirements

### Requirement: Native HTTP/2 startup profile matches measured Codex

Every persistent native HTTP client pool entry MUST use the measured Codex
initial HTTP/2 stream receive window, connection receive window, maximum frame
size, and maximum header-list size. It MUST NOT enable adaptive startup flow
control when that would replace the explicit profile.

#### Scenario: Native helper starts a new HTTP/2 connection

- **WHEN** a direct or routed native HTTP request creates a fresh connection
- **THEN** its ordered initial SETTINGS and pre-request connection-control shape
  match the maintained direct-Codex profile
- **AND** the route choice does not select a different HTTP/2 profile

### Requirement: Native Codex header replacement preserves wire order

For an inbound native Codex request, codex-lb MUST replace authorization,
accept, content-type, and selected-account values at the position and spelling
of their existing case-insensitive field names. It MUST append a field only
when that field is absent and MUST NOT emit duplicate case variants.

#### Scenario: Native Responses request already contains singleton fields

- **GIVEN** a native Codex request contains ordered authorization, accept,
  content-type, and account-id fields
- **WHEN** codex-lb installs the selected account and upstream values
- **THEN** those field names retain their relative wire order and spelling
- **AND** each case-insensitive singleton occurs exactly once

### Requirement: Model discovery uses the direct Codex header sequence

Subscription model-discovery requests MUST emit authorization, optional account
id, accept, originator, and User-Agent in the maintained direct-Codex order.
The client version MUST remain in the model-discovery query and User-Agent and
MUST NOT be duplicated into a standalone `version` header unless a newer
direct-client profile explicitly requires it.

#### Scenario: Authenticated model discovery is serialized

- **WHEN** codex-lb fetches models for an authenticated ChatGPT account
- **THEN** the decoded header-name order matches the maintained direct profile
- **AND** no standalone `version` header is present
