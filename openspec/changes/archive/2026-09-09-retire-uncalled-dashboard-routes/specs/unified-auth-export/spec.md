## ADDED Requirements

### Requirement: Deprecated export routes are retired

The system SHALL NOT serve the predecessor routes `POST /api/accounts/{id}/export` and `POST /api/accounts/{id}/export/opencode-auth`. `POST /api/accounts/{id}/export/auth` is the only account credential export endpoint; both the Codex and the OpenCode `auth.json` payloads are obtained from its single response.

#### Scenario: Legacy Codex export route is gone

- **WHEN** a client calls `POST /api/accounts/acct-123/export`
- **THEN** the request is rejected as an unmatched route (`404`, or `405` when a catch-all partially matches the path)
- **AND** no credential material is returned and no `account_exported` audit event is written

#### Scenario: Legacy OpenCode export route is gone

- **WHEN** a client calls `POST /api/accounts/acct-123/export/opencode-auth`
- **THEN** the request is rejected as an unmatched route (`404`, or `405` when a catch-all partially matches the path)
- **AND** no credential material is returned
