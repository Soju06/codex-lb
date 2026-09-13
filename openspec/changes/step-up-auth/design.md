## Context

Every dashboard mutation is authorised by the account's grants and nothing else. Phase 0/1 gave the dashboard fine-grained permissions and per-account sessions; `auth-provider-abstraction` (PR-2c-1) made trusted-header users accounts too, with `auth_method=trusted_header` and no cookie. H5 adds a time-boxed second factor to the four permission families that decide who can sign in or that hand out upstream credentials.

## Goals / Non-Goals

**Goals**
- A stolen session cookie (or a browser left open) cannot change sign-in security or export credentials without the person re-proving a credential.
- The rule is defined per account, not per mode: whatever the account holds is what it re-proves. Nobody is silently exempt.
- Accounts that sign in through a provider and hold no password can enrol TOTP and use it for step-up.
- One frontend flow: the interrupted request is replayed after the dialog, callers do not change.

**Non-Goals**
- OIDC re-authentication (`prompt=login`, `max_age`) as a factor — Phase 3a.
- The admin-role TOTP enrollment requirement — PR-2a.
- A configurable window.

## Decisions

### Which permissions, and only for mutations

`STEP_UP_PERMISSIONS` is the write side of `PRIVILEGED_PERMISSIONS` (`security:write`, `users:manage`, `roles:manage`, `accounts:export`); `conversations:read` and `audit:read` are privileged for the admin-TOTP policy but have no mutation to protect. The check runs inside the shared permission dependency for non-safe methods, so every route already gated by one of these permissions is covered without per-route code, and a future route gets it by declaring its permission. `PUT /api/settings` is the one handler-level check and follows the existing "a security field's value actually changed" rule.

### Methods follow the account, refusal is explicit

`step_up_methods(user)` is `password` when the account has a hash and `totp` when it has a secret; every listed factor is required. An empty list is answered with `step_up_unavailable` and the message "Set up two-factor authentication or a local password to change security settings" — never with a pass. The provider's `idp_mfa_enforced` flag speaks to the enrollment requirement (D9), not to step-up: the proxy cannot re-verify anything for us at request time.

### Where the verification rides

Password sessions already carry a sealed cookie; `su` joins `pv`/`tp`/`am` there and is copied unchanged when the cookie is re-issued (password change). The sign-in itself counts when it presented every factor the account holds — a password login for an account without a secret, or the `/totp/verify` step — so a fresh sign-in is not followed by an immediate second prompt. Trusted-header accounts have no cookie to extend, so the endpoint sets a separate `codex_lb_step_up` cookie whose payload names the account; a cookie for another `uid` is ignored, and its `exp` is the same five minutes. The dependency also honours a fallback password session of the same account riding along with a header request.

### One clock

Cookies are minted and checked against `session_clock()` in `dashboard_auth.service`, the same `time()` the session store uses, so tests that freeze the auth clock see step-ups age consistently.

### Frontend: intercept at the client, not at every caller

The API client owns the retry: on `403 step_up_required` it awaits the registered dialog and replays the identical request once (`skipStepUp` on the replay prevents a loop); concurrent gated requests wait on the same dialog. Callers therefore see either their result or an ordinary error. `step_up_unavailable` becomes a toast pointing at My sign-in, where the TOTP control now renders for every account and reads the account's `totpConfigured` from the session (the settings row only mirrors the migrated admin's secret).

## Risks / Trade-offs

- A reverse-proxy install upgraded to this change cannot edit security settings, accounts or providers until its admin enrols TOTP or sets the local `admin` password; reads and every other write continue. This is the point of H5 and is documented in `docs/authentication.md`.
- Disabling TOTP on a password-less account removes its only step-up method; the route allows it (the person just proved the code) but reports `stepUpAvailable: false` so the UI can say so.
