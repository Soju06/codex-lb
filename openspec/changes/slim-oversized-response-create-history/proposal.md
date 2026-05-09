# Proposal: slim-oversized-response-create-history

## Why

Large Codex `response.create` requests can still exceed the upstream websocket
budget after the existing inline-image and tool-output slimming pass. In that
case the proxy returns `413 payload_too_large` even when the oversized content is
old conversation history and the current user turn could be preserved.

The oversized debug dump path also used a Docker-only data directory. Local
`uv tool` or LaunchAgent installs may not be allowed to create `/var/lib/codex-lb`,
so the diagnostic dump can fail exactly when it is needed.

## What Changes

- After existing historical image/tool-output slimming, omit the oldest
  historical input items until the serialized `response.create` fits the
  upstream websocket budget.
- Preserve the recent user-turn suffix and insert a single assistant notice that
  says how many historical input items were omitted.
- Use the configured default app home directory for oversized response-create
  dumps, preserving `/var/lib/codex-lb` in containers while using
  `~/.codex-lb` for local installs.

## Impact

- Fewer oversized Codex websocket requests fail with `413` when old history can
  be safely summarized away.
- Debug dumps remain writable in both Docker and local user installs.
