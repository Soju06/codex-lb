## Why

Keyed HTTP SSE mid-loop failover previously wrote account health while the
shared API-key reservation was still open, and a later cancel-during-flush race
could drop deferred penalties after settlement had already committed. Operators
need settle-then-health ordering that survives cancellation the same way compact
keyed failover already does.

## What Changes

- Defer keyed stream mid-loop account-health writes until API-key settlement
  confirms (or fail-safe release confirms when ordered settle is unavailable).
- Record settlement success before awaiting deferred health flush so cancel
  during flush cannot skip retained-queue cleanup.
- Isolate deferred health entries so one failed or cancelled write cannot drop
  later penalties.
- Cover the ordering through the streaming Responses product entry point and
  cancel-during-flush regressions.

## Capabilities

### New Capabilities

### Modified Capabilities

- `api-keys`: keyed stream mid-loop failover MUST settle (or confirm release)
  before deferred account-health writes, and MUST preserve deferred penalties
  across cancellation after settlement commits.

## Impact

HTTP SSE `_stream_with_retry` keyed paths and the `/v1/responses` streaming
route that owns reservation lifecycle. Websocket terminal settle-before-health
and compact failover keep their existing contracts.
