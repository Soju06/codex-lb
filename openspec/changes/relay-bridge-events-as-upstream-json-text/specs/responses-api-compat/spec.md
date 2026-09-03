## ADDED Requirements

### Requirement: HTTP bridge relays unchanged upstream events as their upstream JSON text

When an HTTP bridge session relays an upstream Responses event to a pending
request's downstream SSE stream and the proxy did not change the event's JSON
value (no downstream response-id alignment, tool-call rewrite, or error
masking applied), the relayed `data:` line MAY be the upstream JSON text
verbatim instead of a proxy re-serialization. The relayed block MUST use the
canonical framing `event: <type>\ndata: <json>\n\n` when the payload carries a
non-empty string `type`, and data-only framing `data: <json>\n\n` otherwise.
Any SSE-compliant parser MUST obtain the identical JSON value from the relayed
block that it would obtain from the proxy's re-serialized form. Events whose
JSON value the proxy changed MUST continue to be re-serialized from the
rewritten payload.

#### Scenario: Unchanged event with non-ASCII text is relayed as upstream UTF-8

- **GIVEN** an HTTP bridge request whose upstream response id already matches
  its downstream response id
- **WHEN** the upstream emits a single-line `response.output_text.delta` event
  whose `delta` contains Korean text and U+2028
- **THEN** the downstream block is `event: response.output_text.delta` followed
  by a `data:` line containing the upstream JSON text unchanged
- **AND** parsing the block yields the same JSON value as parsing the proxy's
  re-serialized form of that event

#### Scenario: Unchanged ASCII event is byte-identical to re-serialization

- **WHEN** the upstream emits a compact, ASCII-only event that the proxy does not
  rewrite
- **THEN** the relayed block is byte-identical to the block the proxy would have
  produced by re-serializing the parsed payload

#### Scenario: Rewritten event is re-serialized

- **GIVEN** an HTTP bridge request whose downstream response id differs from the
  upstream response id
- **WHEN** the upstream emits an event carrying the upstream response id
- **THEN** the relayed block is re-serialized from the rewritten payload and
  carries the downstream response id
- **AND** the upstream JSON text does not appear in the relayed block

#### Scenario: Typeless error frame stays data-only

- **WHEN** an upstream frame is a JSON object without a string `type` field
- **THEN** the relayed framing derived from that payload has no `event:` line
