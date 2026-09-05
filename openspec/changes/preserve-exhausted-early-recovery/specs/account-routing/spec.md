## ADDED Requirements

### Requirement: Early recovery requires available quota evidence

Fresh post-block usage MUST NOT clear an unexpired upstream rate-limit block when
an applicable known quota window remains exhausted. The marking replica SHALL
retain the existing reset deadline and block marker until ordinary expiry or
valid available-quota recovery evidence. This SHALL NOT change active-account
advisory usage routing or existing credit-backed recovery behavior.

#### Scenario: Fresh primary availability cannot conceal weekly exhaustion

- **GIVEN** a sticky account was blocked by upstream `usage_limit_reached`
- **AND** the local retry backoff elapsed but the upstream reset is in the future
- **WHEN** a post-block refresh reports primary usage below 100% and weekly usage at 100%
- **THEN** early recovery does not clear the account's block
- **AND** a replay-safe request can use another eligible account without changing its model or reasoning effort

#### Scenario: Fresh exhausted primary usage is not recovery evidence

- **GIVEN** an account has an unexpired upstream rate-limit block
- **WHEN** a post-block refresh still reports primary usage at 100%
- **THEN** freshness alone does not reactivate the account

#### Scenario: Available windows still permit existing early recovery

- **GIVEN** the marking replica's retry backoff has elapsed
- **WHEN** all applicable known windows have available quota and the required post-block sample is fresh
- **THEN** the existing early-recovery path remains available

#### Scenario: Non-applicable primary rows do not prevent recovery

- **GIVEN** a plan has no primary-window capacity and retains a synthetic primary usage row at 100%
- **AND** the marking replica's retry backoff has elapsed
- **WHEN** fresh post-block usage shows available quota in the applicable long window
- **THEN** the non-applicable primary row SHALL NOT prevent the existing early-recovery path
