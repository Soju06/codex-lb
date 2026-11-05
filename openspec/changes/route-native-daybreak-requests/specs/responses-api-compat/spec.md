## ADDED Requirements

### Requirement: Automatic Daybreak request recognition
For an opted-in authenticated key, Responses WebSocket, HTTP Responses and compact requests MUST require Trusted Cyber accounts for `gpt-daybreak-blue-latest` or native `access_programs.cyber = "daybreak_blue"`. It MUST preserve native fields, key scope, ownership and inherited constraints. Ordinary requests MUST use normal routing. Invalid native selectors MUST fail before dispatch. Opted-out keys MUST retain existing behavior.

#### Scenario: One key supports ordinary and Daybreak requests
- **GIVEN** a key permits ordinary and Trusted Cyber accounts and enables automatic routing
- **WHEN** a native Blue frame arrives without a custom marker
- **THEN** selection requires Trusted Cyber eligibility before opening upstream
- **AND** the upstream payload retains the native program

#### Scenario: Legacy and ordinary requests
- **WHEN** the same enabled key requests the legacy Daybreak model
- **THEN** it uses the restricted eligible pool
- **AND** a generic model without a Daybreak selector uses its ordinary pool

#### Scenario: HTTP Responses and compact
- **WHEN** an enabled key sends a legacy or native Daybreak request over HTTP Responses or compact
- **THEN** initial selection and all retries require an eligible Trusted Cyber account within the key scope
- **AND** native fields and account ownership are preserved
- **AND** an empty eligible pool fails without ordinary or external model-source dispatch

#### Scenario: Safe replay retains the requirement
- **WHEN** a verified fresh replay contains only a supported native access-program selector and otherwise account-neutral data
- **THEN** the selector does not itself prevent retry to another eligible account
- **AND** owned continuation data still prevents cross-account replay

#### Scenario: Invalid native selection
- **WHEN** an opted-in key sends a malformed, duplicate, conflicting or unsupported native selector
- **THEN** the request fails before upstream dispatch
- **AND** Red is not treated as Blue
- **AND** explicit standard conflicts with a legacy or custom Daybreak marker

#### Scenario: Equivalent HTTP paths
- **WHEN** a Daybreak request uses a supported Responses alias or a trailing slash on Responses or compact
- **THEN** the same native eligibility and forwarding contract applies

#### Scenario: API-key enforced Daybreak model
- **WHEN** an enabled key enforces `gpt-daybreak-blue-latest` on a generic Responses or compact request
- **THEN** selection and retries MUST require Trusted Cyber eligibility within the key scope
- **AND** an explicit native `standard` selector MUST fail as a conflict before dispatch
- **AND** unrelated WebSocket control frames without selectors MUST retain their existing behavior

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

When a later frame belongs to a separate ordinary conversation, an idle
REQUIRED-selected socket MUST also be retired before ordinary reselection.
Pending work MUST prevent this reverse switch. Explicit account-owned anchors
and opaque replay constraints MUST remain enforced across either transition.

#### Scenario: Ordinary conversation follows a DBB frame
- **WHEN** a later ordinary frame identifies a separate conversation on an idle socket selected for REQUIRED
- **THEN** the upstream is retired before ordinary selection
- **AND** synthesized account-local turn state is not transferred

#### Scenario: Pending DBB work blocks an ordinary switch
- **WHEN** a DBB response is still pending and an ordinary frame identifies a separate conversation
- **THEN** the later frame fails before upstream send
- **AND** the active response keeps its account and socket
