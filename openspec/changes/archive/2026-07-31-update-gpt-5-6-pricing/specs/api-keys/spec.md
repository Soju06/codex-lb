## ADDED Requirements

### Requirement: GPT-5.6 usage cost pricing matches the current published rates

When computing API-key usage, request-log, reservation, or aggregate cost for
the canonical GPT-5.6 models, the system MUST use these USD-per-1M-token rates
for input, cached input, and output:

| Model | Standard | Fast/priority | Flex | Standard long context |
| --- | --- | --- | --- | --- |
| `gpt-5.6-sol` | `5 / 0.50 / 30` | `10 / 1 / 60` | `2.5 / 0.25 / 15` | `10 / 1 / 45` |
| `gpt-5.6-terra` | `2 / 0.20 / 12` | `4 / 0.40 / 24` | `1 / 0.10 / 6` | `4 / 0.40 / 18` |
| `gpt-5.6-luna` | `0.20 / 0.02 / 1.20` | `0.40 / 0.04 / 2.40` | `0.10 / 0.01 / 0.60` | `0.40 / 0.04 / 1.80` |

The existing `priority` and `fast` service-tier aliases MUST use the
Fast/priority rates. Standard long-context rates MUST apply only when input
tokens exceed 272,000. Flex long-context pricing MUST continue to use the
existing Flex short-context rates and multipliers. Model aliases with a
version or snapshot suffix MUST resolve to the corresponding canonical table
entry.

Batch rates and cache-write rates MUST NOT be introduced into this contract
without corresponding proxy request and usage fields.

#### Scenario: Terra standard usage uses the current rate

- **WHEN** a standard-tier `gpt-5.6-terra` request has 200,000 input tokens and 1,000,000 output tokens
- **THEN** the token cost is `$12.40`

#### Scenario: Luna Fast and Flex usage use their tier rates

- **WHEN** a `gpt-5.6-luna` request has 200,000 input tokens, 100,000 cached input tokens, and 1,000,000 output tokens
- **AND** the request uses `priority` or `fast`
- **THEN** the token cost is `$2.444`
- **WHEN** the same usage uses `flex`
- **THEN** the token cost is `$0.611`

#### Scenario: Terra standard long-context usage uses the current long-context rate

- **WHEN** a standard-tier `gpt-5.6-terra` request has 300,000 input tokens, 50,000 cached input tokens, and 100,000 output tokens
- **THEN** the token cost is `$2.82`

#### Scenario: Versioned aliases use canonical GPT-5.6 pricing

- **WHEN** the requested model is `gpt-5.6-luna-2026-07-13`
- **THEN** cost accounting resolves it to the `gpt-5.6-luna` price entry
