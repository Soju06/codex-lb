## ADDED Requirements

### Requirement: Guest dashboard hides the Conversations view

The dashboard view selector MUST render the Conversations option only for an
admin principal. For a guest principal, the effective dashboard view MUST be
Request Logs even when the URL contains `view=conversations`. Guests MUST NOT
mount the Conversations view or issue conversation list/detail API requests.
Admin navigation, filtering, and conversation detail behavior MUST remain
unchanged.

#### Scenario: Guest selector hides Conversations

- **GIVEN** the dashboard principal has role `guest`
- **WHEN** the dashboard view selector opens
- **THEN** it exposes Request Logs and does not expose Conversations

#### Scenario: Guest conversation deep link falls back safely

- **GIVEN** the dashboard principal has role `guest`
- **AND** the URL contains `view=conversations`
- **WHEN** the dashboard renders
- **THEN** the effective view is Request Logs
- **AND** the Conversations view is not mounted
- **AND** no `/api/conversations` request is issued

#### Scenario: Conversation access fails closed during auth hydration

- **GIVEN** auth initialization is incomplete and the auth store still has its
  default admin role
- **AND** the URL contains `view=conversations`
- **WHEN** the dashboard renders before the session resolves
- **THEN** Request Logs is shown and the Conversations view is not mounted
- **AND** no conversation request is enabled
- **AND** the URL retains `view=conversations`
- **WHEN** the session resolves to a guest principal
- **THEN** the conversation surface remains closed and the URL is normalized to
  Request Logs

#### Scenario: Admin retains Conversations navigation

- **GIVEN** the dashboard principal has role `admin`
- **WHEN** the dashboard view selector opens
- **THEN** it exposes both Request Logs and Conversations
