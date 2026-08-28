# Stale anchor diagnostic metadata

## Summary

Add operator-facing diagnostics for direct WebSocket `previous_response_not_found` / `codex_previous_response_stale` failures so the next incident can distinguish client-supplied stale anchors from proxy-injected session anchors and from owner-routing misses.

## Why

Recent production stale-anchor incidents were ambiguous even after matching request-log rows: codex-lb could prove owner lookup hit and selected the preferred account, but logs did not say whether the rejected `previous_response_id` came from the client payload or from proxy session-continuity injection, whether a fresh no-anchor replay was available, or whether the anchor crossed Codex turn sessions.

## What Changes

- Add structured stale-anchor diagnostic metadata to continuity fail-closed logs for direct WebSocket streams.
- Record enough request-log failure metadata to separate client/proxy/upstream classifications during post-incident SQL queries.
- Preserve existing retry/replay behavior; this change is observability-only.
- Add regression coverage for proxy-injected and client-supplied stale anchor metadata.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `responses-api-compat`: Adds observability requirements for Responses WebSocket stale-anchor failures.

## Non-Goals

- No database migration or new dashboard fields in this change.
- No change to retry decisions, account selection, or upstream payload shape.
- No client-side fix for clients that reuse invalid `previous_response_id` values.
