# responses-api-compat Delta

## MODIFIED Requirements

### Requirement: Streaming events are parsed once and re-serialized only when modified

Within each streaming layer (core client consumer, streaming mixin, bridge upstream reader, websocket relay, /v1 normalizers), an SSE event's JSON payload MUST be parsed at most once and reused by that layer's consumers, and an event that no consumer modified MUST NOT be re-serialized by the /v1 normalizers. Schema validation of the parsed payload MUST run only for stream lifecycle frames (`response.created`, `response.completed`, `response.incomplete`, `response.failed`, `error`); all other frames MUST be classified from the parsed payload's `type` field (with a typeless payload carrying an `error` object classifying as `error`).

A canonically framed SSE block — a leading `event: <type>` line followed by a single JSON-object `data:` line with LF framing — whose type requires no per-event consumer MAY skip payload parsing entirely and be relayed downstream with the upstream bytes verbatim (raw UTF-8 and upstream key order/spacing preserved; JSON-equivalent to the canonical re-encode). A frame MUST take the parse path when any consumer needs it: lifecycle/terminal frames, tool-call item frames (`response.output_item.added`, `response.output_item.done`), text-done frames (`response.output_text.done`, `response.content_part.done`), frames arriving while the TTFT first-token window is open (including a pending reasoning-delta window), frames carrying a `"service_tier"` marker, and any block without canonical framing (data-only blocks, multi-line data, or an `event:` field that does not lead the block). A parsed frame MUST be re-serialized with canonical `event: <type>` + `data:` framing when modified or when its source block lacked canonical framing. Legacy event-type alias rewrites MUST cover both the `data:` payload type and the `event:` framing line. Event framing, payload contents, dedupe/rewrite semantics, usage settlement, and error normalization MUST be unchanged.

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

#### Scenario: Unmodified canonical delta frames relay upstream bytes verbatim

- **GIVEN** a canonically framed `response.output_text.delta` frame containing raw UTF-8, arriving after the first visible token settled the TTFT window
- **WHEN** the streaming mixin processes it
- **THEN** the upstream block is yielded byte-identically without a JSON parse or `ensure_ascii` re-encode, and downstream text-visibility accounting still updates

#### Scenario: Data-only frames regain canonical framing

- **GIVEN** a delta frame without a leading `event:` line
- **WHEN** the streaming mixin processes it after the TTFT window settles
- **THEN** the frame is parsed and re-serialized with the canonical `event: <type>` line so named-event (EventSource) clients keep seeing the event name

#### Scenario: Legacy alias frames are rewritten on both lines

- **GIVEN** an upstream block whose `event:` line and `data:` payload both carry the legacy `response.text.delta` type
- **WHEN** the core client normalizes the block
- **THEN** both the `event:` framing line and the payload `type` read `response.output_text.delta`

#### Scenario: Error frames keep the full parse and rewrite path

- **GIVEN** a canonically framed `error` frame, or a frame whose payload carries a top-level `error` envelope
- **WHEN** the core client normalizes the stream for the SDK contract
- **THEN** the frame is parsed and rewritten to a terminal `response.failed` event exactly as before verbatim relay

#### Scenario: /v1 identity pass-through accepts verbatim raw-UTF-8 blocks

- **GIVEN** an upstream-verbatim canonical delta block containing raw UTF-8
- **WHEN** the /v1 normalizer leaves the parsed payload unmodified
- **THEN** the block passes through byte-identically (the identity gate compares parsed-payload object identity and the `event:` framing prefix, not re-serialized bytes)
