## MODIFIED Requirements

### Requirement: Usage refresh trusts recognized paid-plan transitions without workspace identity

Usage refresh MUST persist a stored account's `plan_type` change when
a usage payload that omits a `workspace_id` reports a recognized paid plan and
the stored plan is either `free` or another recognized paid plan (for example,
an upgrade from `free` to `plus` or from `plus` to `pro`). Because the usage
payload carries no independent account identifier and is fetched per-account
token, these transitions MUST be treated as legitimate plan changes rather than
account-slot identity mismatches. This requirement applies to scheduled usage
refresh and the forced refresh performed after an operator's Force probe.

A workspace-less usage payload that reports a recognized paid plan MUST be
trusted on its first observation. A workspace-less usage payload that reports
`free` for an account whose stored plan is a recognized paid plan MUST NOT be
applied on a single observation, and MUST be applied once a second consecutive
workspace-less refresh of the same account reports `free`, as specified in
"Usage refresh confirms a workspace-less downgrade to Free before persisting it".

A workspace-less usage payload MUST still be rejected outright, leaving the
stored plan unchanged and with no confirmation path, when it reports an
unrecognized plan that differs from the stored plan, since that remains the
signature of a degraded or wrong-identity usage response. A usage payload whose
`workspace_id` differs from the workspace the account is bound to MUST continue
to be rejected as a slot mismatch.

#### Scenario: Plus to Pro upgrade without a workspace is persisted

- **GIVEN** an active account with stored `plan_type` `plus` and no `workspace_id`
- **WHEN** background usage refresh returns a payload with `plan_type` `pro` and no `workspace_id`
- **THEN** the account's stored `plan_type` becomes `pro` and the usage sample is written

#### Scenario: Force probe persists a Free to Plus upgrade

- **GIVEN** an active account with stored `plan_type` `free` and no `workspace_id`
- **WHEN** Force probe refreshes usage and the payload reports `plan_type` `plus`
- **THEN** the account's stored `plan_type` becomes `plus` without reauthentication

#### Scenario: Single Free downgrade observation without a workspace is not applied

- **GIVEN** an active account with stored `plan_type` `business` and no `workspace_id`
- **WHEN** background usage refresh returns one payload with `plan_type` `free` and no `workspace_id`
- **THEN** the account's stored `plan_type` stays `business` and no usage mutation is applied

#### Scenario: Unrecognized workspace-less plan is rejected without confirmation

- **GIVEN** an active account with stored `plan_type` `business` and no `workspace_id`
- **WHEN** background usage refresh repeatedly returns payloads with an unrecognized `plan_type` and no `workspace_id`
- **THEN** the account's stored `plan_type` stays `business` for every observation and no usage mutation is applied

#### Scenario: Conflicting workspace identity is rejected

- **GIVEN** an active account bound to `workspace_id` `ws_team`
- **WHEN** background usage refresh returns a payload whose `workspace_id` is `ws_other`
- **THEN** the account is left unchanged and no usage mutation is applied

## ADDED Requirements

### Requirement: Usage refresh confirms a workspace-less downgrade to Free before persisting it

Usage refresh MUST persist a stored account's transition from a recognized paid
plan to `free` for a workspace-less account once two consecutive workspace-less
usage refreshes of that account report `free`. Because each usage payload is
fetched with that account's own token, two consecutive agreeing observations
distinguish a real subscription expiry from the single degraded or
wrong-identity response the workspace-less plan guard defends against.

The first such observation MUST NOT mutate the stored plan and MUST NOT write
the usage sample; it MUST only record that a downgrade is pending for that
account. The pending downgrade MUST be discarded as soon as a subsequent
workspace-less refresh of that account reports a recognized paid plan, so a
transient `free` response never accumulates toward a downgrade. Confirmation
MUST be tracked per account and MUST NOT be shared between accounts.

Confirmation applies only to `free`. An unrecognized plan value MUST NOT be
confirmable, and a payload whose `workspace_id` conflicts with the account's
bound workspace MUST remain rejected regardless of repetition.

Confirmation applies only to accounts that are not bound to a workspace. When
the stored account has a `workspace_id`, a usage payload that omits
`workspace_id` cannot establish that it describes that account's slot, so such a
payload MUST NOT downgrade the account's plan regardless of repetition.

This requirement applies to scheduled usage refresh and to the forced refresh
performed after an operator's Force probe. The confirmation threshold MUST work
with zero configuration and MUST NOT require an operator setting.

#### Scenario: Second consecutive Free observation persists the downgrade

- **GIVEN** an active account with stored `plan_type` `plus` and no `workspace_id`
- **WHEN** background usage refresh returns a payload with `plan_type` `free` and no `workspace_id`
- **AND** a second background usage refresh returns another payload with `plan_type` `free` and no `workspace_id`
- **THEN** the account's stored `plan_type` becomes `free` and the usage sample from the confirming refresh is written

#### Scenario: Intervening paid payload clears the pending downgrade

- **GIVEN** an active account with stored `plan_type` `plus` and no `workspace_id`
- **AND** one workspace-less refresh has reported `plan_type` `free`
- **WHEN** the next workspace-less refresh reports `plan_type` `plus`
- **AND** a later workspace-less refresh reports `plan_type` `free` again
- **THEN** the stored `plan_type` remains `plus` after that later single `free` observation

#### Scenario: Force probe confirms a downgrade on its second observation

- **GIVEN** an active account with stored `plan_type` `pro` and no `workspace_id`
- **WHEN** an operator runs Force probe twice and both refreshes report `plan_type` `free` with no `workspace_id`
- **THEN** the account's stored `plan_type` becomes `free` without reauthentication

#### Scenario: Workspace-bound account is never downgraded by a workspace-less payload

- **GIVEN** an active account bound to `workspace_id` `ws_team` with stored `plan_type` `business`
- **WHEN** repeated usage refreshes return payloads with `plan_type` `free` and no `workspace_id`
- **THEN** the account's stored `plan_type` stays `business` for every observation and no usage mutation is applied

#### Scenario: Confirmation is tracked per account

- **GIVEN** two active workspace-less accounts with stored `plan_type` `plus`
- **WHEN** each account receives exactly one workspace-less refresh reporting `plan_type` `free`
- **THEN** both accounts keep stored `plan_type` `plus`
