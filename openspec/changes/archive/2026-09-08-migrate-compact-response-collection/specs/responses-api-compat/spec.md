## ADDED Requirements

### Requirement: Native compact collection preserves terminal output assembly

For direct and account-routed compact SSE requests with negotiated native
collection, Rust MUST collect output items and assemble the terminal response.
The last item for each integer output index MUST win; indexed items MUST be
ordered numerically, followed by unindexed done items in arrival order. A
nonempty terminal output array MUST take precedence over collected items.
Unknown JSON fields and integer values MUST be preserved. Python MUST retain
public shape normalization, error translation, archives, routing, and settlement.

#### Scenario: Completion without terminal output

- **WHEN** output-item events precede a completed response with missing or empty output
- **THEN** the result includes collected items in the documented order
- **AND** returns on completion without waiting for HTTP EOF or interpreting later events

#### Scenario: Existing terminal output

- **WHEN** the completed response contains a nonempty output array
- **THEN** that array is returned without merging earlier collected items

#### Scenario: Terminal failure or missing completion

- **WHEN** an SSE stream fails, is incomplete, has no valid completed response object, or ends before completion
- **THEN** the existing compact error envelope and failure classification are preserved

#### Scenario: Missing-helper compatibility

- **WHEN** the native helper is unavailable before dispatch
- **THEN** Python transport and collection preserve the same output assembly contract

## MODIFIED Requirements

### Requirement: Native compact Responses preserve terminal and ownership contracts

Direct and account-routed compact requests MUST use native transport when the
helper has negotiated `http_compact_sse_v1` and `http_compact_collect_v1`. Native SSE framing MUST apply only
to successful responses selected by the outbound HTTP Content-Type rule; other
responses MUST retain raw body handling. Python MUST retain request shaping,
compact normalization, terminal error mapping, archives, routing, and settlement.
Responses MUST remain open until consumption finishes, and owned responses and
routed sessions MUST close on completion, failure, and cancellation. Missing
helpers MAY use Python transport only before dispatch. An installed helper lacking
either required compact capability MUST fail negotiation before dispatch without Python fallback. Native failures after
dispatch MUST NOT replay the POST through Python or another proxy endpoint.

#### Scenario: Compact completes before HTTP EOF

- **WHEN** response.completed follows compact output items while upstream keeps the body open
- **THEN** compact returns the existing normalized payload without waiting for EOF
- **AND** the owned transport request closes while unrelated helper requests stay usable

#### Scenario: Terminal and framing failures

- **WHEN** compact receives a terminal SSE error or exceeds its event/idle/total limit
- **THEN** existing compact public error codes, status mapping, and replay safety are preserved
- **AND** the response and owned client are cleaned up

#### Scenario: Routed fallback before dispatch

- **WHEN** a confirmed pre-dispatch connection failure permits the next configured endpoint
- **THEN** native SSE options follow that attempt and its exact route metadata is recorded
- **AND** an accepted response or ambiguous body failure never triggers endpoint replay

#### Scenario: Missing helper and cancelled caller

- **WHEN** the helper is missing before dispatch
- **THEN** the resolved Python transport receives no native-only option and retains compact parsing
- **AND** cancellation finishes owned response/session cleanup even in an already cancelled scope

#### Scenario: Cancellation while waiting for native response headers

- **WHEN** an already cancelled caller scope interrupts native response-head waiting
- **THEN** request cancellation completes and its stream registration is removed
- **AND** a completed native exchange wins a simultaneous shutdown/cancel race without an additional terminal event
