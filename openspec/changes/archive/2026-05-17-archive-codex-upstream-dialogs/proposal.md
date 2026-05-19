## Why

Operators need a durable, replayable record of Codex-to-upstream traffic for later incident audit and possible offline dataset preparation. Existing request logs and audit logs only preserve metadata, while console payload logging is transient and not structured as a training-friendly archive.

## What Changes

- Add an opt-in gzip JSONL conversation archive for upstream Codex Responses traffic.
- Store upstream request payloads, streamed SSE events, compact responses, and websocket frames with request/account metadata.
- Redact credential-bearing headers while preserving payload bodies and upstream events verbatim.
- Add archived payload inspection to the existing dashboard request details flow.

## Capabilities

### Modified Capabilities

- `proxy-runtime-observability`: operators can enable a durable full-fidelity upstream conversation archive.

## Impact

- Affected code: `app/core/conversation_archive.py`, upstream HTTP/SSE/compact/websocket clients, dashboard archive APIs, frontend archive viewer, settings, and tests.
- Operational impact: disabled by default; when enabled, archive files may contain private prompts, tool outputs, and model responses and must be stored as sensitive data.
