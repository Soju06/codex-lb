## Why

Streaming source-routed `POST /v1/responses` returned raw model-source SSE bytes without the normalize-and-keepalive wrapping that account-backed streaming applies. Idle upstream gaps could trip front-door proxy timeouts, split byte chunks could surface partial SSE frames, and native Codex clients selecting a model source lost their heartbeat framing (discovery `CLB-20260820-02`).

## What Changes

- Source-routed Responses streaming reassembles upstream byte chunks into complete SSE event blocks, applies public Responses normalization, and injects SSE keepalives.
- Native Codex source-routed streams preserve `codex.*` events and use `codex.keepalive` heartbeat framing like the subscription path.
- Reassembly preserves upstream terminator bytes for unmodified events, recognizes CR, LF, CRLF, and mixed blank-line separators, bounds buffered event size, and forwards unparseable source data verbatim without synthesizing a terminal event.
- Reservation settlement stays outermost and stream resources close deterministically on completion, failure, or cancellation.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Require source-routed Responses streaming to stay proxy-timeout friendly, transport-aware, byte-faithful for content the proxy does not rewrite, memory-bounded, and settlement-safe.

## Impact

- Affected code: `app/modules/proxy/api.py`, `app/core/clients/proxy.py`, `app/core/utils/sse.py`.
- Affected tests: source-responses integration coverage in `tests/integration/test_proxy_api_extended.py` and SSE separator unit coverage in `tests/unit/test_proxy_utils.py`, including a perf regression guard for the shared separator scan.
- No API surface, schema, persistence, dependency, or configuration changes.
