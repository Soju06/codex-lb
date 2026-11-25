## ADDED Requirements

### Requirement: Streamed Responses requests use identity response encoding

The Codex client MUST normalize inbound `Accept-Encoding` headers to `identity`
before dispatching a streamed Responses request through either native egress or
the aiohttp fallback. Non-streaming requests MAY retain their existing
compression negotiation.

#### Scenario: Compressed inbound header on a streamed request

- **WHEN** a streamed Responses request includes `Accept-Encoding: gzip, br,
  zstd`
- **THEN** the upstream request contains `Accept-Encoding: identity`
- **AND** both native and fallback transports receive the same normalized
  headers
