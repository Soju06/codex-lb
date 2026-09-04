## ADDED Requirements

### Requirement: GPT-6 Astra pricing is recognized

The system MUST recognize `gpt-6-astra` when computing request costs. Suffixed
aliases matching `gpt-6-astra*` MUST resolve to the canonical `gpt-6-astra`
pricing entry. The bare `gpt-6` alias MUST resolve to `gpt-6-astra` pricing and
MUST NOT resolve through generic GPT-5 pricing.

For `gpt-6-astra`, standard requests MUST use `$10.00` per 1M input tokens,
`$1.00` per 1M cached input tokens, and `$50.00` per 1M output tokens.
Priority/Fast requests MUST use `$20.00`, `$2.00`, and `$100.00` respectively.
Flex/Batch requests MUST use `$5.00`, `$0.50`, and `$25.00` respectively.
Requests above `272000` input tokens MUST apply long-context rates by service
tier: standard uses `$20.00` input, `$2.00` cached input, and `$75.00` output;
Priority/Fast uses `$20.00`, `$2.00`, and `$100.00`; Flex/Batch uses `$10.00`,
`$1.00`, and `$37.50`. Cache-write pricing is not represented until the pricing
schema gains a cache-write field.

#### Scenario: Canonical GPT-6 Astra uses standard pricing

- **WHEN** a standard-tier request completes for `gpt-6-astra`
- **THEN** the system computes cost using `$10.00` input, `$1.00` cached input, and `$50.00` output rates per 1M tokens

#### Scenario: GPT-6 Astra aliases resolve to Astra pricing

- **WHEN** a request completes for `gpt-6` or a suffixed `gpt-6-astra` model ID
- **THEN** the system resolves it to the canonical `gpt-6-astra` pricing entry
- **AND** the system does not use any GPT-5 pricing entry

#### Scenario: GPT-6 Astra service tiers use published tier rates

- **WHEN** a `gpt-6-astra` request completes with `service_tier: "priority"`, `"fast"`, `"flex"`, or an observed accounting tier of `"batch"`
- **THEN** the system computes cost using the published rates for that service tier

#### Scenario: GPT-6 Astra long-context request uses published tier rates

- **WHEN** a `gpt-6-astra` request completes with more than 272K input tokens
- **THEN** the system computes cost using the long-context rates for the request service tier
