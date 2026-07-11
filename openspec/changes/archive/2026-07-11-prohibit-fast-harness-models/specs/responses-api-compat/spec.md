## MODIFIED Requirements

### Requirement: Cursor GPT-5 model aliases normalize to canonical slugs

For Responses proxy traffic, the service MUST recognize Cursor-style GPT-5 model aliases formed by appending known suffix tokens
(`minimal`, `low`, `medium`, `high`, `xhigh`, `extra`, `fast`, `priority`, `reasoning`, `thinking`) to supported GPT-5 family slugs. The alias
resolver MUST match longer qualified canonical slugs before shorter family prefixes so aliases such as `gpt-5.4-mini-high` and `gpt-5.3-codex-fast` normalize
to the intended model. Unknown suffix tokens MUST leave the requested model unchanged. When the dashboard `prohibitFastMode` setting is enabled, an alias's
`fast` token MUST NOT derive `service_tier: "priority"`; canonical-model and reasoning-effort normalization MUST still apply. Explicit client service tiers and
API-key service-tier enforcement remain unchanged.

#### Scenario: Qualified mini model alias normalizes reasoning

- **WHEN** a client sends a Responses request with `model: "gpt-5.4-mini-high"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.4-mini"`
- **AND** the forwarded upstream request uses `reasoning.effort: "high"`

#### Scenario: Qualified codex model alias normalizes service tier

- **GIVEN** `prohibitFastMode` is disabled
- **WHEN** a client sends a Responses request with `model: "gpt-5.3-codex-fast"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.3-codex"`
- **AND** the forwarded upstream request uses `service_tier: "priority"`

#### Scenario: Fast Mode alias is forwarded at the normal tier

- **GIVEN** `prohibitFastMode` is enabled
- **WHEN** a client sends a Responses request with `model: "gpt-5.6-sol-xhigh-fast"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.6-sol"`
- **AND** the forwarded upstream request uses `reasoning.effort: "high"`
- **AND** the forwarded upstream request omits `service_tier`
