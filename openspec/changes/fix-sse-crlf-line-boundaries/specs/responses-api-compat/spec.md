## ADDED Requirements

### Requirement: Responses SSE parsing uses only CR/LF line boundaries

When parsing streamed Responses Server-Sent Events, the service MUST treat only
CR (`\r`), LF (`\n`), and CRLF (`\r\n`) as SSE line boundaries. The parser MUST
NOT split a `data:` field on other Unicode line-boundary characters such as
U+2028 LINE SEPARATOR or U+2029 PARAGRAPH SEPARATOR when those characters appear
inside the payload value. Multi-line `data:` fields delimited by CR, LF, or CRLF
MUST continue to be joined with `\n` before JSON decoding.

The streaming HTTP receive path MUST also treat CR-only blank lines (`\r\r`) as
complete SSE event separators, and any normalization of legacy event aliases
MUST preserve the event block's original CR, LF, or CRLF terminator style.

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

#### Scenario: CR-only event separators dispatch complete events

- **WHEN** the HTTP streaming receive path receives an upstream SSE event ending
  in a CR-only blank line
- **THEN** it dispatches that event without waiting for EOF or an LF delimiter
- **AND** legacy event alias normalization preserves the CR-only blank-line
  terminator
