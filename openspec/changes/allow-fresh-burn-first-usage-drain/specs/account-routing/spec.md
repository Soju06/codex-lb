## ADDED Requirements

### Requirement: Fresh requests may exhaust usage-draining burn-first accounts

For a request with no existing account owner, including a fresh soft-sticky key with no mapping, the load balancer SHALL prefer a `burn_first` account that is `DRAINING` solely because of quota usage when that account remains selectable, at least one separate healthy fallback account is selectable for the same request, and the routing strategy already honors `burn_first` policy. The load balancer MUST NOT apply this exception to an account with current error-based drain evidence, to a request with an existing soft-sticky owner, to a request with a hard-continuity owner, or to explicit sequential/reset/single-account routing. Recovery-probe admission SHALL retain precedence over this exception.

#### Scenario: Fresh request consumes remaining burn-first quota

- **GIVEN** a fresh request with no existing account assignment has a selectable `burn_first` account whose only current drain cause is quota usage
- **AND** a separate healthy fallback account is selectable for the same request
- **WHEN** account selection occurs before the `burn_first` account reaches 100% usage
- **THEN** the load balancer selects the usage-draining `burn_first` account

#### Scenario: Error-draining burn-first account remains excluded

- **GIVEN** a `burn_first` account has current error-based drain evidence
- **WHEN** a fresh request with no existing account assignment selects an account
- **THEN** the load balancer does not admit that account through the usage-drain exception

#### Scenario: Missing fallback prevents zero-drain admission

- **GIVEN** a `burn_first` account is draining solely because of quota usage
- **AND** no separate healthy account is selectable for the same request
- **WHEN** a fresh request with no existing account assignment selects an account
- **THEN** the load balancer does not admit that account through the usage-drain exception

#### Scenario: Existing soft-sticky owner remains selected

- **GIVEN** a request has a selectable soft-sticky owner
- **AND** another `burn_first` account is draining solely because of quota usage
- **WHEN** sticky selection occurs
- **THEN** the existing soft-sticky owner remains selected
- **AND** its mapping is not rebound by the usage-drain exception

#### Scenario: Hard-continuity owner remains selected

- **GIVEN** a request has a required hard-continuity owner
- **AND** another `burn_first` account is draining solely because of quota usage
- **WHEN** account selection occurs
- **THEN** the required owner remains the only eligible owner

#### Scenario: Zero-percent transition contract is unchanged

- **GIVEN** member-auth automatic transition requires two successful 0% usage observations
- **WHEN** fresh requests exhaust a usage-draining `burn_first` account
- **THEN** the automatic transition eligibility condition remains two successful 0% observations
