## Why

Live Codex traffic can still produce `context_length_exceeded` request-log rows
even when remote compaction is firing. The current live snapshot reports those
rows only as generic recent errors, so operators cannot quickly tell which
transport, model, reasoning effort, token shape, or session family is still
overflowing.

## What Changes

- Lower the host Codex auto-compaction threshold to leave more headroom before
  the configured model context window.
- Add a bounded `context_length_exceeded` section to the live snapshot helper.
- Include safe recent-row fields for context-window failures, including a hashed
  session identifier when request logs have one.
- Keep prompts, auth headers, raw request payloads, and raw session ids out of
  diagnostic output.

## Capabilities

### Modified Capabilities

- `proxy-runtime-observability`: live snapshot diagnostics expose safe
  context-window failure correlation fields.

## Impact

- Host config: lowers `model_auto_compact_token_limit` for new Codex sessions.
- Ops diagnostics: `scripts/codex_lb_live_snapshot.py` exposes bounded
  context-window failure groups and recent rows.
- Tests: focused unit coverage for the new snapshot output.
