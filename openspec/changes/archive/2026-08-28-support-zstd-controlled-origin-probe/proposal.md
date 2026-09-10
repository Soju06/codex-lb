# Change: Support Codex zstd requests in the controlled origin probe

## Why

Codex CLI 0.150.1 compresses HTTP Responses request bodies with
`Content-Encoding: zstd`. The controlled origin currently parses the encoded
bytes as JSON, so a real Codex HTTP/SSE probe fails before the origin can
produce a response.

## What Changes

- Decode zstd-encoded fixture requests before JSON parsing.
- Apply the existing 1 MiB request limit to both encoded input and decoded
  output so compressed payloads cannot bypass the probe bound.
- Reject malformed or unsupported content encodings with explicit client
  errors.
- Document the real Codex HTTP/SSE compatibility requirement in the controlled
  origin specification and runbook.

## Impact

- Affected spec: `compatibility-tooling`
- Affected code: `scripts/traffic_analysis/origin_fixture.py`
- Affected tests: `tests/unit/test_traffic_origin_fixture.py`
