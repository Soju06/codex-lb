## Context

`dashboard_users` is already the source of truth for sign-in; principals carry `user_id`, `role_slug` and `grants`; audit rows carry an actor. What is missing is the lifecycle: adding, changing, disabling and removing accounts, and the invariants that keep a multi-account install safe. This change adds the smallest complete slice of PLAN.md §4.5 (no SSO, no custom-role writes, no frontend).

## Goals / Non-Goals

**Goals**
- An admin can add a person by handing them a link; nobody but the person ever knows the password.
- Every dangerous transition (last admin, self-demotion, wider-than-caller delegation, deleting the migrated `admin`, leaving an account with no credential) is refused in one service with an exact error code.
- Disabling a person turns off what they own (API keys) in the same transaction and ends their sessions on the next request.
- The frontend can render role pickers and permission grids from one endpoint per concept.

**Non-Goals**
- SSO-only accounts and expected identities (Phase 2), custom role writes (Phase 5b), effective-access preview, per-user key listing, any UI.

## Decisions

### Invites are pre-created accounts with a hash-only, single-use token

An invite is a `status=invited` user row plus one `dashboard_user_invites` row. The role lives on the user row, so nothing needs to be re-checked at acceptance; the invite only proves possession of the link. Like the bootstrap token, only a SHA-256 of the token is stored and the plaintext is returned once (`201`, and again on resend). Resending rotates the hash in place (the old link dies immediately). Acceptance is a compare-and-set `UPDATE ... WHERE consumed_at IS NULL AND revoked_at IS NULL AND expires_at > now`; zero rows means another accept won and the caller sees the same `404 invite_not_found` as for a garbage token. Expired, consumed, revoked and unknown tokens are deliberately indistinguishable so the public route leaks nothing about which accounts exist.

### No orphan "invited" accounts, no background job

Revoking the invite of an `invited` account deletes the account row (it has no credential, no keys, no sessions). Expired invited accounts are purged lazily — on `GET /invites` and before every create/resend — instead of by a scheduler: a personal install that invited one person and never followed up returns to its clean single-account state the next time the admin opens the people list.

### Delegation is a subset check on grant tables

`assert_can_delegate(caller, target)` compares `(permission, scope)` pairs: the target's scope must be satisfied by the caller's grant for the same permission (`scope_satisfies`), with a missing permission ranking below `own`. Applied to the new role on create/PATCH and, via `assert_can_act_on`, to the target account's current role on every action against another account. It follows that only an admin-preset holder can grant the admin preset, and that a custom role with `users:manage` but not `security:write` cannot touch an admin. The check runs before the last-admin rule on PATCH and after it on DELETE, matching the plan's ordering, so a manager without admin grants is told `insufficient_delegation` rather than being given a hint about admin counts.

### Last admin counts only the admin preset

The invariant "at least one active admin remains" counts active accounts whose `role_id` is the admin preset. A custom role holding every permission does not count: the rule must be deterministic across vocabulary upgrades. The check is a post-state predicate: the target counts today (active and admin) and would not count after the change → refuse when nobody else counts.

### Mutations require an account, not just a permission

`users:manage` is held by the implicit local admin, the disabled-auth principal and a trusted-header principal without a row. None of them can be recorded as an inviter or an actor with an id, so every mutating route additionally requires `principal.user_id` (`409 admin_account_required`). Reads stay open so the people list can show "set a password first".

### Key cascade records why

Disabling an account (and deleting one) sets `is_active=false, deactivated_reason='owner_disabled'` on every active key it owns, in the same transaction as the status change. Re-enabling never restores keys by itself; `POST /{id}/reactivate-keys` restores only `owner_disabled` keys, so a key an operator blocked by hand stays blocked. Every touched key hash is invalidated in the process cache and the `api_key` namespace is bumped once, the same path `PATCH /api/api-keys` uses.

### Credential-required guard

`assert_credential_remains(password_hash, identity_count, solo_install)` refuses any end state with no password and no identity, except the single-account password removal that intentionally returns the install to bootstrap. In this release the callers are `DELETE /api/dashboard-auth/password` (belt-and-braces after `other_users_exist`) and `PATCH status=active`; TOTP reset is not a credential change. It lives in `dashboard_users/credentials.py` so the auth module can import it without importing the users service.

### Roles API reads code truth

Preset grants are resolved with `resolve_role_grants` (code), `assignable_to_users` for presets comes from `ASSIGNABLE_PRESET_ROLES` rather than the row, and `/permissions` is built from `Permission`, `PERMISSION_IMPLIES`, `OWN_SCOPED_PERMISSIONS`, `PRIVILEGED_PERMISSIONS` plus a description table in `dashboard_roles/service.py` whose completeness is unit-tested.

## Risks / Trade-offs

- `sso_only` / `expected_identity` are rejected (`extra="forbid"`) rather than accepted-and-ignored, so a future client that sends them against this release gets a clear `422`.
- Time comparisons on invites use aware UTC datetimes bound to `DateTime(timezone=True)`; SQLite stores and returns naive UTC, so the service normalises on read (`as_utc`) and the consume UPDATE disables ORM identity-map evaluation.
- Operator holds no `users:manage`, so the "operator invites viewer" case from the plan is exercised with a custom role that holds `users:manage` plus read grants; the pure operator→admin refusal is unit-tested on the primitive.
