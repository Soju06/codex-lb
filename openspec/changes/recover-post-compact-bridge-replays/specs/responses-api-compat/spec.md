## ADDED Requirements

### Requirement: Post-compact bridge replays preserve compact context

codex-lb MUST treat completed post-compaction replay context as self-contained when an HTTP bridge or Responses WebSocket request must recover a follow-up turn after compaction. A projected fresh replay payload MUST retain that compact context while removing response-owned bookkeeping ids.

Trimming a stored prefix because a session-level compact anchor was injected
MUST NOT by itself mark the unanchored request safe to replay. The proxy may
mark the unanchored request replay-safe only when the original anchor site had
already made that decision or when a durable full-resend proof shows the fresh
suffix is self-contained.

Account-neutral recovery MAY select another eligible account after the previous
owner has been excluded or proved silent. Requests that explicitly require a
preferred previous-response owner MUST continue to fail closed when that owner
is unavailable.

#### Scenario: id-free completed compaction and tool-search context survives fresh replay projection

- **GIVEN** a follow-up payload starts with a completed `compaction` item whose
  encrypted content is non-empty
- **AND** that compaction item does not carry an `id`
- **AND** the payload also carries a completed `tool_search_call` /
  `tool_search_output` pair followed by a fresh user message
- **WHEN** codex-lb projects an account-neutral fresh replay payload
- **THEN** the projected payload includes the compaction item
- **AND** it preserves the completed tool-search pair without response-owned ids
- **AND** the projected payload is eligible for account-neutral replay

#### Scenario: session-level compact trim does not fabricate replay safety

- **GIVEN** a session-level compact anchor trimmed a stored prefix from a
  follow-up request
- **AND** the original request state was not already known to be safe as an
  unanchored fresh replay
- **WHEN** the bridge records the retained fresh request text
- **THEN** codex-lb does not mark that retained request as retry-safe solely
  because the trim happened

#### Scenario: account-neutral recovery can leave a silent owner

- **GIVEN** an account-neutral HTTP bridge recovery request excludes the previous
  owner account after it failed to acknowledge `response.create`
- **WHEN** another eligible account is available
- **THEN** codex-lb reconnects on that replacement account and sends the retained
  request there

#### Scenario: required previous-response owner still fails closed

- **GIVEN** a follow-up request explicitly requires its preferred
  previous-response owner account
- **WHEN** that owner is unavailable
- **THEN** codex-lb returns the previous-response-owner-unavailable failure
  instead of silently rebinding the request to another account
