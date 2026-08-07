## ADDED Requirements

### Requirement: Fresh requests may exhaust usage-draining burn-first accounts

For an owner-free request, including a fresh soft-sticky key with no mapping, the load balancer SHALL include a `burn_first` account that is `DRAINING` solely because of quota usage in the authoritative routing selection when that account remains selectable, at least one separate healthy fallback account is selectable for the same request, and the routing strategy already honors `burn_first` policy. The load balancer MUST NOT apply this exception to an account with current error-based drain evidence, to a request with an existing soft-sticky owner, to a request with a hard-continuity owner, to a request carrying an unresolved ownership requirement, or to explicit sequential/reset/single-account routing. Recovery-probe admission SHALL retain precedence over this exception. Eligibility probing MUST NOT consume or log a weighted routing winner, and the load balancer SHALL return the original account state corresponding to the one authoritative routing winner.

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
- **AND** the original candidate pool proceeds through the pre-existing routing path unchanged

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

#### Scenario: Unresolved owner-bearing request is not fresh routing

- **GIVEN** a request requires an unambiguous account owner but no persisted owner has resolved yet
- **WHEN** unbound or fresh soft-sticky selection occurs
- **THEN** the load balancer does not enable the usage-drain exception
- **AND** existing continuity resolution remains authoritative

#### Scenario: Weighted routing winner is returned without redraw

- **GIVEN** a usage-only draining `burn_first` candidate and separately selectable healthy fallbacks
- **WHEN** a weighted routing strategy performs selection
- **THEN** fallback eligibility checking does not consume or log a weighted winner
- **AND** the account returned is the original state corresponding to the single weighted winner

#### Scenario: Existing last-resort and opportunistic policies remain authoritative

- **GIVEN** a usage-only draining `burn_first` account
- **AND** no separate healthy fallback is selectable
- **WHEN** account selection occurs
- **THEN** the load balancer preserves the original candidate pool
- **AND** the existing last-resort or opportunistic emergency-floor policy decides whether the account is eligible

#### Scenario: Zero-percent transition contract is unchanged

- **GIVEN** member-auth automatic transition requires two successful 0% usage observations
- **WHEN** fresh requests exhaust a usage-draining `burn_first` account
- **THEN** the automatic transition eligibility condition remains two successful 0% observations
