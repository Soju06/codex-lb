# Add GPT-6.2 Astra and Sol usage pricing

## Why

The proxy serves Astra and Sol, but its bundled defaults lack their prices. Usage can go unpriced when the active catalog lacks these models.

## What Changes

- Add Standard and Fast fallback rates and snapshot aliases for both models.
- Use the existing Standard fallback for Flex requests. OpenAI lists no Flex rates for these models.

## Capabilities

- `api-keys` gains fallback pricing for GPT-6.2 Astra and Sol.

Sources checked November 21, 2026. [OpenAI pricing](https://developers.openai.com/api/docs/pricing), [Astra](https://developers.openai.com/api/docs/models/gpt-6.2-astra), [Sol](https://developers.openai.com/api/docs/models/gpt-6.2-sol)
