## ADDED Requirements

### Requirement: Dashboard client starts with least-privilege auth state

The dashboard client auth store MUST initialize with `role` set to `guest`, an empty `permissions` list, and `canWrite` false, and MUST reset to those same values when the user logs out, before the session is refreshed. The auth gate MUST render only a loading indicator, and MUST NOT mount the application content, until the first session response has been applied (`initialized` true), regardless of whether a request is in flight. Session parsing MUST default an omitted `role` to `guest` and an omitted `permissions` list to empty, MUST accept any string value inside `permissions` so that fine-grained permission names do not invalidate the session, and MUST derive `canWrite` solely from the presence of the `write` permission.

#### Scenario: Application content is hidden until the session resolves

- **GIVEN** the dashboard has just loaded and no session response has been applied
- **WHEN** the auth gate renders for the first time
- **THEN** only the loading indicator is shown
- **AND** neither the application content nor the login form is mounted
- **AND** the session refresh request is issued

#### Scenario: Store boots without write access

- **WHEN** the auth store is created
- **THEN** `role` is `guest`, `permissions` is empty, and `canWrite` is false
- **AND** `initialized` is false

#### Scenario: Logout does not restore admin defaults

- **GIVEN** an authenticated admin session in the store
- **WHEN** the user logs out
- **THEN** the store holds `role` `guest`, empty `permissions`, and `canWrite` false while the follow-up session refresh is in flight
- **AND** the refreshed session response determines the final state

#### Scenario: Session response omits role and permissions

- **WHEN** a session response contains neither `role` nor `permissions`
- **THEN** the parsed session has `role` `guest` and empty `permissions`
- **AND** `canWrite` is false

#### Scenario: Fine-grained permission strings are accepted

- **WHEN** a session response lists `permissions` `["read", "write", "accounts:export"]`
- **THEN** parsing succeeds and preserves every value
- **AND** `canWrite` is true because `write` is present

## MODIFIED Requirements

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
  least-privilege default (`guest` role, no permissions)
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
