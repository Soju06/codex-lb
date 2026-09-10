## ADDED Requirements

### Requirement: Completed tool-search replay stays portable only when client-owned

The Responses replay-safety predicate MUST treat a completed
`tool_search_call` / `tool_search_output` pair as account-neutral only when the
pair is self-contained and any declared execution owner is `client`. The call
MUST carry dictionary `arguments`; the output MUST carry a `tools` list or a
string `output`; and the output MUST NOT declare `execution: "server"` or any
other non-client owner. The HTTP bridge and WebSocket retry paths MAY trim a
replayed tool-search call after this predicate succeeds, but MUST preserve its
matching output and following user input.

#### Scenario: Client-owned tool-search pair can move accounts

- **GIVEN** replay input containing a completed `tool_search_call` with
  dictionary `arguments`
- **AND** a matching completed `tool_search_output` with a `tools` list
- **AND** both items either omit `execution` or declare `execution: "client"`
- **WHEN** the proxy evaluates the replay for account-neutral retry
- **THEN** the tool-search pair is accepted as portable replay state

#### Scenario: Server-owned tool-search output fails closed

- **GIVEN** replay input containing a completed client-owned
  `tool_search_call`
- **AND** its matching completed `tool_search_output` declares
  `execution: "server"`
- **WHEN** the proxy evaluates the replay for account-neutral retry
- **THEN** the replay is rejected as non-portable

### Requirement: Compact triggers are terminal and singular

The canonical Responses compact endpoint MUST forward one terminal
`compaction_trigger` unchanged. It MUST reject duplicate triggers and
non-terminal trigger states before dispatching upstream work. The OpenAI
compatible `/v1/responses/compact` endpoint MUST retain its existing duplicate
terminal trigger normalization for compatible clients.

#### Scenario: One terminal trigger is forwarded

- **GIVEN** a compact request whose input contains exactly one terminal
  `compaction_trigger`
- **WHEN** the proxy forwards the request upstream
- **THEN** that trigger remains in the forwarded input

#### Scenario: Duplicate trigger is rejected before upstream work

- **GIVEN** a compact request whose input contains more than one
  `compaction_trigger`
- **WHEN** the proxy validates the request
- **THEN** it returns a client error before dispatching upstream work

#### Scenario: OpenAI-compatible compact normalizes duplicate terminal triggers

- **WHEN** a client calls `POST /v1/responses/compact` with duplicate terminal
  top-level `compaction_trigger` items
- **THEN** codex-lb preserves the existing compatibility behavior and returns
  HTTP 200 when the compact operation succeeds
- **AND** the forwarded compact input contains one terminal trigger
