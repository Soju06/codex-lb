## ADDED Requirements

### Requirement: Username/password login UX

The SPA login flow SHALL require both `username` and `password` and consume the updated dashboard auth session contract with principal details.

#### Scenario: Login form submits username and password

- **WHEN** user submits login form
- **THEN** frontend sends `{ username, password }` to `POST /api/dashboard-auth/password/login`

### Requirement: Role-aware navigation and pages

The SPA SHALL adapt visible navigation/actions by authenticated role.

#### Scenario: Admin sees management controls

- **WHEN** session role is `admin`
- **THEN** admin-only user/group management UI is visible
- **AND** account/resource views support filtering by owner user

#### Scenario: User sees only own scope

- **WHEN** session role is `user`
- **THEN** UI hides admin-only management controls
- **AND** list/detail views expose only caller-owned resources

### Requirement: Account management settings scope

The settings experience SHALL separate global admin settings from self account settings.

#### Scenario: User accesses settings

- **WHEN** role is `user`
- **THEN** UI shows own account settings only
- **AND** hides global routing/system controls reserved for `admin`
