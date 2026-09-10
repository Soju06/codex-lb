# Change: Add a raw HTTP/2 controlled observer

## Why

The existing mitmproxy capture sees negotiated HTTP/2 and decoded headers but
does not retain the client connection preface, ordered SETTINGS, initial flow
control, or raw HEADERS/CONTINUATION frame shape. Those are the highest-value
remaining server-observable gaps and cannot be inferred safely from an
application-level flow.

## What Changes

- Add an explicitly launched TLS HTTP/2 deterministic origin that terminates
  the client connection itself and records bounded raw frame metadata.
- Record ordered SETTINGS, connection-control frames, stream reuse, decoded
  header-name order, and header-block length/digest without retaining header
  values, request bodies, source addresses, or TLS key material.
- Serve deterministic model-discovery, HTTP JSON, and SSE Responses shapes so
  direct Codex and codex-lb can run the same controlled sequence.
- Add a standalone A/A′/C HTTP/2 profile comparator and Markdown/JSON report.
- Keep HPACK contents, packet/TCP behavior, and public-origin claims outside the
  evidence boundary.

## Capabilities

### Modified Capabilities

- `compatibility-tooling`: controlled probes can observe and compare raw
  HTTP/2 connection/profile metadata without credentials or prompt content.

## Impact

Traffic-analysis scripts, development dependencies, focused tests, and the
traffic-parity runbook are affected. The codex-lb runtime and public API are
unchanged.
