## ADDED Requirements

### Requirement: File-pinned compact refresh/connect failures fail closed

The proxy SHALL preserve file-owner routing during pre-visible refresh and
upstream-connect failure handling. If the pinned account cannot refresh or open
the upstream compact connection before any compact response is emitted, the proxy
MUST surface a stable upstream-unavailable failure for that request instead of
excluding the pinned account and replaying the compact request on another
account. This fail-closed rule applies only to file-pinned compact requests;
replayable compact/connect requests without a live file-id pin continue to use
the existing pre-visible forced-refresh and eligible-account failover behavior.

#### Scenario: file-pinned compact request fails closed on refresh transport failure

- **GIVEN** `file_pinned` was uploaded through `account_a` and its in-memory pin is live
- **AND** a compact request references `{"type": "input_file", "file_id": "file_pinned"}`
- **WHEN** `account_a` fails token refresh with a pre-visible transport or connection error
- **THEN** the proxy returns an upstream-unavailable error for that compact request
- **AND** it does not select another account for that request

#### Scenario: replayable compact request without file pins can still fail over

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the compact request has no live `input_file.file_id` routing pin
- **WHEN** the selected account fails before compact output is emitted and the
  failure is classified by an existing pre-visible failover rule
- **THEN** the proxy may exclude that account for the current request and try
  another eligible account
