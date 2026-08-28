## ADDED Requirements

### Requirement: GPT-5.6 personality pricing is recognized

The system MUST recognize `gpt-5.6`, `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` when computing request costs. The bare `gpt-5.6` alias MUST resolve to Sol, and suffixed aliases for each personality model MUST resolve to the matching canonical pricing entry. Standard, Flex, Priority, and requests with more than 272K input tokens MUST use the published rates applicable to the model and tier.

#### Scenario: Canonical GPT-5.6 models use personality-specific pricing

- **WHEN** a standard-tier request completes for `gpt-5.6-sol`, `gpt-5.6-terra`, or `gpt-5.6-luna`
- **THEN** the system computes cost using that model's standard input, cached-input, and output rates

#### Scenario: Bare GPT-5.6 alias resolves to Sol pricing

- **WHEN** a request completes for `gpt-5.6`
- **THEN** the system resolves it to the canonical Sol pricing entry
- **AND** the system does not use the generic `gpt-5` pricing entry

#### Scenario: Suffixed GPT-5.6 model resolves to its personality price

- **WHEN** a request completes for a suffixed GPT-5.6 personality model ID
- **THEN** the system resolves it to the matching canonical Sol, Terra, or Luna pricing entry
- **AND** the system does not use the generic `gpt-5` pricing entry

#### Scenario: GPT-5.6 service tiers use published tier rates

- **WHEN** a GPT-5.6 request completes with `service_tier: "flex"` or `service_tier: "priority"`
- **THEN** the system computes cost using the published rates for that model and service tier

#### Scenario: GPT-5.6 long-context request uses published uplift

- **WHEN** a standard-tier or Flex GPT-5.6 request completes with more than 272K input tokens
- **THEN** the system computes cost using the published long-context input, cached-input, and output rates for that model and tier
