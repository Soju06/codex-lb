## ADDED Requirements

### Requirement: Async tool results remain pending across continuations

The proxy SHALL preserve `async: true` on emitted `function_call` and
`custom_tool_call` items. It SHALL allow a subsequent anchored response
to omit an async call result while retaining that call identity for later
output. It SHALL NOT synthesize an interrupted-tool result for a known
asynchronous call. Matching actual outputs SHALL complete the
corresponding pending async call without consuming unrelated pending
calls.

#### Scenario: Async work spans an intervening turn

- **GIVEN** a response emits async function call `call_a`
- **WHEN** an anchored follow-up contains a new user message without `call_a` output
- **THEN** no synthetic output for `call_a` is forwarded
- **AND** a later actual `call_a` output can be forwarded unchanged

#### Scenario: Async and synchronous calls coexist

- **GIVEN** the previous response contains async `call_a` and interrupted synchronous `call_b`
- **WHEN** an anchored follow-up omits both outputs
- **THEN** only `call_b` receives the existing synthetic interrupted output

#### Scenario: Durable recovery keeps synchronous pending calls

- **GIVEN** a completed response contains async `call_a` and synchronous `call_b`
- **WHEN** the proxy persists the durable pending-tool manifest
- **THEN** only `call_b` is stored for interrupted-output recovery

#### Scenario: Stored prefixes tolerate unresolved async calls

- **GIVEN** a stored HTTP-bridge prefix contains an unresolved async function call followed by a later user turn
- **WHEN** the proxy proves a durable full-resend suffix against the synchronous pending-tool manifest
- **THEN** the unresolved async call does not reject the prefix
- **AND** the synchronous suffix still matches

#### Scenario: Account-neutral replay accepts settled async pairs

- **GIVEN** a full-history retry contains an async function or custom tool call and its matching typed output
- **WHEN** the proxy proves the input is a self-contained fresh replay
- **THEN** the settled async pair is accepted
- **AND** an unresolved async call without an output remains admissible
- **AND** the durable suffix matcher ignores completed async pairs when comparing the synchronous pending-tool manifest

#### Scenario: Malformed async suffix items fail closed

- **GIVEN** a durable full resend settles the synchronous pending-tool manifest but includes an async function or custom tool call, or its output, that is not self-contained
- **WHEN** the proxy validates the recovery proof
- **THEN** every suffix item MUST have a nonblank string `call_id` and satisfy the existing self-contained tool-item rules before async items are excluded from manifest comparison
- **AND** invalid items MUST reject the proof without raising an internal error
- **AND** an unavailable continuity owner MUST produce the existing fail-closed compatibility error instead of an upstream replay

#### Scenario: No-manifest recovery retains asynchronous history

- **GIVEN** a durable HTTP-bridge response has only asynchronous pending calls and no synchronous pending-tool manifest
- **AND** a full resend matches the stored input prefix and retains a completed assistant message followed by fresh input
- **WHEN** the owner is lost or another instance recovers the session
- **THEN** unresolved async function and custom tool calls in the prefix or suffix MUST NOT block the retained-output proof
- **AND** matching typed async outputs, including delayed prefix-call outputs, MUST be accepted without synthetic results
- **AND** malformed, duplicate, or mismatched async items MUST fail closed
- **AND** async calls or their outputs alone MUST NOT replace the completed-assistant boundary or settle pending synchronous calls
- **AND** existing account-ownership and account-neutral replay checks MUST remain required
