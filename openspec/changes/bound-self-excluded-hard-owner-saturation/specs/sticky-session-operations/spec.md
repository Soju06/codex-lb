# sticky-session-operations Delta

## ADDED Requirements

### Requirement: A hard-affinity saturation caused by the caller's own exclusion is not waited on

When account selection fails with `hard_affinity_saturated`, the selector MUST
report whether the resolved hard `CODEX_SESSION` owner is one of the account
ids the caller asked to exclude from that selection. The report MUST be true
only for that combination: a saturation whose owner the caller did not exclude,
and any other selection outcome, MUST NOT be reported as caller-excluded.

A hard row is ownership evidence, so selection narrows to its owner and never
spills to another account, and the exclusion filter removes the owner from the
candidate pool before that narrowing. A caller that excluded the owner
therefore cannot be served by any re-selection while its exclusion holds. When
the selector reports that cause, the caller MUST NOT take the short
owner-recovery wait that a briefly unavailable owner earns, and MUST fail the
request closed on that first saturated selection instead of re-selecting until
its request budget is spent. The failure's error code, HTTP status and payload
MUST be unchanged from the same failure surfaced after the wait.

The rule MUST hold for every transport that waits on selection recovery — the
HTTP responses session bridge (session creation and reconnect), the direct
WebSocket surface, and the SSE retry path — so no surface keeps spinning after
another stops. It MUST NOT change any selection decision: the exclusion stands,
the sticky row is neither rebound nor deleted, no alternate account is served
for a resolved hard owner, and an owner the caller did not exclude keeps both
its recovery wait and its retry.

#### Scenario: Raw legacy hard owner excluded by the requesting replay

- **GIVEN** a native Codex session whose bare session header still resolves a raw legacy `CODEX_SESSION` row naming account A as the hard owner, and no namespaced row
- **AND** a healthy alternate account B is selectable
- **WHEN** selection runs with that affinity and `exclude_account_ids` containing A
- **THEN** selection fails with `hard_affinity_saturated` and never returns B
- **AND** the failure reports that the resolved hard owner is one of the caller's own exclusions
- **AND** the sticky row still names A, unrewritten and undeleted

#### Scenario: Bridge replay that excluded its own hard owner fails closed at once

- **GIVEN** the HTTP responses session bridge is enabled and a native Codex request carries a `session_id` header whose raw legacy row names the serving account as the hard owner
- **AND** the turn fails before `response.created` reaches the client (the created-only pre-created replay) or the account rejects the requested model (the model-fallback replay), so the replay excludes that account to move the turn
- **WHEN** the reconnect re-selects and selection reports the caller-excluded hard owner
- **THEN** the reconnect fails the request closed on that first re-selection without waiting for the owner to recover
- **AND** the turn is not sent to another account, and the owner is not reconnected behind the client's back
- **AND** the client observes the resulting failure immediately instead of keepalives until the bridge request budget

#### Scenario: Unavailable owner the caller did not exclude keeps its recovery wait

- **GIVEN** the same session whose raw legacy row names account A as the hard owner
- **AND** A is temporarily unselectable through a status or health transition while the caller excludes nothing
- **WHEN** selection fails with `hard_affinity_saturated`
- **THEN** the failure is not reported as caller-excluded
- **AND** the caller takes its short owner-recovery wait and re-selects, so the owner may serve the turn once it recovers
