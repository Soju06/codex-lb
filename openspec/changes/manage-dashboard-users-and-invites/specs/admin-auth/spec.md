## ADDED Requirements

### Requirement: Invite acceptance routes

`GET /api/dashboard-auth/invite/{token}` SHALL be unauthenticated, rate limited per client (30 per 60 s, `429 invite_rate_limited` with `Retry-After`), and SHALL answer `{roleName, inviterDisplayName, suggestedUsername, usernameLocked, expiresAt}` for a live invite (`inviterDisplayName` is the inviter's display name, else username, else null) and one `404 invite_not_found` for an expired, consumed, revoked, or unknown token. `POST /api/dashboard-auth/invite/accept` `{token, username?, password, displayName?}` SHALL be unauthenticated and SHALL: answer `409 already_signed_in` when the request carries a valid account session cookie; apply the same password policy as `/password/setup`; be rate limited per client (8 per 60 s) and per token hash (5 per 60 s) with `429 invite_rate_limited`; answer `404 invite_not_found` for any token that is not live; allow `username` to replace the pre-filled one unless the invite is `usernameLocked` (`422 username_locked`), subject to the username shape (`422 validation_error`) and uniqueness (`409 username_taken`); consume the invite compare-and-set with the presented token hash in the predicate, so a second accept of the same token, or an accept whose token was rotated by a resend between lookup and consume, answers `404`; a username collision that the pre-check missed MUST still answer `409 username_taken` and leave the invite unconsumed; set the account's password, display name, `status=active`, `last_login_at`, increment its `session_generation`; audit `invite_accepted` with the new account as actor; and issue a v2 account session with the same TTL and TOTP-enrolment rules as password login. Cross-site requests to `/invite/accept` are refused by the origin check like every other mutation.

#### Scenario: Lookup and acceptance

- **WHEN** a signed-out browser opens the invite link and then posts the token with a password
- **THEN** the lookup answers `200` with the role name and suggested username, the accept answers `200` with `authenticated: true` and a session cookie, and `GET /api/dashboard-auth/me` works

#### Scenario: A link works once

- **WHEN** the same token is posted again from another client
- **THEN** the response is `404 invite_not_found`

#### Scenario: Signed-in browsers do not consume invites

- **GIVEN** a valid account session cookie
- **WHEN** the client posts a valid token to `/invite/accept`
- **THEN** the response is `409 already_signed_in` and the invite stays live

#### Scenario: Rate limits

- **WHEN** a client exceeds 30 lookups or 8 accept attempts within a minute, or 5 attempts for one token
- **THEN** the next request answers `429 invite_rate_limited`

### Requirement: Invite tokens never reach the logs

Rendered log lines (access logs, error lines) that contain `/api/dashboard-auth/invite/<token>` MUST have the token segment replaced by `[REDACTED]` at every log level; `/api/dashboard-auth/invite/accept` stays readable.

#### Scenario: Access log

- **WHEN** a request for `/api/dashboard-auth/invite/<40-character token>` is logged
- **THEN** the rendered line contains `/api/dashboard-auth/invite/[REDACTED]`

### Requirement: Password removal is refused while an invite is pending

`DELETE /api/dashboard-auth/password` SHALL, under the same write-intent serialisation as account mutations, additionally refuse with `409 other_users_exist` while any live (unconsumed, unrevoked, unexpired) invite exists, so a passwordless install can never become one where sign-in is mandatory again but no admin holds a password.

#### Scenario: Pending invite blocks removal

- **GIVEN** the only active admin has invited a viewer
- **WHEN** the admin removes the dashboard password
- **THEN** the response is `409 other_users_exist`
- **AND** after revoking the invite the same call answers `200`

### Requirement: Self-service profile edit

`PATCH /api/dashboard-auth/me` `{displayName?, email?}` SHALL require a fully authenticated account session (an unenrolled account under the TOTP policy may use it), SHALL update only the fields present (`null` clears), SHALL normalise the e-mail and refuse a taken one with `409 email_taken` and an invalid one with `422 validation_error`, SHALL audit `user_updated` with the account as actor, and SHALL return the same shape as `GET /api/dashboard-auth/me`.

#### Scenario: Edit own profile

- **WHEN** an account patches its display name and e-mail
- **THEN** the response echoes the trimmed display name and lower-cased e-mail, and a second account cannot claim that e-mail

### Requirement: Pending invites are counted in the access summary

`access_summary.pending_invites` in the session response SHALL equal the number of live invites (not consumed, not revoked, not expired) of `invited` accounts.

#### Scenario: Count follows the lifecycle

- **WHEN** an admin creates an account and the person accepts
- **THEN** `pending_invites` is `1` before acceptance and `0` after
