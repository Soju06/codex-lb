## Why

Image-capable Codex requests bypass the HTTP bridge and use the streaming selection path directly. When all eligible accounts are briefly at the local per-account stream or response-create cap, that path returns an immediate local 429 (`account_stream_cap` / `account_response_create_cap`). Codex retries several subagents at once, exhausts its client retry budget, and surfaces `exceeded retry limit, last status: 429 Too Many Requests` even though capacity may free seconds later.

## What Changes

- Treat local account stream/response-create cap failures as recoverable account-capacity waits across direct
  streaming, HTTP bridge session submission, and downstream WebSocket account selection.
- Reuse the original request budget while preserving stream leases for same-account retries and preferring an
  eligible spare account when continuity does not pin the request.
- Keep permanent no-account and local balancer `Rate limit exceeded. Try again in Ns` failures non-waitable.

## Impact

- Code: `app/modules/proxy/_service/support.py`, `app/modules/proxy/_service/streaming/retry.py`, `app/modules/proxy/_service/http_bridge/streaming.py`
- Tests: targeted proxy unit tests
