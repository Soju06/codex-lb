# Advertise GPT-6 max output tokens in /v1/models

## Why

The upstream Codex model registry currently omits `max_output_tokens` for GPT-6 Astra, Sol, and Luna even though OpenAI documents a 128,000-token maximum output for those models. Codex-LB therefore emits `null` on the OpenAI-compatible `/v1/models` capability fields, causing clients that require a numeric capability to fall back to an arbitrary local default.

## What Changes

- Extend the existing `/v1/models` max-output fallback table with `gpt-6-astra`, `gpt-6-sol`, and `gpt-6-luna` at 128,000 tokens.
- Preserve existing precedence: an integer `max_output_tokens` supplied by upstream raw model metadata always wins over the fallback table.
- Add endpoint regression coverage for both the GPT-6 fallback and raw-upstream precedence.
- Do not change request routing, output generation, native Codex catalog semantics, context-window reporting, or account behavior.

## Capabilities

### Modified Capabilities

- `model-catalog-compat`: `/v1/models` advertises the known GPT-6 max-output budget when upstream omits it while remaining subordinate to explicit upstream metadata.

## Impact

This is a compatibility-metadata correction only. It changes the numeric max-output capability exposed to OpenAI-compatible catalog consumers; it does not force requests to generate 128,000 tokens and does not alter the upstream request payload.
