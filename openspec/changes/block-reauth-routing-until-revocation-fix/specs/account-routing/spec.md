## ADDED Requirements

### Requirement: Temporary re-authentication routing quarantine

Until access-token revocation can be represented independently from refresh-token eligibility, the proxy MUST treat every `reauth_required` account as unavailable for new account selection and live HTTP bridge reuse regardless of the stored access token's nominal expiry. The proxy MUST NOT send new upstream I/O through such an account. Movable soft affinity MUST remain eligible to select an active replacement, while hard account-owned continuity MUST fail closed rather than cross accounts.

#### Scenario: Unexpired warning account is excluded

- **GIVEN** account A is `reauth_required` and its stored access token has a future expiry
- **AND** account B is active
- **WHEN** an ordinary proxy request selects an account
- **THEN** account B is selected
- **AND** no upstream request is sent through account A

#### Scenario: Warning-state bridge is not reused

- **GIVEN** a live HTTP bridge is authenticated to account A
- **AND** account A is `reauth_required`
- **WHEN** a later request considers that bridge for reuse
- **THEN** the bridge is not reused for new upstream I/O
- **AND** normal replacement or fail-closed ownership behavior applies

#### Scenario: Warning-state hard owner remains fail-closed

- **GIVEN** hard account-owned continuity resolves to account A
- **AND** account A is `reauth_required`
- **WHEN** the request is routed
- **THEN** the request fails without selecting another account
- **AND** the ownership mapping is not rebound

#### Scenario: Warning-only pool reports re-authentication

- **GIVEN** every otherwise scoped proxy candidate is `reauth_required`
- **WHEN** account selection runs
- **THEN** selection returns an explicit all-accounts-require-reauthentication error
