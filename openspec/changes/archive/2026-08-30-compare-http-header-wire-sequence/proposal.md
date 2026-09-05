# Change: Compare HTTP header wire sequence

## Why

The server-observable report compares selected header values but the capture
addon collapses headers into a mapping. That loses duplicate field names,
original casing, and serialization order, so it cannot evaluate one of the
remaining direct-Codex versus codex-lb wire distinctions.

## What Changes

- Capture the ordered request header-name sequence, including duplicates and
  original casing, without retaining any additional header values.
- Compare A/C normalized name order and exact casing as independent,
  informational server-observable dimensions.
- Keep captures made before this change explicitly unobserved instead of
  treating missing sequence evidence as a match.
- Document that decoded header sequence still does not attest HPACK encoding or
  HTTP/2 SETTINGS/frame behavior.

## Capabilities

### Modified Capabilities

- `compatibility-tooling`: controlled traffic captures preserve and compare
  privacy-safe HTTP request header sequence evidence.

## Impact

The mitmproxy addon, comparison JSON, Markdown report, traffic-parity runbook,
and focused analyzer tests are affected. Runtime codex-lb egress behavior is
unchanged.
