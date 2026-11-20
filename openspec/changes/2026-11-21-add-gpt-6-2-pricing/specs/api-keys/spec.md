## ADDED Requirements

### Requirement: GPT-6.2 Astra and Sol have bundled usage prices

API-key usage, request-log, reservation, and aggregate cost calculations MUST use the following bundled rates when no catalog entry takes precedence under the upstream-metadata capability. Each cell lists input / cached input / output in USD per million tokens.

| Model | Standard short | Standard long | Fast short | Fast long |
| --- | --- | --- | --- | --- |
| `gpt-6.2-astra` | 8 / 0.40 / 40 | 16 / 0.80 / 60 | 16 / 0.80 / 80 | 32 / 1.60 / 120 |
| `gpt-6.2-sol` | 2 / 0.10 / 10 | 4 / 0.20 / 15 | 4 / 0.20 / 20 | 8 / 0.40 / 30 |

For these defaults, `fast` and `priority` MUST use Fast rates, and `flex` MUST use Standard rates. The system MUST apply long-context rates to the entire request when input exceeds 272,000 tokens and short-context rates otherwise. Versioned and snapshot aliases MUST resolve to the canonical entry when there is no exact price.

#### Scenario: A dated Astra snapshot uses Fast long-context rates

- **GIVEN** the bundled fallback is active
- **WHEN** `gpt-6.2-astra-2026-11-21` is requested with `fast`, 272,001 input tokens including 100,000 cached tokens, and 100,000 output tokens
- **THEN** its token cost is `$17.664032`
