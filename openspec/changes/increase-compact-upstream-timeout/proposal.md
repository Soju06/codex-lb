# Change Proposal: Increase compact upstream timeout

## Why

`/backend-api/codex/responses/compact` currently uses a hard-coded 60 second upstream read timeout. Real compact requests can legitimately take longer than that, which makes the proxy return `502 upstream_unavailable` even when the provider would have succeeded if given more time.

## What Changes

- add a dedicated `CODEX_LB_UPSTREAM_COMPACT_TIMEOUT_SECONDS` setting
- raise the default compact upstream timeout from 60 seconds to 300 seconds
- use that setting for both total and read timeout on upstream compact calls
- add regression coverage for compact read timeouts and timeout wiring

## Impact

- fewer false `502 upstream_unavailable` failures on slow but valid compact requests
- operators can tune compact latency tolerance independently from SSE stream idle timeout
