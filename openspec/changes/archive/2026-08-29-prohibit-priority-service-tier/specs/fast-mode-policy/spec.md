## MODIFIED Requirements

### Requirement: Operators can prohibit priority service tiers

The dashboard settings API MUST persist and return a boolean
`prohibitFastMode` setting. It MUST default to `false`. When enabled, the
service MUST prevent every outbound Responses request whose `service_tier`
canonicalizes to `priority`, regardless of whether the tier came from a model
alias, explicit client input, an API-key enforced tier, a default, or an
owner-forwarded payload. The administrator prohibition MUST take precedence
over API-key service-tier enforcement. A prohibited tier MUST be represented
on the upstream wire by omitting `service_tier`; absent and non-priority values
MUST retain their existing behavior. The setting MUST take effect before
model-source selection, quota reservation, request-state capture, request
logging, serialization, and upstream OpenAI forwarding for HTTP requests; a
Codex WebSocket connection MUST use the policy resolved when that connection
began. Each stripped tier MUST emit an info-level diagnostic containing the
request ID and stripped value.

#### Scenario: Operator disables Fast Mode for a harness alias

- **GIVEN** `prohibitFastMode` is enabled
- **WHEN** a Codex harness request uses `model: "gpt-5.6-sol-xhigh-fast"`
- **THEN** the upstream request uses `model: "gpt-5.6-sol"`
- **AND** the upstream request uses `reasoning.effort: "high"`
- **AND** the upstream request omits `service_tier`

#### Scenario: Operator disables an explicit priority tier

- **GIVEN** `prohibitFastMode` is enabled
- **WHEN** a client sends `service_tier: "priority"` or its canonical `"fast"` alias
- **THEN** routing, quota reservation, request logging, and the upstream payload observe no requested service tier
- **AND** the upstream request omits `service_tier`

#### Scenario: Global prohibition overrides API-key enforcement

- **GIVEN** `prohibitFastMode` is enabled
- **AND** an API key enforces `service_tier: "priority"`
- **WHEN** a request is prepared with that API key
- **THEN** API-key enforcement provenance remains available to its callers
- **AND** the effective upstream request omits `service_tier`

#### Scenario: Non-priority wire behavior is unchanged

- **GIVEN** `prohibitFastMode` is enabled
- **WHEN** a request has no `service_tier` or has a tier that does not canonicalize to `priority`
- **THEN** the policy does not change that field

#### Scenario: Fast Mode prohibition is disabled by default

- **GIVEN** the operator has not changed the setting
- **WHEN** a request explicitly selects priority or a supported qualified model alias contains the `fast` token
- **THEN** the request continues to use `service_tier: "priority"`

#### Scenario: Dashboard warmup obeys Fast Mode prohibition

- **GIVEN** `prohibitFastMode` is enabled
- **AND** the configured warmup model or API-key enforced model is a qualified Fast Mode alias
- **WHEN** the dashboard submits a warmup request
- **THEN** the upstream warmup request preserves the canonical model and reasoning effort
- **AND** the upstream warmup request omits `service_tier`

#### Scenario: WebSocket explicit priority obeys the connection policy

- **GIVEN** a Codex WebSocket connection began while `prohibitFastMode` was enabled
- **WHEN** a `response.create` frame carries `service_tier: "priority"`
- **THEN** its upstream payload omits `service_tier`
- **AND** its effective request state omits `service_tier`

#### Scenario: Policy changes are audited

- **WHEN** an operator changes `prohibitFastMode`
- **THEN** the `settings_changed` audit entry includes `prohibit_fast_mode` in `changed_fields`
