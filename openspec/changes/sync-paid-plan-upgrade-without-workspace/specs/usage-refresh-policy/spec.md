## ADDED Requirements

### Requirement: Usage refresh trusts recognized paid-plan transitions without workspace identity

Usage refresh MUST persist a stored account's `plan_type` change when
a usage payload that omits a `workspace_id` reports a recognized paid plan and
the stored plan is either `free` or another recognized paid plan (for example,
an upgrade from `free` to `plus` or from `plus` to `pro`). Because the usage
payload carries no independent account identifier and is fetched per-account
token, these transitions MUST be treated as legitimate plan changes rather than
account-slot identity mismatches. This requirement applies to scheduled usage
refresh and the forced refresh performed after an operator's Force probe.

A workspace-less usage payload MUST still be rejected, leaving the stored plan
unchanged, when it reports `free` or an unrecognized plan that differs from the
stored plan, since that is the signature of a degraded or wrong-identity usage
response. A usage payload whose `workspace_id` differs from the workspace the
account is bound to MUST continue to be rejected as a slot mismatch.

#### Scenario: Plus to Pro upgrade without a workspace is persisted

- **GIVEN** an active account with stored `plan_type` `plus` and no `workspace_id`
- **WHEN** background usage refresh returns a payload with `plan_type` `pro` and no `workspace_id`
- **THEN** the account's stored `plan_type` becomes `pro` and the usage sample is written

#### Scenario: Force probe persists a Free to Plus upgrade

- **GIVEN** an active account with stored `plan_type` `free` and no `workspace_id`
- **WHEN** Force probe refreshes usage and the payload reports `plan_type` `plus`
- **THEN** the account's stored `plan_type` becomes `plus` without reauthentication

#### Scenario: Free downgrade without a workspace is rejected

- **GIVEN** an active account with stored `plan_type` `business` and no `workspace_id`
- **WHEN** background usage refresh returns a payload with `plan_type` `free` and no `workspace_id`
- **THEN** the account's stored `plan_type` stays `business` and no usage mutation is applied

#### Scenario: Conflicting workspace identity is rejected

- **GIVEN** an active account bound to `workspace_id` `ws_team`
- **WHEN** background usage refresh returns a payload whose `workspace_id` is `ws_other`
- **THEN** the account is left unchanged and no usage mutation is applied
