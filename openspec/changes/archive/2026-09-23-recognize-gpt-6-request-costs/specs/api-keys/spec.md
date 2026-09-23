## ADDED Requirements

### Requirement: GPT-6 request cost pricing is recognized

The system MUST recognize `gpt-6-astra`, `gpt-6-sol`, and `gpt-6-luna`, case-insensitively and with family-specific suffixed aliases, when computing API-key reservations, settled usage, request-log costs, and aggregate costs. It MUST use these USD-per-1M-token rates in input / cached-input / output order:

| Model | Standard | Fast/priority | Flex | Standard long context |
| --- | --- | --- | --- | --- |
| `gpt-6-astra` | `10 / 1 / 50` | `20 / 2 / 100` | `5 / 0.5 / 25` | `20 / 2 / 75` |
| `gpt-6-sol` | `2 / 0.2 / 10` | `4 / 0.4 / 20` | `1 / 0.1 / 5` | `4 / 0.4 / 15` |
| `gpt-6-luna` | `0.1 / 0.01 / 0.5` | `0.2 / 0.02 / 1` | `0.05 / 0.005 / 0.25` | `0.2 / 0.02 / 0.75` |

Long-context rates MUST apply to the full request only when total input tokens, including cached tokens, exceed 272,000. Fast/priority and Flex long-context rates MUST be respectively twice and half the standard long-context rates. Cached-input tokens MUST be deducted from uncached input before costing. The system MUST NOT assign a generic GPT-6 price to unrecognized GPT-6 families.

#### Scenario: Canonical and suffixed models have distinct costs

- **WHEN** a standard-tier request has 200,000 input tokens, 100,000 cached input tokens, and 100,000 output tokens
- **THEN** its cost is `$6.10` for Astra, `$1.22` for Sol, and `$0.061` for Luna
- **AND** a suffixed or uppercase name in the same family receives the same price

#### Scenario: Service tiers affect quota settlement and request logs

- **WHEN** a completed Sol request has 200,000 input tokens, 100,000 cached input tokens, and 100,000 output tokens
- **AND** its effective billable tier is `fast` or `priority`
- **THEN** request-log cost and API-key cost usage are `$2.44`
- **WHEN** the same usage has effective billable tier `flex`
- **THEN** request-log cost and API-key cost usage are `$0.61`

#### Scenario: Long-context tiers include cached input

- **WHEN** an Astra request has 300,000 input tokens, 50,000 cached input tokens, and 100,000 output tokens
- **THEN** its standard cost is `$12.60`
- **AND** its Fast/priority cost is `$25.20`
- **AND** its Flex cost is `$6.30`

#### Scenario: Exact threshold retains short-context rates

- **WHEN** a GPT-6 request has exactly 272,000 input tokens
- **THEN** short-context rates apply for its service tier
- **WHEN** its input grows to 272,001 tokens
- **THEN** long-context rates apply for its service tier

#### Scenario: Unrecognized GPT-6 family remains unpriced

- **WHEN** cost accounting receives `gpt-6-unknown`
- **THEN** it has no resolved pricing entry
