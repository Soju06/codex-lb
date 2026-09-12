## ADDED Requirements

### Requirement: GPT-6 Astra usage cost pricing matches the published rates

When computing API-key usage, request-log, reservation, or aggregate cost for
`gpt-6-astra`, the system MUST use these USD-per-1M-token rates for input,
cached input, and output:

| Model | Standard | Fast/priority | Flex | Standard long context |
| --- | --- | --- | --- | --- |
| `gpt-6-astra` | `10 / 1 / 50` | `20 / 2 / 100` | `5 / 0.50 / 25` | `20 / 2 / 75` |

The existing `priority` and `fast` service-tier aliases MUST use the
Fast/priority rates. Standard long-context rates MUST apply only when input
tokens exceed 272,000. Model aliases with a version or snapshot suffix MUST
resolve to the canonical `gpt-6-astra` entry.

Batch rates and cache-write rates MUST NOT be introduced into this contract
without corresponding proxy request and usage fields.

#### Scenario: Standard usage uses the published rate

- **WHEN** a standard-tier `gpt-6-astra` request has 200,000 input tokens,
  100,000 of them cached, and 1,000,000 output tokens
- **THEN** the token cost is `$51.10`

#### Scenario: Fast and Flex usage use their tier rates

- **WHEN** that same request is billed at the `fast` or `priority` tier
- **THEN** the token cost is `$102.20`
- **WHEN** it is billed at the `flex` tier
- **THEN** the token cost is `$25.55`

#### Scenario: Long context applies above the 272,000-token boundary

- **WHEN** a standard-tier `gpt-6-astra` request has 300,000 input tokens,
  50,000 of them cached, and 100,000 output tokens
- **THEN** the token cost is `$12.60`
- **AND** the same request billed at the `flex` tier costs `$6.30`

#### Scenario: The boundary itself stays on standard rates

- **WHEN** a request has exactly 272,000 input tokens
- **THEN** input is charged at the standard rate
- **WHEN** a request has 272,001 input tokens
- **THEN** input is charged at the long-context rate

#### Scenario: Snapshot names resolve to the canonical entry

- **WHEN** usage is reported for `gpt-6-astra-2026-04-30`
- **THEN** it resolves to the canonical `gpt-6-astra` pricing entry
