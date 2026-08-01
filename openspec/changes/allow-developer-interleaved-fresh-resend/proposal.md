## Why

A verified durable Responses-Lite input prefix can contain a completed direct
tool call with a Codex `developer` message between the call and its matching
output. Because the Lite `additional_tools` bundle keeps that message inline,
the fresh full-resend classifier encounters it while the historical call is
pending and rejects the otherwise valid shape. The request then falls back to
anchor injection instead of preserving the original resend on the durable
owner.

## What Changes

- Treat the observed unphased, non-response-owned historical `developer`
  message as transparent only while proving an exact durable pending-tool
  manifest from inline Responses-Lite input.
- Keep the matching historical output mandatory and keep the fresh suffix
  restricted to complete direct call/output pairs.
- Leave non-Lite `input` and `messages` instruction hoisting and classification
  unchanged; this change does not add hoist provenance to the durable proof.
- Add helper-level fail-closed coverage and a public `/v1/responses` bridge
  regression using the observed interleaving.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: verified Responses-Lite developer-interleaved history
  can preserve the existing safe fresh full-resend path.

## Impact

- Code: replay classification plus the HTTP bridge call site that preserves
  developer-message ID evidence during classification.
- Tests: focused replay-safety and existing HTTP bridge route coverage.
- Retained-output replay, leading commentary, owner forwarding, retry policy,
  logging, storage, and public schemas are unchanged.
