## Why

A verified durable Responses-Lite input prefix can contain a completed direct
tool call with a Codex `developer` message between the call and its matching
output. Because the Lite `additional_tools` bundle keeps that message inline,
the fresh full-resend classifier encounters it while the historical call is
pending and rejects the otherwise valid shape.

Two additional Responses-Lite resend shapes are now observed after the stored
prefix: a fresh `developer` message after retained final output plus one user
follow-up, and a fresh `developer` message between a custom tool call and its
matching output. Treating every fresh developer message as unsafe makes these
bounded, account-neutral resends fall back to anchor injection and can trigger
an upstream acknowledgement timeout.

## What Changes

- Keep the observed unphased, non-response-owned historical `developer`
  message transparent only while proving an exact durable pending-tool
  manifest from inline Responses-Lite input.
- Bound that historical transparency to a pending window that holds exactly one
  outstanding call and consumes at most one developer message, so parallel
  batches and duplicate developer messages stay fail-closed.
- Allow a fresh developer message after retained output only when the latest
  retained assistant message is `final_answer`, exactly one explicit user
  message follows it, and the developer message is terminal.
- Allow a fresh developer message in a tool suffix only when the entire suffix
  is exactly `custom_tool_call -> developer -> matching custom_tool_call_output`
  and the pair exactly equals the durable pending-tool manifest.
- Require fresh developer messages to contain exactly one account-neutral
  `input_text` part, exact `turn_id` metadata, known fields, no response-owned
  ID or phase, and no status other than `completed`.
- Keep function calls, apply-patch calls, parallel batches, extra leading or
  trailing items, malformed types, account-scoped content, and all unproven
  developer positions fail-closed.
- Leave non-Lite `input` and `messages` instruction hoisting unchanged; this
  change does not add hoist provenance to the durable proof.
- Add helper, bridge-unit, and public `/v1/responses` regressions for both
  observed fresh suffixes and their rejection boundaries.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: verified Responses-Lite developer-interleaved history
  and two bounded fresh developer suffixes can preserve the existing safe fresh
  full-resend path.

## Impact

- Code: replay classification; the existing HTTP bridge projection and owner
  selection contracts remain unchanged.
- Tests: focused replay-safety, bridge-unit, and HTTP bridge route coverage.
- Owner forwarding, retry policy, logging, storage, public schemas, and
  non-Lite instruction hoisting remain unchanged.
