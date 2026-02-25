## ADDED Requirements

### Requirement: Dashboard user accounts and default admin bootstrap

The system SHALL persist dashboard login accounts with explicit role assignment (`admin`, `user`). On startup, when no admin user exists, the system MUST create a default admin account with username `admin`, role `admin`, and a bootstrap password from `CODEX_LB_BOOTSTRAP_ADMIN_PASSWORD`.

#### Scenario: Default admin bootstrap on empty database

- **WHEN** the application starts with no dashboard users present
- **THEN** the system creates user `admin` with role `admin`
- **AND** stores only a bcrypt hash of `CODEX_LB_BOOTSTRAP_ADMIN_PASSWORD`

### Requirement: Username/password authentication

The system SHALL authenticate dashboard sessions using username and password via `POST /api/dashboard-auth/password/login`.

#### Scenario: Successful username/password login

- **WHEN** a request provides valid `username` and `password`
- **THEN** the system issues a session cookie and returns an authenticated session payload
- **AND** includes the authenticated user principal (`id`, `username`, `role`)

#### Scenario: Invalid credentials

- **WHEN** username does not exist, password is invalid, or user is inactive
- **THEN** the system returns `401` with dashboard auth error code `invalid_credentials`

### Requirement: Session principal contract

`GET /api/dashboard-auth/session` SHALL return current session auth state and the authenticated principal when present.

#### Scenario: Authenticated session includes principal

- **WHEN** a valid user session exists
- **THEN** response includes `authenticated: true`
- **AND** includes `user.id`, `user.username`, and `user.role`

### Requirement: Role-based access control (RBAC)

Dashboard API authorization SHALL enforce:

- `admin`: full access across users and management features.
- `user`: access only to resources owned by the authenticated user and own account settings.

#### Scenario: User cannot access admin-only user management

- **WHEN** a `user` role calls admin-only user/group management endpoint
- **THEN** the system returns `403`

#### Scenario: User cannot access another owner's resource

- **WHEN** a `user` role requests another owner's account/detail endpoint
- **THEN** the system returns `404` (object not visible to caller)

### Requirement: Admin user management endpoints

The system SHALL provide admin-only user/group management endpoints for listing, creating, updating role, and deleting/deactivating dashboard users.

#### Scenario: Admin creates a user account

- **WHEN** admin submits a valid create request with `username`, `password`, and role
- **THEN** the user is created and returned with role metadata

#### Scenario: Admin updates a user's role

- **WHEN** admin updates role from `user` to `admin` (or vice versa)
- **THEN** the updated role is persisted and reflected in subsequent session authorization checks
