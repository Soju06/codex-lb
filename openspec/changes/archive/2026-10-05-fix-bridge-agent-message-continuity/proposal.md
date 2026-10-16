## Why

Codex can interrupt a response to append an `agent_message` from a subagent. On
a replacement HTTP bridge, the retained full input then fails the durable
replay proof even when it contains the complete prior tool-call manifest.
The bridge injects a socket-local `store=false` response anchor, trims away
the supplied history, and fails with `previous_response_not_found`.

## What Changes

- Recognize well-formed agent messages as new input in the same-owner durable
  context proof, after retained assistant output or a fully settled tool manifest.
- Preserve the original full request on fresh bridges and bounded same-owner
  stale-anchor recovery, including opaque encrypted content.
- Prevent preferred-account fallback or owner retirement from moving a proved
  same-owner full resend without separate account-neutral authorization.
- Keep account-neutral replay and incomplete-context rejection unchanged.
- Add route-level regressions for replacement sockets and stale-anchor rejection.

## Impact

- Affected spec: `responses-api-compat`.
- Affected code: replay-safety helpers and HTTP bridge context classification.
- No settings, schema changes, or client wire-format changes.
