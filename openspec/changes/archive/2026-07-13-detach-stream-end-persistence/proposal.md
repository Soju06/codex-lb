## Why

Every proxied request's stream close waited on two inline database transactions: the request-log INSERT (task + `asyncio.shield`) and, for keyed requests, the API-key reservation settlement (~5+2N statements). Codex CLI does not continue until the stream closes, so both writes added tens of milliseconds of tail latency to every turn — while the machinery to run them detached (task tracking with failure logging and release-on-failure fallbacks) already existed for the cancellation path.

## What Changes

- The request-log write and the stream API-key settlement detach unconditionally into their existing tracked task sets instead of being shield-awaited; the response path no longer blocks on either transaction.
- Settlement keeps its full safety net: the tracking callback schedules a reservation release when settlement fails or is cancelled, the caller's finally-net is skipped via the transferred flag, and reservations keep counting toward limits until finalized/released — a lagging settlement can only over-restrict, never over-admit.
- New `ProxyService.drain_persistence_tasks(timeout)` flushes pending persistence; the shutdown path drains it (bounded by `shutdown_drain_timeout_seconds`) so graceful restarts do not lose the final requests' logs or settlements.
- The test suite's `async_client` drains after every response (httpx response hook) to keep the historical synchronous read-after-response semantics inside tests; the detach contract itself is pinned by dedicated hook-free tests.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `api-keys`: stream reservation settlement MUST NOT block the response path; failed/cancelled detached settlements MUST still release the reservation; shutdown MUST drain pending settlements.
- `proxy-runtime-observability`: request-log persistence MUST NOT block the response path and MUST be drained at shutdown.

## Impact

- **Code**: `app/modules/proxy/_service/request_log.py`, `app/modules/proxy/_service/api_key_usage.py`, `app/modules/proxy/service.py`, `app/main.py`, `tests/conftest.py`.
- **Behavior**: response/stream close no longer waits for persistence; dashboard visibility of a request's log row lags by milliseconds. Compact/websocket settlement paths and the reservation reaper are unchanged.
