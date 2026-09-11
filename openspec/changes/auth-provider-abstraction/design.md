## Context

`dashboard_auth_mode=trusted_header` produces `DashboardRequestAuth(actor=<header>)` and the session dependency turns it into `admin_principal(user_id=None)`. Phase 1 gave the dashboard accounts, identities (`dashboard_identities`), invites and roles, but the trusted-header path bypasses all of it. Phase 3 (OIDC, SCIM) needs the same identity → account mapping, so it is built once here and the header becomes its first client.

## Goals / Non-Goals

**Goals**
- Every trusted-header request is served as an account with a role; the audit log names it.
- An upgraded reverse-proxy install behaves the same for its users: everyone the proxy vouches for still gets in as an admin (D10), existing accounts keep their roles.
- One resolver for every provider kind; providers only produce an `ExternalIdentity`.
- Nothing new to configure; the knobs live in a table with safe defaults and an API.

**Non-Goals**
- Mappings, `no_match_role_id`/`skip_role_sync` behaviour, the groups header, login policy, OIDC, the organisation UI, TOTP step-up for header sessions.

## Decisions

### Providers are rows plus a code implementation; "active" is computed

`dashboard_auth_providers.enabled` is not a persisted copy of the env mode. A provider is active when its row is enabled *and* the mode admits its kind (`trusted_header` only in `trusted_header` mode; `password` in every mode, management being refused in `disabled` mode elsewhere). Persisting the mode would create a second source of truth that drifts on the next `docker run`.

### Identity resolution is one ordered path, throttled per identity

Steps: identity row → invited account whose live invite carries exactly this `(provider, provider_key, subject)` → active account with the same e-mail if the provider opted into `link_by_email` → JIT with `unknown_identity_role_id` (`NULL` refuses and audits `login_failed reason=unknown_identity` with the presented identity). Username is never a lookup key: the proxy's `admin` must not become the break-glass `admin`, so JIT reserves that name and hands out `admin-2`. Header-style providers present the identity on every request, so the resolution result is cached per identity for the users-cache TTL (5 s): the database path runs once per TTL per identity, `last_seen_at`/`last_login_at` are written once per TTL, and a cached `ResolvedAccount` only names the account — the request path re-reads it through the users cache, so a disable or role change takes effect within the same TTL as for password sessions.

### No re-evaluation without mappings (D10 regression guard)

With zero mappings, an existing account is never touched by the resolver except for its identity row's `last_seen_at` and the account's `last_login_at`. Changing `unknown_identity_role_id` later affects only accounts created after the change.

### Break-glass `admin` stays creatable next to proxy accounts

`create_first_admin` used to refuse when any active account could sign in; identity-only accounts now exist before any password does, so the guard counts password holders only. The migrated/compat `admin` remains the local password fallback the docs already promise for `trusted_header` mode, and the session response for header-less requests shows the reverse-proxy notice (not a login form) until such a password exists.

### TOTP is not applied to header sessions yet

A header session has no cookie to carry a verified TOTP step and no route to verify one; applying the policy would lock every header user out. The provider row already stores `idp_mfa_enforced`; the step-up change (PR-2b) wires the policy for header sessions and honours that flag.

### Frontend gates follow the account, not the mode

The People tab, the solo line and `/settings/access` required `authMode === "standard"` because account-less principals answer `409 admin_account_required`. A reverse-proxy account is an account, so the gate becomes `user !== null` (`disabled` mode keeps no people line: it can never have an account). The invite dialog reads `login.providers` to offer the password-less option only when a non-password provider is active.

## Risks / Trade-offs

- A proxy that sends different spellings of one person creates one account (subject is case-folded); a proxy that sends e-mails for some users and short names for others creates one account per distinct value — the documented behaviour of the header.
- `link_by_email` is off by default because an IdP that lets users change their e-mail would otherwise let one user claim another's account.
