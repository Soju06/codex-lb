## ADDED Requirements

### Requirement: Operators can prohibit harness Fast Mode aliases

The dashboard settings API MUST persist and return a boolean
`prohibitFastMode` setting. It MUST default to `false`. When enabled, the
service MUST prevent a qualified supported GPT-5 model alias containing the
`fast` token from deriving the OpenAI `priority` service tier, while preserving
the alias's canonical base model and reasoning effort. The setting MUST take
effect before model-source selection, quota reservation, and upstream OpenAI
forwarding for HTTP requests; a Codex WebSocket connection MUST use the policy
that was resolved when that connection began.

#### Scenario: Operator disables Fast Mode for a harness alias

- **GIVEN** `prohibitFastMode` is enabled
- **WHEN** a Codex harness request uses `model: "gpt-5.6-sol-xhigh-fast"`
- **THEN** the upstream request uses `model: "gpt-5.6-sol"`
- **AND** the upstream request uses `reasoning.effort: "high"`
- **AND** the upstream request omits `service_tier`

#### Scenario: Fast Mode prohibition is disabled by default

- **GIVEN** the operator has not changed the setting
- **WHEN** a supported qualified model alias contains the `fast` token
- **THEN** the request continues to derive `service_tier: "priority"`

#### Scenario: Dashboard warmup obeys Fast Mode prohibition

- **GIVEN** `prohibitFastMode` is enabled
- **AND** the configured warmup model or API-key enforced model is a qualified Fast Mode alias
- **WHEN** the dashboard submits a warmup request
- **THEN** the upstream warmup request preserves the canonical model and reasoning effort
- **AND** the upstream warmup request omits `service_tier`

#### Scenario: Policy changes are audited

- **WHEN** an operator changes `prohibitFastMode`
- **THEN** the `settings_changed` audit entry includes `prohibit_fast_mode` in `changed_fields`
