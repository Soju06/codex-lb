## ADDED Requirements

### Requirement: HTTP bridge parses each upstream websocket frame as one JSON document

The HTTP Responses bridge MUST parse each upstream websocket text frame as one JSON document, including a frame whose JSON spans several lines delimited by LF, CRLF, or CR. It MUST NOT apply SSE line rules to a websocket frame. When the bridge relays a multi-line frame downstream, it MUST re-encode the parsed payload as a single-line `data:` block. Single-line frames MAY keep relaying the upstream text unchanged.

#### Scenario: Multi-line upstream error settles the waiting request

- **GIVEN** a request is waiting on the HTTP bridge for `response.created`
- **WHEN** the upstream websocket sends a pretty-printed `type: "error"` frame with `status: 400`
- **THEN** the bridge matches the error to the waiting request and ends it with that error
- **AND** the client receives the upstream 400 without waiting for the idle timeout

#### Scenario: Multi-line frame is relayed as one data line

- **WHEN** the bridge relays a parsed multi-line websocket frame downstream
- **THEN** the relayed SSE block carries the whole payload in a single `data:` line
