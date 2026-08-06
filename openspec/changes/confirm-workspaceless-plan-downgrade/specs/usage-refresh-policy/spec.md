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

Only a recognized paid plan discards a pending downgrade. An unrecognized plan
value is absence of evidence rather than evidence that the account is still paid,
so it MUST NOT reset the pending state; otherwise a persistently degraded
upstream could prevent a real expiry from ever converging.

Plan values MUST be compared after normalization, so upstream differences in
letter case or surrounding whitespace MUST NOT change whether an observation
counts toward confirmation.

Confirmation applies only to accounts that are not bound to a workspace. When
the stored account has a `workspace_id`, a usage payload that omits
`workspace_id` cannot establish that it describes that account's slot, so such a
payload MUST NOT downgrade the account's plan regardless of repetition.

Pending observations MUST be persisted in shared storage rather than in process
memory, so that every replica operating against the same database observes and
advances the same sequence for a given account. A `free` observation recorded by
one replica MUST count toward confirmation on any other replica, and a recognized
paid plan observed by one replica MUST discard the pending evidence for all of
them. Persisted evidence MUST NOT reduce the confirmation threshold: a downgrade
is still applied only on the second agreeing observation.

Recording an observation MUST be atomic with respect to concurrent refreshes of
the same account: two refreshes observing `free` MUST advance the count twice
rather than both reading the same prior value and writing the same result.

Pending observations MUST be invalidated when the account's credentials are
replaced. Account identifiers are deterministic, so deleting and re-importing an
account, or reauthenticating it in place, reuses the identifier with new token
material; evidence gathered under the previous credential MUST NOT count toward a
downgrade for the new one, which MUST begin its own count. Evidence MUST also be
removed when the account itself is deleted.

Routine token rotation is not a credential replacement. Refresh tokens rotate on
every successful token refresh, so rotation occurring between two observations
MUST NOT reset the pending count; otherwise an account whose token-refresh
cadence interleaves with usage refresh could have a real expiry postponed
indefinitely.

Security notes: the stored evidence MUST NOT contain usable token material or
any other secret. The persisted credential fingerprint is a non-reversible
digest — an HMAC under a fixed, public, versioned salt — over the account's
stable seat-identity fields (the ChatGPT workspace and principal identifiers,
the email address, and the codex installation id), stored outside the encrypted
token columns. Deriving it involves no decryption, so encryption-key rotation,
re-encryption, or an undecryptable credential row cannot perturb it.

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

#### Scenario: A degraded payload between two Free observations does not reset confirmation

- **GIVEN** an active workspace-less account with stored `plan_type` `plus`
- **AND** one workspace-less refresh has reported `plan_type` `free`
- **WHEN** the next workspace-less refresh reports an unrecognized `plan_type`
- **AND** a later workspace-less refresh reports `plan_type` `free` again
- **THEN** the account's stored `plan_type` becomes `free`

#### Scenario: Plan casing and surrounding whitespace do not change confirmation

- **GIVEN** an active workspace-less account with stored `plan_type` `plus`
- **WHEN** two consecutive workspace-less refreshes report `plan_type` values that
  normalize to `free` but differ in letter case or surrounding whitespace
- **THEN** the first observation leaves the stored `plan_type` unchanged
- **AND** the second observation persists the downgrade to `free`

#### Scenario: Clearing one account's pending downgrade leaves another's intact

- **GIVEN** two active workspace-less accounts with stored `plan_type` `plus`
- **AND** each has recorded one workspace-less refresh reporting `plan_type` `free`
- **WHEN** the first account's next workspace-less refresh reports a recognized paid plan
- **THEN** the first account's pending downgrade is discarded
- **AND** the second account still persists its downgrade on its own next `free` observation

#### Scenario: Confirmation applies to a refresh performed with an access-token override

- **GIVEN** an active workspace-less account with stored `plan_type` `plus`
- **WHEN** two consecutive refreshes performed with an explicit access-token override
  report `plan_type` `free` with no `workspace_id`
- **THEN** the account's stored `plan_type` becomes `free`

#### Scenario: Confirmation is tracked per account

- **GIVEN** two active workspace-less accounts with stored `plan_type` `plus`
- **WHEN** each account receives exactly one workspace-less refresh reporting `plan_type` `free`
- **THEN** both accounts keep stored `plan_type` `plus`

#### Scenario: Observations split across replicas still confirm the downgrade

- **GIVEN** an active workspace-less account with stored `plan_type` `plus`
- **AND** two replicas operating against the same database
- **WHEN** one replica's workspace-less refresh reports `plan_type` `free`
- **AND** the other replica's next workspace-less refresh also reports `plan_type` `free`
- **THEN** the account's stored `plan_type` becomes `free`

#### Scenario: A paid payload on one replica clears pending evidence for all replicas

- **GIVEN** an active workspace-less account with stored `plan_type` `plus`
- **AND** one replica has recorded a workspace-less refresh reporting `plan_type` `free`
- **WHEN** another replica's workspace-less refresh reports a recognized paid plan
- **AND** the first replica's next workspace-less refresh reports `plan_type` `free` again
- **THEN** the stored `plan_type` remains `plus` after that later single `free` observation

#### Scenario: A single Force probe records durable pending evidence

- **GIVEN** an active workspace-less account with stored `plan_type` `plus`
- **WHEN** an operator runs Force probe once and the payload reports `plan_type` `free`
- **THEN** the pending observation is stored in shared storage with a count of one
- **AND** the stored evidence records the plan value observed and no usable token material
- **AND** the stored evidence is removed once the downgrade is applied

#### Scenario: A replaced credential does not inherit pending evidence

- **GIVEN** an active workspace-less account with stored `plan_type` `plus`
- **AND** one workspace-less refresh has reported `plan_type` `free`
- **WHEN** the account is re-imported or reauthenticated with new token material under the same identifier
- **AND** the next workspace-less refresh reports `plan_type` `free`
- **THEN** the stored `plan_type` remains `plus`
- **AND** the account's stored `plan_type` becomes `free` only on a further `free` observation

#### Scenario: Routine token rotation between observations does not reset confirmation

- **GIVEN** an active workspace-less account with stored `plan_type` `plus`
- **AND** one workspace-less refresh has reported `plan_type` `free`
- **WHEN** a successful token refresh rotates the account's tokens
- **AND** the next workspace-less refresh reports `plan_type` `free`
- **THEN** the account's stored `plan_type` becomes `free`

#### Scenario: Concurrent observations each advance the count

- **GIVEN** an active workspace-less account with stored `plan_type` `plus`
- **WHEN** two refreshes of that account observe `plan_type` `free` concurrently
- **THEN** the recorded observation count reflects both observations rather than one
