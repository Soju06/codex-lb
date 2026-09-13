# responses-api-compat Delta

## ADDED Requirements

### Requirement: Rebuilt compact requests omit connection scoped headers

When the proxy decodes a `POST /backend-api/codex/responses/compact` request
and rebuilds its JSON request for the upstream Responses endpoint, the
outbound headers MUST omit fixed HTTP hop by hop fields, including
`Connection`, `Keep-Alive`, `Proxy-Authenticate`, `Proxy-Authorization`,
`Proxy-Connection`, `TE`, `Trailer`, `Transfer-Encoding`, and `Upgrade`.
The outbound headers MUST also omit every inbound field named by the
`Connection` header, case insensitively. The builder MUST still regenerate its
canonical LB-owned `Authorization`, account, `Accept`, and `Content-Type`
fields after filtering so a caller cannot use connection nomination to disable
required credentials or JSON negotiation.

Native Codex identity headers, selected authorization and account headers, and
continuity headers such as `x-codex-turn-state` MUST retain their existing
values and routing semantics.

#### Scenario: Chunked compact request is rebuilt without transfer encoding

- **WHEN** a native Codex client sends a compact request with
  `Transfer-Encoding: chunked`
- **THEN** the upstream rebuilt JSON request omits `transfer-encoding`
- **AND** compaction proceeds using the decoded JSON payload
- **AND** native identity, selected authorization, account, and continuity
  headers remain available to the upstream request

#### Scenario: Connection nominated headers are not forwarded

- **WHEN** an inbound compact request contains `Connection: keep-alive,
  x-client-hop`
- **AND** it contains an `x-client-hop` header
- **THEN** the upstream rebuilt request omits `connection` and `x-client-hop`
- **AND** ordinary `Accept` and `Content-Type` headers retain their canonical
  builder behavior
