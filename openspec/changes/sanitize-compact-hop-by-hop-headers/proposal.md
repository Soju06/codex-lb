# Sanitize hop by hop headers on compact upstream requests

## Why

The compact route decodes an inbound HTTP request and rebuilds a JSON request
for the upstream Responses endpoint. When the inbound request uses chunked
transfer encoding, the rebuilt request currently retains
`Transfer-Encoding: chunked`. The upstream then interprets the request as an
invalid transport instead of the JSON body, producing a 400 response before
compaction runs.

## What Changes

- Remove fixed HTTP hop by hop headers and fields named by the inbound
  `Connection` header before the shared upstream JSON header builder returns.
- Continue deriving the canonical `Accept`, `Content-Type`, authorization, and
  selected account headers from the builder after filtering caller supplied
  values, while preserving native Codex identity and continuity headers.
- Add route coverage for a chunked compact request and focused header-builder
  coverage for connection-nominated fields.

## Non Goals

- Changing compact payload normalization, account selection, authentication,
  continuity routing, or upstream transport selection.
- Allowing caller supplied connection tokens to suppress fresh LB-owned
  authorization, account, or JSON negotiation headers.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: rebuilt compact requests do not forward inbound HTTP
  framing or connection-scoped headers.

## Impact

The shared Responses HTTP header builder and its compact route regressions are
affected. No dependency, database, configuration, or deployment changes are
required.
