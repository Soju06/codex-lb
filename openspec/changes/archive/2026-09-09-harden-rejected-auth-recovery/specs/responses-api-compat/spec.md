## MODIFIED Requirements

### Requirement: Pre-visible HTTP authentication recovery preserves legal replay

When a pre-visible HTTP Responses 401 reaches account-level retry, the proxy MUST retain its existing bounded same-account forced-refresh attempt. When that attempt cannot repair authentication, a movable request MUST try another eligible account within the existing attempt and time budget. The exact replacement body MUST pass the canonical account-neutral fresh-replay predicate; known response bookkeeping MAY be projected out only when that produces a complete self-contained replacement. Installing the replacement body and clearing its transient dispatch-owner binding MUST be atomic within the retry state transition.

Independent file, previous-response, turn-state, conversation, single-account, and legacy hard-affinity ownership MUST NOT be weakened. Opaque compaction, hosted-tool results, unknown payload fields, and unresolved tool state MUST NOT be discarded to manufacture replay eligibility. Same-account successful refresh MUST retain the original body. A failure after downstream output or with ambiguous upstream execution MUST NOT authorize replay. When no legal replacement exists, the original authentication failure MUST be surfaced rather than `preferred_account_unavailable`; existing previous-response-specific error mapping MUST remain unchanged.

The rejected account's stream lease MUST be released before replacement selection. API-key reservations MUST settle before deferred account-health writes, including failure and cancellation paths. Subsequent replacement failures MUST supersede earlier authentication errors.

Each projected reasoning block MUST be followed by a complete retained assistant
answer in the same turn before any fresh user or instruction boundary. Earlier
assistant answers, recognized reasoning fields, summaries alone, empty or
incomplete assistant messages, and unresolved tool calls MUST NOT prove that
reasoning is redundant. Every affected turn MUST satisfy this proof independently.

#### Scenario: Expired access plus failed refresh recovers a full transcript

- **GIVEN** an unanchored complete text/tool transcript with response-owned IDs and reasoning bookkeeping
- **WHEN** account A returns pre-visible 401 and forced refresh fails permanently
- **THEN** the proxy validates a projected account-neutral body and completes on account B
- **AND** another independent message does not repeat A's rejected authentication

#### Scenario: Successful refresh retains the original body

- **GIVEN** account A rejects the first attempt with 401
- **WHEN** forced refresh succeeds and A accepts the retry
- **THEN** both attempts use the original input and no account switch occurs

#### Scenario: Hard ownership and opaque state fail closed

- **GIVEN** the request contains a required file or previous-response owner, turn state, opaque compaction, or unresolved tool output
- **WHEN** authentication cannot be repaired on A
- **THEN** the request is not sent to B and returns the authentication failure or its existing previous-response-specific error

#### Scenario: A post-output 401 cannot replay

- **GIVEN** A has already emitted downstream-visible output
- **WHEN** authentication subsequently fails
- **THEN** the proxy does not replay the request on another account

#### Scenario: Reasoning without a retained answer fails closed

- **GIVEN** account-owned encrypted reasoning is followed only by a fresh user message
- **WHEN** pre-visible authentication recovery cannot repair the owning account
- **THEN** the reasoning is not discarded to manufacture account-neutral replay
- **AND** no replacement request is sent to another account

#### Scenario: An earlier complete turn does not authorize an incomplete later turn

- **GIVEN** one retained turn contains reasoning and a complete assistant answer
- **AND** a later reasoning block has no complete retained answer before the next user message
- **WHEN** authentication recovery evaluates cross-account replay
- **THEN** the entire replacement is rejected as unsafe
