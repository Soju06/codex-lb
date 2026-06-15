## ADDED Requirements

### Requirement: Responses SSE parsing uses only CR/LF line boundaries

When parsing streamed Responses Server-Sent Events, the service MUST treat only
CR (`\r`), LF (`\n`), and CRLF (`\r\n`) as SSE line boundaries. The parser MUST
NOT split a `data:` field on other Unicode line-boundary characters such as
U+2028 LINE SEPARATOR or U+2029 PARAGRAPH SEPARATOR when those characters appear
inside the payload value. Multi-line `data:` fields delimited by CR, LF, or CRLF
MUST continue to be joined with `\n` before JSON decoding.

#### Scenario: Unicode separators inside JSON strings are preserved

- **WHEN** an upstream Responses SSE event contains a `data:` JSON payload whose
  string value includes unescaped U+2028 or U+2029
- **THEN** the parser preserves those characters inside the JSON string
- **AND** the event remains available to downstream response-event processing

#### Scenario: CR/LF-delimited multi-line data still joins

- **WHEN** an upstream Responses SSE event contains multiple `data:` lines
  delimited by CR, LF, or CRLF
- **THEN** the parser joins the field values with `\n`
- **AND** continues JSON decoding against the joined payload
