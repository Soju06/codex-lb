## MODIFIED Requirements

### Requirement: Direct WebSocket replay never mixes numeric response sequences

For direct Responses WebSocket requests, the proxy MUST NOT transparently replay
a request on a fresh upstream generation after any finite integer
`sequence_number` frame for that request has been successfully sent downstream.
When an upstream close would otherwise trigger replay after numeric exposure,
the proxy MUST settle the failed pending request without emitting frames from a
new upstream generation under the existing downstream response id, and MUST
close the downstream WebSocket with code 1011 so the client can retry on a fresh
transport. When an upstream terminal error would otherwise trigger quota,
authentication, security-work, or equivalent replay, the proxy MUST finalize and
surface that terminal error without reconnecting. Suppressed frames and
non-integer sequence sentinels MUST NOT by themselves disable otherwise-safe
replay.

#### Scenario: Sequenced response is interrupted before completion

- **WHEN** a direct WebSocket request has emitted `response.created` or another
  frame with a finite integer `sequence_number`
- **AND** upstream closes before a terminal response event
- **THEN** codex-lb does not transparently replay that request under the
  existing downstream response id
- **AND** no lower replay sequence is emitted downstream
- **AND** the downstream WebSocket closes with code 1011

#### Scenario: Unsafe replay settles request ownership

- **WHEN** sequenced replay is refused after upstream close
- **THEN** response-create admission, account-local leases, API-key
  reservations, and request logging are finalized exactly once
- **AND** the failed attempt does not become a successful continuity owner

#### Scenario: Sequenced retryable terminal event is not replayed

- **WHEN** a direct WebSocket request has successfully emitted a finite integer
  `sequence_number`
- **AND** upstream emits a terminal error that would ordinarily trigger
  transparent quota, authentication, or security-work replay
- **THEN** codex-lb does not reconnect or resend the request
- **AND** the terminal error is finalized and remains client-visible under the
  existing error contract

#### Scenario: Sequence-free startup remains replayable within a bounded budget

- **WHEN** upstream closes before any numeric sequence-bearing frame has been
  successfully sent downstream
- **AND** the request otherwise satisfies the direct-WebSocket replay guard
- **THEN** codex-lb MAY transparently replay the request on a fresh upstream
  connection
- **AND** codex-lb MUST cap these transparent created-only close replays at a
  finite implementation budget of three attempts
- **AND** after the budget is exhausted, codex-lb MUST surface the stream failure
  and finalize cleanup instead of replaying indefinitely

#### Scenario: Suppressed frame does not establish exposure

- **WHEN** codex-lb suppresses an upstream frame before downstream emission
- **AND** the suppressed frame contains a numeric `sequence_number`
- **THEN** that frame does not establish the downstream sequence watermark
