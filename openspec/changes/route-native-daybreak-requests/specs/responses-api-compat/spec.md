## ADDED Requirements

### Requirement: Automatic Daybreak request recognition
Enabled keys MUST recognize legacy/enforced `gpt-daybreak-blue-latest` and native `access_programs.cyber = "daybreak_blue"` across Responses WebSocket, HTTP Responses and compact, including aliases/slashes. Requests/retries MUST preserve native fields, scope, ownership and lineage. Required HTTP traffic MUST use direct subscriptions without external-source/bridge fallback. Disabled keys and unrelated WebSocket control frames MUST keep existing behavior.

#### Scenario: One key serves ordinary and Daybreak work
- **WHEN** an enabled key sends a legacy, enforced-model or native Blue request
- **THEN** selection and retries require Trusted Cyber eligibility and forward native fields unchanged
- **AND** ordinary requests without an inherited restriction retain normal routing

#### Scenario: Invalid selectors fail before dispatch
- **WHEN** an enabled key sends malformed, duplicate, conflicting or unsupported selectors
- **THEN** the proxy rejects the request before dispatch
- **AND** explicit standard conflicts with legacy or custom required intent; Red does not imply Blue

#### Scenario: Safe replay remains constrained
- **WHEN** a request contains a supported selector and otherwise account-neutral data
- **THEN** it can retry another eligible account
- **AND** account-owned continuation data still prevents cross-account replay

## MODIFIED Requirements

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

Required-to-ordinary transitions MUST obey the same idle-retirement and pending-work rules. Both directions MUST preserve explicit ownership and opaque replay constraints. Inferred response anchors MUST match the incoming conversation; matching input alone MUST NOT establish continuity.

#### Scenario: A separate ordinary conversation follows DBB work
- **WHEN** a separate ordinary frame follows a DBB frame, even with a matching list input prefix
- **THEN** an idle upstream is retired before normal selection, without transferring synthesized turn state or an inferred DBB response anchor
- **AND** pending work instead causes the new frame to fail without changing the active socket
