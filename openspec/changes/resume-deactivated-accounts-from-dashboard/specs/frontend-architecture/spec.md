## ADDED Requirements

### Requirement: Deactivated accounts expose the reactivate action

The dashboard MUST offer the account reactivate action for accounts whose status is
`deactivated`, on every surface that already offers it for `paused` accounts. Those
accounts MUST also retain their re-authentication action. The dashboard MUST NOT
offer the reactivate action for accounts whose status is `reauth_required`, because
the reactivate endpoint refuses that transition.

#### Scenario: Operator resumes a deactivated account

- **WHEN** an operator views an account whose status is `deactivated`
- **THEN** a resume control is available and invokes the reactivate request for that
  account
- **AND** the re-authentication control remains available

#### Scenario: Re-auth required accounts withhold the reactivate action

- **WHEN** an operator views an account whose status is `reauth_required`
- **THEN** no resume control is offered
- **AND** the re-authentication control is offered

#### Scenario: Paused accounts are unchanged

- **WHEN** an operator views an account whose status is `paused`
- **THEN** the resume control behaves exactly as before this change
