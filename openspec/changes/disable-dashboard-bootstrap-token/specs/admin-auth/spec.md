### Requirement: Remote bootstrap gate can be disabled

The system SHALL treat `CODEX_LB_DISABLE_BOOTSTRAP_TOKEN=true` as disabling the remote bootstrap-token gate for dashboard access and initial password setup. When this flag is enabled and no password is configured, remote requests SHALL follow the same dashboard bootstrap behavior as local requests without requiring `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN`.

#### Scenario: Remote first-run session bypasses bootstrap gate when disabled

- **WHEN** `password_hash` is NULL, `totp_required_on_login` is false, `CODEX_LB_DISABLE_BOOTSTRAP_TOKEN` is true, and the session request comes from a non-local client
- **THEN** `GET /api/dashboard-auth/session` does not report `bootstrapRequired: true`
- **AND** the response remains usable for dashboard bootstrap without a bootstrap token

#### Scenario: Remote password setup works without bootstrap token when disabled

- **WHEN** `password_hash` is NULL, `CODEX_LB_DISABLE_BOOTSTRAP_TOKEN` is true, and a non-local client calls `POST /api/dashboard-auth/password/setup` with a valid password and no bootstrap token
- **THEN** the system accepts the request and configures the password
- **AND** the configured `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` value is ignored
