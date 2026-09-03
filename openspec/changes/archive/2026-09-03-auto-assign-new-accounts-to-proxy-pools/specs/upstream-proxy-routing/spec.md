## ADDED Requirements

### Requirement: New accounts receive a balanced proxy-pool binding

When an `auth.json` import or untargeted OAuth completion creates a new local
account row, the system MUST create an active binding to an active proxy pool
that has at least one active member backed by an active endpoint, when such a
pool exists. The selected pool MUST have the fewest active account bindings at
selection time, with a stable deterministic tie-break. The account and binding
MUST become durable in the same transaction, and upstream-route caches MUST be
invalidated after commit.

Automatic assignment MUST be independent of the global upstream-proxy routing
toggle because an explicit account binding is itself the routing contract. The
system MUST NOT replace or reactivate an existing binding when an existing
account row is re-imported or reauthenticated. If no structurally usable pool
exists, account creation MUST retain the existing unbound import or OAuth
behavior.

#### Scenario: Imported account is bound before usage refresh

- **GIVEN** at least one active proxy pool has an active member and endpoint
- **WHEN** a valid `auth.json` import creates a new local account row
- **THEN** the account and an active binding to a least-loaded pool are committed atomically
- **AND** any import-time usage refresh resolves the new account-bound route

#### Scenario: New OAuth account receives an initial binding

- **GIVEN** at least one active proxy pool has an active member and endpoint
- **WHEN** an untargeted OAuth completion creates a new local account row
- **THEN** the account receives an active binding to a least-loaded pool
- **AND** the upstream-route cache is invalidated after the binding commits

#### Scenario: Sequential account additions remain balanced

- **GIVEN** multiple structurally usable pools
- **WHEN** new accounts are created one after another
- **THEN** each assignment selects a pool with the fewest active account bindings at that selection
- **AND** equal-load ties are resolved in a stable deterministic order

#### Scenario: Existing account binding is preserved

- **GIVEN** an existing account has an active or inactive proxy-pool binding
- **WHEN** that account is re-imported or targeted for reauthentication
- **THEN** automatic assignment does not replace the binding
- **AND** it does not change the binding's active state

#### Scenario: No usable pool preserves existing account creation behavior

- **GIVEN** no active proxy pool has both an active member and active endpoint
- **WHEN** an import or OAuth completion creates a new local account row
- **THEN** no automatic binding is created
- **AND** the existing unbound success or fail-closed behavior applies
