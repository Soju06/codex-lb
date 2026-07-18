## ADDED Requirements

### Requirement: Phase-4 removed prewarm canary settings are retired

The Codex HTTP-bridge prewarm rollout scoping SHALL NOT be
operator-configurable: prewarm eligibility MUST be the
`CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED` flag alone,
with no canary sampling percent and no API-key allow/deny cohort lists. The
removed phase-4 environment variable names
(`CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_CANARY_PERCENT`,
`CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ALLOW_API_KEY_IDS`,
`CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_DENY_API_KEY_IDS`)
MUST be covered by the existing removed-settings startup warning: they are
ignored without failing startup and reported in the single warning log
(names only, never values) for at least one release.

#### Scenario: Phase-4 removed env vars are ignored with one startup warning

- **GIVEN** a deployment whose environment still sets
  `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_CANARY_PERCENT` or
  the allow/deny list variables
- **WHEN** the application starts
- **THEN** startup succeeds and the values are ignored
- **AND** exactly one warning log lists the removed names without their
  values

#### Scenario: Prewarm eligibility is the enabled flag alone

- **GIVEN** `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED=true`
- **WHEN** a first-turn Codex bridge request arrives on a session that has
  not been prewarmed
- **THEN** the session prewarm is attempted for that request
- **AND** no request is excluded by canary sampling or an allow/deny cohort

#### Scenario: Prewarm stays off by default

- **GIVEN** a default install with no prewarm variables set
- **WHEN** Codex bridge requests are served
- **THEN** no session prewarm is attempted and visible requests record
  `prewarm_status=not_applicable`
