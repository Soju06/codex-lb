## ADDED Requirements

### Requirement: Codex installation metadata is account-owned

For Codex response-create upstream requests, the service MUST attach a
server-owned per-account Codex installation id to upstream client metadata when
an account is selected. Inbound client-supplied Codex installation id headers or
metadata MUST NOT be trusted as the account installation id. Existing unrelated
client metadata such as turn metadata MUST be preserved.

#### Scenario: Inbound installation id is replaced

- **GIVEN** an account has a stored Codex installation id
- **AND** a client sends response-create metadata with a different
  `x-codex-installation-id`
- **WHEN** the request is forwarded upstream
- **THEN** the upstream metadata contains the account's stored installation id
- **AND** preserves unrelated metadata entries

#### Scenario: Inbound installation id header is stripped

- **WHEN** a client sends `X-Codex-Installation-Id`
- **THEN** the upstream request does not forward that header as a trusted
  client-supplied identity

### Requirement: Compact payloads omit unsupported client metadata

Compact request payload normalization MUST remove `client_metadata` before
forwarding compact requests upstream.

#### Scenario: Compact strips client metadata

- **WHEN** a compact payload includes `client_metadata`
- **THEN** the upstream compact payload omits it
