# responses-api-compat Delta

## MODIFIED Requirements

### Requirement: Streaming events are parsed once and re-serialized only when modified

Within each streaming layer (core client consumer, streaming mixin, bridge upstream reader, websocket relay, /v1 normalizers), an SSE event's JSON payload MUST be parsed at most once and reused by that layer's consumers, and an event that no consumer modified MUST NOT be re-serialized by the /v1 normalizers. Schema validation of the parsed payload MUST run only for stream lifecycle frames (`response.created`, `response.completed`, `response.incomplete`, `response.failed`, `error`); all other frames MUST be classified from the parsed payload's `type` field (with a typeless payload carrying an `error` object classifying as `error`). Event framing, payload contents, dedupe/rewrite semantics, usage settlement, and error normalization MUST be unchanged.

#### Scenario: Unmodified events pass through the /v1 normalizer verbatim

- **GIVEN** a canonical stream event that no normalizer branch rewrites
- **WHEN** the /v1 response normalizer processes it
- **THEN** the original block is yielded byte-identically without re-serialization

#### Scenario: Tool-call rewrite reuses the parsed event on the no-change path

- **GIVEN** an event without duplicate parallel tool calls
- **WHEN** the rewrite step runs with the caller's parsed event
- **THEN** it returns the original line, payload, and event without re-parsing or re-validating

#### Scenario: Rewritten events stay consistent

- **WHEN** the rewrite step removes duplicate tool calls
- **THEN** the returned line, payload, and validated event all reflect the rewritten content

#### Scenario: Delta frames skip schema validation

- **GIVEN** a stream of `response.output_text.delta` frames between `response.created` and `response.completed`
- **WHEN** the streaming mixin, websocket relay, or bridge upstream reader processes the stream
- **THEN** only the lifecycle frames are schema-validated, the delta frames are classified from the parsed payload dict, and downstream output, usage settlement, and error normalization are unchanged

#### Scenario: Identity websocket relay frames are forwarded without re-encoding

- **GIVEN** a websocket frame matched to a request whose downstream response-id rewrite does not apply
- **WHEN** the relay forwards the frame downstream
- **THEN** the upstream frame text is forwarded as-is instead of a canonical JSON re-encode
