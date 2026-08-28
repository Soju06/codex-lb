## Why

Codex sends `generate = false` prewarm requests before some GPT-5.6 turns. The
upstream can accept the prewarm and emit a sequenced `response.created`, then
lose the WebSocket before `response.completed`. The direct-WebSocket sequence
guard correctly prevents general replay after a numeric sequence is visible,
but it also rejects this no-generation prewarm and forces Codex to reconnect
even though no model output or tool call can have been produced.

## What Changes

- Permit the existing bounded, created-only replay for a narrowly verified
  Codex prewarm: `request_kind = "prewarm"`, `generate = false`, exactly one
  `response.created` event, sequence watermark `0`, and no visible output.
- Keep ordinary sequenced Responses requests, spoofed prewarm metadata, and
  prewarms with additional response progress on the existing fail-closed 1011
  path.
- Refuse the replay if a forwarded replay sequence would not advance beyond
  the sequence already exposed downstream.
- Track direct-WebSocket response progress accurately and add endpoint-level
  regressions for successful recovery and every fail-closed boundary.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Define the safe `generate = false` prewarm exception
  to the direct-WebSocket numeric-sequence replay guard.

## Impact

- Affected code: direct Responses WebSocket request classification, response
  progress tracking, replay eligibility, and sequence validation.
- Affected clients: Codex CLI GPT-5.6 prewarm requests can survive an abnormal
  upstream close after `response.created` without a client reconnect.
- Compatibility: normal turns retain the existing no-mixed-generation rule;
  no setting, migration, dashboard change, or deployment action is added.
