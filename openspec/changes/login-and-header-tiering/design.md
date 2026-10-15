## Context

The auth store is the only role signal the SPA has: `permissions` holds the coarse `read`/`write` aliases plus, since `expand-dashboard-permission-vocabulary`, every grant as `<permission>:<scope>`. Pages gate on `canWrite`. The session response now also carries `user`, `login`, `totp_enrollment_required` and `access_summary`, but the client discards them. `AuthGate` decides between spinner, bootstrap, login form, TOTP dialog, trusted-header notice and the app purely from store flags; it never looks at the URL, so a link a visitor must be able to open without a session (`/invite/<token>`) cannot exist yet.

## Goals / Non-Goals

**Goals**
- A two-account install can sign in (username field appears when the server says so, or when the person asks for it), and a one-account install looks like today.
- An invited person can open the link, set a password and land in the dashboard without help, and gets exactly one message for a dead link.
- The header tells a team who is signed in and gives them their own controls; a solo install keeps the Logout button.
- One function decides tiers; components never combine `access_summary` facts themselves.
- Nav budget untouched (five core items), guest UI byte-identical.

**Non-Goals**
- The Access settings card, people table, invite dialog, `/settings/access` (next slice), the `/login` route and SSO buttons (Phase 2), own-scoped views for Member (Phase 4), a must-change-password gate (the flag is stored, nothing renders on it yet).

## Decisions

### Public routes are a branch of `AuthGate`, not of the router

The router lives inside the gate, so a route the gate does not know is unreachable without a session. Adding a `useLocation()` read and one `matchPath("/invite/:token")` branch before the login branch keeps the single decision point (initialized → public → bootstrap → login → trusted header → enrollment → app) and makes the rule "public routes render before the login branch" a property of one component. The screen is keyed by token so a different link mounts a fresh check. Signed-in visitors reach the same screen and are shown the "log out first" message from the store's `authenticated && user`; the server independently refuses the accept with `409 already_signed_in`.

### Disclosure is derived, never stored

`resolveDisclosureTier(accessSummary, user)` is the only reader of `access_summary`. The server only sends the summary to `users:manage` holders, so for everyone else the function has exactly one fact: whether the session is an account at all. A signed-in account that does not manage users can only exist because somebody invited it, which is the §4.11 team rule evaluated from that fact — `null` + `user` → `team`; `null` without a user (guest, visitor, implicit admin) → `individual`. With a summary it returns `enterprise` when any enterprise object exists, else `team` when a second row / pending invite / non-admin exists. The store computes `tier` once per session and components read `tier`, so an install that deletes its second account falls back to the individual header on the next session refresh without any component knowing why.

### `can()` reads scoped grants only

`can(permission)` is true when `permissions` contains `<permission>:all` or `<permission>:own`; the legacy aliases are not mapped onto fine-grained permissions (that mapping would be a guess: `read` says nothing about `accounts:read`). `canWrite` stays the alias check so existing gates are untouched. Fixtures gain the wire form of the preset grants so tests exercise the same strings the backend emits.

### Nav metadata is the route-guard source

Each nav item carries the permission its page's reads require (`accounts:read` for Accounts, `dashboard:read` for everything else, mirroring the backend route matrix). The header filters by it and `RouteGuard` looks the current path up in the same arrays, so a page cannot be reachable by URL while hidden from the nav. The fallback is `/dashboard`; if even `dashboard:read` is missing the not-found surface renders instead of a redirect loop (the backend refuses such roles at sign-in anyway). Moving the arrays to `nav-items.ts` is forced by the fast-refresh lint rule (the header file may only export components); the budget config is repointed and still counts five.

### Username memory is the typed value

The form remembers the username the person typed, not the account the server resolved: a single-account install signs in without a username and must keep its password-only form. A remembered username forces the field visible even when the server says `hidden` (PLAN §4.4 anti-flapping), and is cleared after a username-less sign-in.

### One enrollment form

The QR/secret/code part of the Settings TOTP dialog becomes `TotpEnrollmentForm` (starts `/totp/setup/start` on mount, confirms on submit). The gate renders it when the session says `totpEnrollmentRequired` (the backend answers 403 everywhere else) and the Settings dialog keeps its behaviour through the same component. `/totp/setup/confirm` stores the secret but does not mark the session TOTP-verified, so a plain refresh would show the TOTP dialog right after the QR step. The form hands the confirmed code up and the gate calls `/totp/verify` with it before anything else; if that fails (clock skew, replay protection) the gate refreshes and the regular TOTP dialog takes over. The durable fix — confirm issuing a verified session — is a backend follow-up. The invite screen does not render it itself: after a successful accept it refreshes the session and navigates to `/`, and the gate shows the enrollment step before any page — one enrollment surface instead of two.

## Risks / Trade-offs

- [Risk] The "different account" link changes the individual login card by one line of text. → Required by PLAN §4.4 so a personal install that accidentally has two accounts is never locked out; the screenshots show the only difference.
- [Decision] No "Invite teammate" item yet: its target (the Access card) lands in `access-settings-card`, and a menu entry without a target would be a stub. "My two-factor" links to `/settings#totp` (the TOTP card gets that id) and is offered only under the predicate the Settings page uses to mount that card, so the link always lands.
- [Decision] Session-fetch failures render the route-recovery error surface with a retry that re-requests the session; before, the store's least-privilege defaults sent the visitor through `RouteGuard` to the not-found page. The 401 handler likewise resets to least privilege with `initialized: false`, so the gate shows its spinner until the follow-up refresh settles.
- [Trade-off] `RouteGuard` redirects rather than rendering a "not allowed" page. → Every assignable role today holds every nav permission, so the guard is a safety net for future roles, not a surface anyone sees now.
