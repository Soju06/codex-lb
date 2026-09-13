## ADDED Requirements

### Requirement: Inventory and topology reads are not guest-safe

The following dashboard reads MUST require the named permission at `all` scope; principals without it (including the built-in `guest`) MUST receive HTTP 403 with error code `permission_required` and `param` naming the permission:

- `GET /api/api-keys`, `GET /api/api-keys/`, `GET /api/api-keys/{id}/trends`, `GET /api/api-keys/{id}/usage-7d` → `api_keys:read`
- `GET /api/settings/upstream-proxy`, `GET /api/settings/runtime/connect-address`, `GET /api/sticky-sessions` → `ops:write`
- `GET /api/oauth/status` → `accounts:write`

`GET /api/request-logs/options` MUST remain readable by every dashboard principal but MUST return an empty `apiKeys` list to principals without `api_keys:read`, with every other facet unchanged. `GET /api/request-logs` MUST return `apiKeyId` and `apiKeyName` as null, and free-text search MUST NOT match API-key ids or names, for principals without `api_keys:read`. These routes are part of the route list verified by the route authorization matrix (see "Dashboard route authorization is machine-verified").

#### Scenario: Guest cannot list API keys

- **WHEN** a guest principal requests `GET /api/api-keys/`
- **THEN** the response is HTTP 403 with error code `permission_required` and `param` `api_keys:read`
- **AND** no key names, prefixes, policies, assignments, limits, or usage are returned

#### Scenario: Guest cannot read egress topology or sticky bindings

- **WHEN** a guest principal requests `GET /api/settings/upstream-proxy`, `GET /api/settings/runtime/connect-address`, or `GET /api/sticky-sessions`
- **THEN** the response is HTTP 403 with error code `permission_required` and `param` `ops:write`

#### Scenario: Guest request-log filter options omit the key inventory

- **WHEN** a guest principal requests `GET /api/request-logs/options`
- **THEN** the response succeeds with `apiKeys` equal to `[]`
- **AND** account, model, and status options are unchanged

#### Scenario: Guest request-log rows omit the key identity

- **WHEN** a guest principal requests `GET /api/request-logs` for logs made with a named API key
- **THEN** every row has null `apiKeyId` and `apiKeyName`
- **AND** `GET /api/request-logs?search=<key name or key id>` returns no rows for the guest while the same search returns them for an admin

### Requirement: Account identity is redacted without account write access

For principals that do not hold `accounts:write`, every account summary returned by `GET /api/accounts` and embedded in `GET /api/dashboard/overview` MUST mask `email` to its first local-part character followed by `***` and the domain (`a***@example.com`), MUST return `chatgptAccountId`, `workspaceId`, and `workspaceLabel` as null, and MUST derive `displayName` from the alias or the masked e-mail. Account status, plan, alias, quota, and reset fields MUST be unchanged. Request-log free-text search MUST NOT match account e-mail addresses for such principals. Principals holding `accounts:write` MUST receive the unredacted summary.

#### Scenario: Guest sees masked account identity

- **WHEN** a guest principal requests `GET /api/accounts` for an account with e-mail `alice.smith@example.com` and a ChatGPT account id
- **THEN** the returned summary has `email` `a***@example.com`, `displayName` `a***@example.com`, and null `chatgptAccountId`, `workspaceId`, `workspaceLabel`
- **AND** the raw e-mail and ChatGPT account id appear nowhere in the response

#### Scenario: Guest search cannot probe account e-mails

- **WHEN** a guest principal requests `GET /api/request-logs?search=alice.smith` and a request log belongs to an account with that e-mail
- **THEN** the search returns no rows for that term
- **AND** the same request log is still found by its request id

#### Scenario: Account writer sees full identity

- **WHEN** a principal holding `accounts:write` requests `GET /api/accounts`
- **THEN** `email`, `chatgptAccountId`, `workspaceId`, and `workspaceLabel` are returned unredacted

### Requirement: Guest sessions are bound to a revocable generation

The system SHALL persist an integer `guest_session_generation` on the dashboard settings row (default 0). Every guest session cookie MUST carry the generation current at issuance, and a guest cookie MUST authorize only while its generation equals the stored value; a guest cookie without a generation MUST NOT authorize. Admin sessions MUST NOT carry a generation. The system MUST increment the generation when the guest password is set, when the guest password is removed, when guest access transitions from enabled to disabled, and when `POST /api/dashboard-auth/guest/logout-all` is called. That endpoint MUST require `security:write`, MUST leave guest access and guest password settings unchanged, and MUST record the audit action `guest_sessions_revoked`. Saving unrelated settings, or re-saving guest access as enabled, MUST NOT change the generation. Every generation bump, including `POST /api/dashboard-auth/guest/logout-all`, MUST durably bump the `settings` cache-invalidation namespace before the response is returned, so peer replicas stop honoring the old generation within the invalidation-bus poll bound (the per-process settings cache TTL is the fallback bound). Guest login MUST stamp the generation from the same database row the credential check used, not from a possibly stale process cache.

#### Scenario: Rotating the guest password logs guests out

- **WHEN** a guest holds a valid password-authenticated guest session and an administrator sets a new guest password
- **THEN** the guest's next dashboard request returns HTTP 401 `authentication_required`
- **AND** logging in with the new password issues a working session

#### Scenario: Disabling guest access invalidates sessions even after re-enabling

- **WHEN** a guest session exists and guest access is switched off and then on again
- **THEN** the pre-existing guest session no longer authorizes

#### Scenario: Administrators can log every guest out

- **WHEN** a principal with `security:write` calls `POST /api/dashboard-auth/guest/logout-all`
- **THEN** every existing guest session stops authorizing
- **AND** guest access and guest password settings are unchanged
- **AND** a `guest_sessions_revoked` audit entry is written

#### Scenario: Guests cannot revoke guests

- **WHEN** a guest principal calls `POST /api/dashboard-auth/guest/logout-all`
- **THEN** the response is HTTP 403 with error code `permission_required` and `param` `security:write`

#### Scenario: Unrelated settings saves keep guest sessions

- **WHEN** an administrator saves a non-guest setting or re-saves guest access as enabled
- **THEN** existing guest sessions continue to authorize

### Requirement: Guest session generation migration

The Alembic revision `20260908_000000_add_guest_session_generation` MUST add `dashboard_settings.guest_session_generation` as a NOT NULL integer with server default 0, MUST be idempotent on re-run, and MUST drop the column on downgrade. Existing settings rows MUST read as generation 0 after upgrade.

#### Scenario: Upgrade seeds zero and downgrade removes the column

- **GIVEN** a database at the parent revision with a seeded settings row
- **WHEN** the revision is applied
- **THEN** the column exists and the row's value is 0
- **AND** downgrading to the parent removes the column
- **AND** upgrading to head passes through the revision on a single-head graph
