## ADDED Requirements

### Requirement: Pre-visible HTTP authentication rejection permits verified replay

The Responses streaming retry path MUST distinguish an upstream HTTP 401
rejection before visible output from an ambiguous failure after dispatch. If
forced refresh fails permanently or a refreshed credential is rejected with
HTTP 401, an otherwise unanchored full-history request MAY move to another
eligible account only after the existing account-neutral projection removes
known response-owned bookkeeping and the resulting complete wire payload
passes the shared replay-safety gate. The failed account MUST remain excluded
from the current request. Original previous-response, file, turn-state, and
single-account ownership MUST remain enforced. If no safe replay exists, the
original authentication failure MUST be preserved.

#### Scenario: Full history recovers after permanent refresh failure

- **GIVEN** a movable full-history request with encrypted reasoning bookkeeping
- **AND** account A rejects the request with HTTP 401 before visible output
- **WHEN** forced refresh fails permanently and healthy account B is available
- **THEN** the verified projected request completes on B without reselecting A
- **AND** no credentials or persisted conversation history are discarded

#### Scenario: Refreshed token still rejected

- **GIVEN** a movable full-history request rejected with HTTP 401 by A
- **WHEN** forced refresh succeeds but A again rejects with HTTP 401
- **THEN** safe verified replay may complete on B rather than selecting excluded A

#### Scenario: Hard owner and ambiguous dispatch remain protected

- **GIVEN** original hard account ownership or an unprojectable request body
- **WHEN** authentication rejection or an ambiguous transport failure occurs
- **THEN** the proxy does not dispatch the body to another account
