## ADDED Requirements

### Requirement: Direct WebSocket capability intent is trusted and private

A direct Responses WebSocket MUST recognize the exact internal marker
`X-Codex-LB-Required-Capability: trusted_cyber` only after successful existing
proxy API-key authentication. It MUST accept one marker from either the
handshake headers or the current `response.create.client_metadata`. Duplicate,
conflicting, non-string, unknown, malformed, or unauthenticated signals MUST
fail before account selection. Raw duplicate JSON keys or duplicate
`client_metadata` containers MUST NOT collapse into an ordinary request. The
marker MUST be rejected on every downstream frame type other than
`response.create`.

The proxy MUST remove the capability header and the exact consumed metadata
key before upstream dispatch, request archival, diagnostics, and logging.
Unrelated client metadata MUST remain unchanged.

#### Scenario: Per-frame intent routes before upstream open
- **WHEN** an authenticated frame carries the exact metadata marker on a
  downstream socket opened without the header
- **THEN** the proxy establishes REQUIRED before opening or reusing an upstream
  socket

#### Scenario: Ambiguous or untrusted signal fails closed
- **WHEN** a signal is duplicated, malformed, unknown, or lacks an authenticated
  proxy API-key principal
- **THEN** the proxy returns a typed error before account or model-source
  dispatch

#### Scenario: Duplicate JSON cannot erase intent
- **WHEN** raw JSON repeats the capability key or repeats `client_metadata`
  around a capability marker
- **THEN** the proxy returns the typed unsupported-capability error before
  selection

#### Scenario: Capability metadata on another frame is rejected
- **WHEN** a downstream frame other than `response.create` contains the
  capability metadata key
- **THEN** the proxy returns a typed error without forwarding or archiving that
  frame upstream
- **AND** malformed JSON text is rejected rather than passed through an already
  open upstream socket
- **AND** binary downstream frames are rejected before parsing, archiving, or
  upstream forwarding

#### Scenario: Internal metadata is not forwarded or archived
- **WHEN** a valid capability-bearing frame is dispatched and archived
- **THEN** neither capability carrier appears in upstream headers, upstream
  payload, archive payload, diagnostics, or logs

### Requirement: A late capability cannot reuse an ordinary upstream socket

A later REQUIRED frame MUST NOT reuse an upstream socket selected for an
ordinary request on the same downstream WebSocket. An idle ordinary socket
MUST be retired before capable
reselection. If another frame is still pending, the proxy MUST fail closed
rather than change the account requirement beneath in-flight work. The socket's
selection contract, not whether its account happened to have the capability
grant, MUST determine whether it was selected as ordinary. Before reusing a
REQUIRED-selected socket, the proxy MUST revalidate the pinned account and its
current capability grant through the canonical selector.

#### Scenario: Idle ordinary socket is replaced
- **WHEN** an idle downstream session previously selected an ordinary account
  and a later frame establishes REQUIRED
- **THEN** the ordinary upstream is retired before the frame is sent
- **AND** the replacement selection requires a security-work-authorized account

#### Scenario: Pending ordinary work blocks a requirement change
- **WHEN** ordinary work is still pending and a later frame establishes REQUIRED
- **THEN** the later frame fails before upstream send
- **AND** the pending frame's account and request state are not rewritten

#### Scenario: Revoked capability grant prevents socket reuse
- **WHEN** a socket was selected for REQUIRED but its pinned account's grant is
  no longer valid at canonical revalidation
- **THEN** the stale socket does not receive the next REQUIRED frame
- **AND** an idle socket is retired before constrained reselection

#### Scenario: Revalidation uncertainty fails closed
- **WHEN** canonical account revalidation cannot complete for a REQUIRED socket
- **THEN** the frame receives a typed capability-routing-unavailable error
- **AND** its reservation is settled without forwarding the frame upstream
