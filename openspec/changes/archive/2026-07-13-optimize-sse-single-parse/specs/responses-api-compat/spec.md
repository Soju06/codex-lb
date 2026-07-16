# responses-api-compat Delta

## ADDED Requirements

### Requirement: Streaming events are parsed once and re-serialized only when modified

Within each streaming layer (core client consumer, streaming mixin, bridge upstream reader, /v1 normalizers), an SSE event's JSON payload MUST be parsed at most once and reused by that layer's consumers, and an event that no consumer modified MUST NOT be re-serialized by the /v1 normalizers. Event framing, payload contents, dedupe/rewrite semantics, and error normalization MUST be unchanged.

#### Scenario: Unmodified events pass through the /v1 normalizer verbatim

- **GIVEN** a canonical stream event that no normalizer branch rewrites
- **WHEN** the /v1 response normalizer processes it
- **THEN** the original block is yielded byte-identically without re-serialization

#### Scenario: Tool-call rewrite reuses the parsed event on the no-change path

- **GIVEN** an event without duplicate parallel tool calls
- **WHEN** the rewrite step runs with the caller's parsed event
- **THEN** it returns the original line, payload, and event without re-parsing

#### Scenario: Rewritten events stay consistent

- **WHEN** the rewrite step removes duplicate tool calls
- **THEN** the returned line, payload, and validated event all reflect the rewritten content
