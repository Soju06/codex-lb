## ADDED Requirements

### Requirement: Helm Gateway API routes support rule-level matches and filters

The Helm chart MUST allow operators to configure an ordered list of HTTPRoute
rules containing Gateway API `matches` and `filters`. The chart MUST attach the
codex-lb Service backend to every configured rule. The feature MUST be optional
and preserve the existing backend-only catch-all rule when no rules are set.

#### Scenario: Paths use different Gateway filters

- **GIVEN** `gatewayApi.enabled=true`
- **AND** `gatewayApi.rules` contains an unfiltered API rule matching `/v1`,
  `/backend-api/codex`, `/backend-api/wham`, `/backend-api/transcribe`,
  `/backend-api/files`, and `/api/codex`, followed by a filtered `/` catch-all
  rule
- **WHEN** the chart renders its HTTPRoute
- **THEN** both rules retain their configured matches in order
- **AND** only the catch-all rule contains the configured filter
- **AND** both rules target the chart-managed codex-lb Service and port
- **AND** WHAM identity discovery, file-upload, and Codex usage/reset-credit
  paths retain their own caller-authentication contracts instead of traversing
  the dashboard filter

#### Scenario: Empty rule configuration preserves the default route

- **GIVEN** `gatewayApi.enabled=true`
- **AND** `gatewayApi.rules` is empty
- **WHEN** the chart renders its HTTPRoute
- **THEN** it contains one backend-only rule targeting the chart-managed
  codex-lb Service and port
