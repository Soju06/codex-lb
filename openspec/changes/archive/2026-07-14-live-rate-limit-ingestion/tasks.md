## 1. Core parsing and hub

- [x] 1.1 `app/core/usage/live_snapshots.py`: typed snapshot dataclass + parsers for x-codex response headers and `codex.rate_limits` event payloads.
- [x] 1.2 `app/core/usage/live_hub.py`: publish/no-op hub with startup registration.

## 2. Ingestor

- [x] 2.1 `app/modules/usage/live_ingest.py`: bounded queue (drop-oldest), single consumer with its own background sessions, per-account fingerprint + min-interval throttle, usage-history writes with credits fields, selection-cache invalidation on write.
- [x] 2.2 Settings: `live_usage_ingestion_enabled` (default true), `live_usage_write_min_interval_seconds` (default 5), queue size; startup wiring in main.py.

## 3. Tap points

- [x] 3.1 HTTP/SSE: publish header snapshots when upstream response headers arrive and event snapshots on `codex.rate_limits` blocks in `_stream_responses_with_session`.
- [x] 3.2 WS bridge: publish event snapshots for `codex.rate_limits` frames in the upstream relay.

## 4. Validation

- [x] 4.1 Unit: parser edge cases; throttle fingerprint/interval; queue overflow drop-oldest; hub no-op.
- [x] 4.2 Integration: SSE stream with rate-limit event writes rows for the serving account; kill switch produces no writes.
- [x] 4.3 `openspec validate live-rate-limit-ingestion --strict`; targeted proxy/usage suites.
