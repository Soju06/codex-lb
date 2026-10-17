## MODIFIED Requirements

### Requirement: Adding a person issues a one-time invite

`POST /api/dashboard-users` `{username, displayName?, email?, roleId, usernameLocked?, ssoOnly?, expectedIdentity?: {provider, providerKey?, subject}}` SHALL normalise the username (trim, casefold) and refuse an invalid shape with `422 validation_error`, refuse a taken username with `409 username_taken` and a taken e-mail with `409 email_taken`, refuse a role that does not exist or is not assignable (presets outside `admin`, `operator`, `viewer`; custom roles with `assignable_to_users=false`) with `422 role_not_assignable`, and refuse a role whose grants exceed the caller's (`assert_can_delegate`) with `403 insufficient_delegation`. Unknown fields MUST be rejected with `422`. `ssoOnly` or `expectedIdentity` MUST be refused with `409 sso_not_available` unless the named `(provider, providerKey)` is an active non-password provider; `ssoOnly` without `expectedIdentity` answers `422`; an `expectedIdentity` that already belongs to an account, or that another open (not consumed, not revoked) invite already waits for, answers `409 identity_taken` — a partial unique index on the open invites' expected triple backs the check, and its violation is mapped to the same code. A trusted-header `subject` is stored case-folded; `subject` is bounded to 512 characters like the identity column. The username `admin` is refused with `422`. On success it SHALL create the account with `status=invited`, `role_source=manual`, `created_by_user_id=<caller>` and one invite valid for 24 hours whose SHA-256 is stored, carrying `sso_only` and the `expected_*` triple, and SHALL answer `201 {user, invite}` where `invite` is `{token, expiresAt}` — the only time the plaintext token is returned — or `null` for an SSO-only account, whose activation is its first provider sign-in matching the expected identity. An SSO-only invite has no link and never expires: it stays live until consumed or revoked, is never purged, `POST /{id}/invite` (resend) answers `409 sso_only_invite`, and its token hash opens nothing on `GET /api/dashboard-auth/invite/{token}` or `POST .../invite/accept` (`404`). Account listings and `GET /api/dashboard-users/invites` carry `pendingInvite: {expiresAt, ssoOnly}` / `ssoOnly` with `expiresAt` null for SSO-only rows. It SHALL audit `user_created` (role slug) and `user_invited` (`sso_only`, `expires_at` only for link invites, and the expected provider and subject when set).

#### Scenario: Create returns the token once

- **WHEN** an admin creates `Bob` with the viewer role
- **THEN** the response is `201` with `user.username == "bob"`, `user.status == "invited"`, and a non-empty `invite.token`
- **AND** a later listing shows the account with `pendingInvite` but no token

#### Scenario: Duplicate and invalid input

- **WHEN** an admin creates a second account with the same username in another case, or the same e-mail
- **THEN** the responses are `409 username_taken` and `409 email_taken`
- **AND** creating an account with the `guest` or `member` role answers `422 role_not_assignable`

#### Scenario: Delegation

- **GIVEN** a caller whose role holds `users:manage` but not the admin grants
- **WHEN** it creates an account with the admin role
- **THEN** the response is `403 insufficient_delegation`
- **AND** creating an account with the viewer role succeeds

#### Scenario: SSO-only account

- **GIVEN** the trusted-header provider is active
- **WHEN** an admin creates `bob` with `ssoOnly: true` and `expectedIdentity: {provider: "trusted_header", subject: "Bob@Example.com"}`
- **THEN** the response is `201` with `invite: null`, the pending-invite list marks the row `ssoOnly`, and the account activates when the proxy first sends `bob@example.com`

#### Scenario: SSO-only account waits without expiring

- **GIVEN** an SSO-only viewer account whose invite row is 25 hours old
- **WHEN** an admin lists accounts and invites
- **THEN** the account is still listed with `pendingInvite.expiresAt` null, resend answers `409 sso_only_invite`
- **AND** the first proxy request for its identity signs in as that viewer instead of provisioning an admin

#### Scenario: SSO fields need a provider

- **GIVEN** a standard-mode install
- **WHEN** an admin posts `ssoOnly: true` with an expected identity
- **THEN** the response is `409 sso_not_available`
