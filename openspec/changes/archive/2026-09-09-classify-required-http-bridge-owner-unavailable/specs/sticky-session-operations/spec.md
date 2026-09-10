## ADDED Requirements

### Requirement: Required continuity-owner selection failures are explicit

Account selection MUST distinguish a proven request continuity owner from an ordinary preferred account or configured routing restriction. Durable continuity provenance, including a soft prompt-cache follow-up whose durable record contains a latest turn state, MUST make the preferred account mandatory for local reuse and fresh selection. When a required continuity owner cannot be returned because that owner no longer exists or is unavailable after supported selection reloads, selection MUST return `continuity_owner_unavailable`. When the owner exists but is outside the effective model, API-key, security, authorization, or routing policy, selection MUST return `continuity_owner_policy_conflict` or the more specific existing policy code. Stable local capacity codes MUST remain unchanged. The HTTP bridge MUST translate only a typed `continuity_owner_unavailable` selection result for its required owner to `previous_response_owner_unavailable`.

#### Scenario: Required owner is unavailable while another account is healthy

- **GIVEN** a follow-up has proven account A as its continuity owner
- **AND** account B remains available for unrelated traffic
- **WHEN** account A cannot be selected after supported selection reloads
- **THEN** selection returns `continuity_owner_unavailable`
- **AND** the HTTP bridge returns `previous_response_owner_unavailable` unless verified replay applies
- **AND** it does not select account B as the unchanged continuation owner

#### Scenario: Required owner conflicts with selection policy

- **GIVEN** a follow-up has proven account A as its continuity owner
- **WHEN** account A is outside the request's model, API-key, security, authorization, or routing policy
- **THEN** selection returns `continuity_owner_policy_conflict` or the existing more-specific policy code
- **AND** the bridge does not convert that result into replay permission

#### Scenario: Soft prompt-cache follow-up has durable continuity provenance

- **GIVEN** a soft prompt-cache follow-up resolves durable account A and a latest turn state
- **AND** a stale local lane for the same prompt-cache key uses account B
- **WHEN** the bridge reuses or creates the follow-up lane
- **THEN** it rejects the stale account-B lane and requires account A
- **AND** account selection cannot fall back to B before verified replay proof applies

#### Scenario: Soft prompt-cache first turn changes model

- **GIVEN** a first-turn request reuses a soft prompt-cache key whose durable row was written for another model
- **AND** the request carries no previous-response, turn-state, or session-header continuation
- **WHEN** the bridge isolates the incompatible durable lane
- **THEN** it does not make the stale durable account a required continuity owner
- **AND** ordinary first-turn account selection remains available

#### Scenario: Required owner reaches a local capacity cap

- **WHEN** a required owner reaches `account_stream_cap` or `account_response_create_cap`
- **THEN** selection preserves that code and its `429` classification
- **AND** bounded local-cap recovery remains available

#### Scenario: Failure occurs after the owner was selected

- **GIVEN** the required continuity owner was selected successfully
- **WHEN** token refresh, authentication, connection, timeout, or another transport step fails
- **THEN** the failure retains its ordinary authentication or upstream classification
- **AND** it is not rewritten to `previous_response_owner_unavailable`

### Requirement: Restricted ownership misses do not change global health

A terminal no-account result MUST change global degraded state only when the attempted selection represents the effective global routing pool. A request explicitly constrained by continuity ownership, or a resolved hard sticky owner, MUST leave the existing global health state unchanged when that restricted owner is unavailable; the restricted miss MUST neither enter nor clear degraded mode. A configured single-account routing strategy without request ownership provenance remains an effective global routing policy and MUST retain the existing degraded-state behavior when its configured pool is unavailable.

#### Scenario: Explicit owner restriction misses

- **GIVEN** a request is explicitly constrained to account A by continuity ownership
- **AND** another account remains healthy in the wider pool
- **WHEN** account A is unavailable
- **THEN** the request receives its restricted-owner failure
- **AND** the service does not enter global degraded mode

#### Scenario: Resolved hard sticky owner misses

- **GIVEN** a hard sticky mapping resolves account A as the request owner
- **WHEN** account A is unavailable
- **THEN** the request fails closed without rerouting
- **AND** the restricted miss does not mark the wider process globally degraded

#### Scenario: Configured single-account pool is unavailable

- **GIVEN** the operator configured single-account routing without a request ownership constraint
- **WHEN** that effective routing pool has no available account
- **THEN** the service retains its existing global degraded-mode behavior

#### Scenario: Unrestricted pool is unavailable

- **WHEN** an unrestricted selection finds no available upstream account
- **THEN** the service enters degraded mode and retains the existing global exhaustion envelope
