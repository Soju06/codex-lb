## MODIFIED Requirements

### Requirement: Completed operator probe recovery

The existing account probe endpoint MUST clear a persisted rate-limit or quota hold only after a valid `response.completed` for the held account and recorded rejected model and service tier. HTTP success without completed execution MUST NOT authorize recovery. Unknown historical rejection scope MUST NOT authorize recovery, including when a default probe model succeeds.

Every persisted rejection MUST advance a durable monotonic block generation, including repeated rejections with identical integer-second block timestamps. Recovery MUST compare the generation captured before dispatch, account status, block markers and credential identity. A newer rejection, pause, deletion or reauthentication MUST win. Concurrent probes for the same hold MUST share a bounded admission. Probe failure, timeout, cancellation, malformed stream and incomplete execution MUST NOT authorize completed-probe recovery. Existing ordinary usage and deadline recovery remain independent.

Recovery MUST preserve routing policy, model eligibility, file ownership, registered continuation ownership and existing account affinity. Ordinary quota refresh and deadline recovery rules MUST remain unchanged. No automatic provider probe SHALL be introduced.

#### Scenario: Successful recovery after restart

- **GIVEN** a held account with recorded rejected scope and no replica-local block state
- **WHEN** an operator probe completes successfully for that same scope and its generation remains current
- **THEN** the persisted hold is cleared and fresh eligible requests can select the account
- **AND** established owner bindings remain unchanged

#### Scenario: Same-second rejection wins

- **GIVEN** an operator probe captured a hold generation
- **WHEN** a newer upstream rejection persists with the same reset and block timestamps before probe settlement
- **THEN** its advanced generation prevents recovery

#### Scenario: Historical hold remains protected

- **GIVEN** a hold migrated without trustworthy rejected scope
- **WHEN** a probe completes for any model
- **THEN** completed-probe recovery does not clear that hold

#### Scenario: Incomplete provider response

- **GIVEN** a held account whose scope matches the probe
- **WHEN** upstream returns HTTP 200 followed by failure, incomplete execution or EOF without completion
- **THEN** the persisted hold remains unchanged

#### Scenario: Transient error arrives before recovered replica selection
- **GIVEN** a replica retains an older hold and the persisted account has a newer recovered generation
- **WHEN** a transient error is recorded before that replica next selects an account
- **THEN** the recovered hold MUST NOT be restored or retained
- **AND** ordinary transient-error accounting MUST still apply

#### Scenario: Shared WebSocket finalization preserves selected rejection scope
- **GIVEN** pending WebSocket requests with different models or service tiers
- **WHEN** a request-specific error identifies the request selected for a persisted rejection
- **THEN** the rejection MUST retain that selected request's model and service tier
- **AND** reservation settlement and terminal logging MUST precede the health write

#### Scenario: Matching probe recovers a rejected WebSocket handshake
- **GIVEN** a WebSocket handshake rejects a known requested model and service tier with an account rate-limit or quota error
- **WHEN** an operator probe completes for that unchanged scope and generation
- **THEN** the persisted handshake rejection SHALL be eligible for completed-probe recovery

#### Scenario: Shared failure has ambiguous request scope
- **GIVEN** a shared WebSocket failure with pending requests for different models or service tiers and no request-specific attribution
- **WHEN** terminal cleanup persists the account rejection
- **THEN** the rejection model and service tier MUST remain unknown
- **AND** a completed probe for any one pending request's scope MUST NOT clear that hold

#### Scenario: Shared failure has one common request scope
- **GIVEN** every pending request has the same model and service tier
- **WHEN** terminal cleanup persists a shared account rejection
- **THEN** the rejection SHALL retain that common scope for completed-probe recovery
