## Context

Everything this change needs already exists: accounts and roles (1a), the compat `admin` row with `is_break_glass` backfilled (1a-1), per-user TOTP and the enrolment state (1a-2), step-up (2b), the provider rows and the `IdentityResolver` (2c-1), the Organisation group and its two cards (2c-2). What is missing is the one setting that decides who may still use the local password form, and the invariant that keeps that setting from being a lockout button. The wire fields were even shipped ahead of time: `login.local_login` and `access_summary.local_login_policy` are one-value `Literal`s hardcoded at their single construction site, and the frontend's `resolveDisclosureTier` already treats a non-default policy as the enterprise tier. This change fills them in.

## Goals / Non-Goals

**Goals**
- A company can close local sign-in without being able to lock itself out, and the refusal that stops it always names the account that would fix it.
- One function decides "would this leave zero ways in", called from every path that could — auditable as a list, not as a habit.
- The default policy changes nothing: an install that never touches the setting behaves exactly as it does today, including the operator and viewer password sessions 2c-1 made possible behind a proxy.
- When the software's own guards are not enough, four commands on the host are, and they are written down before the identity provider that makes them necessary ships.

**Non-Goals**
- OIDC and its test-login pre-flight (3a) — the pre-flight shares this change's 409 vocabulary but adds its own proof-of-login condition.
- SCIM (3b), audit webhooks (Phase 4), the custom role editor (5b), API key ownership (Phase 4).

## Decisions

### The designation and the qualification are two different things

Making `is_break_glass` mean "this account can be relied on in an emergency" would have been simpler, but the migration cannot create such an account: the compat `admin` it backfills has a password and usually no second factor. If the flag implied qualification, the first upgrade would ship a lie. So the flag is a *designation* an operator can carry forward from the legacy install, and qualification is computed from five current facts on every read. The fifth is the local password: an account a sign-in provider created and that never set one cannot use the local password form at all, so counting it would let an install tighten the policy into a state where the proxy is the only door — which is the failure the invariant exists to prevent, not a variant of it. Because designation and qualification are two things, `break_glass_only` admits on the qualifying predicate, not on the flag: admitting the bare designation would leave the migrated `admin` as a password-only admin door under the *strictest* policy, weaker than `admins_only`, and would be the one place in the change where a gate read designations. This is also why enrolling a second factor is enough to unblock a policy change with no second write, and why the plan's "migrated admin cannot tighten, but can after enrolling" test is a two-line test instead of a workflow.

### The predicate is post-state, and it lives inside the write

A pre-state check ("is this the last one?") answers the wrong question for half the call sites: removing a TOTP secret does not remove an account, and clearing a designation does not change a role. The guard therefore computes the resulting row set — role, status, designation, second factor and local password as they would be after the change — and counts qualifying accounts in it. And a read-side check alone is a race: the codebase already learned this with `last_admin_protected`, whose UPDATE is conditional on another admin existing *at that moment*, behind a write-intent transaction on SQLite and `FOR UPDATE` on PostgreSQL. The break-glass guard copies all three layers rather than the first one. Two sequencing rules make the lock real rather than decorative, and both were review findings on this change: the intent must be acquired **before the path's first read**, because `BEGIN IMMEDIATE` cannot run inside an open transaction and a late acquisition falls back to a marker write rather than the statement the code names; and nothing may **commit between the count and the write**, because a commit releases it. `/totp/disable` therefore advances the TOTP replay counter (which commits) *before* the guard rather than between the guard and the secret write — the cost is that a refused removal still spends the typed code, which is a far better trade than a guard two callers can both pass. The two gates in the other direction (policy tightening, provider enable) take the same lock for the same reason, and only when the payload could actually trip them, so ordinary settings saves are not queued behind account mutations.

### One requirement lists all eight call sites, instead of eight edited requirements

The call sites belong to six requirements owned by four unarchived change folders, and three of them (`isBreakGlass` on the PATCH, `deactivate_user()`, custom-role delete-and-reassign) do not exist yet. Chaining a fourth or fifth copy of each host block would add several hundred lines of unrelated text and would still not make "one function, every call site" checkable in one place. `dashboard-users` already has the shape to copy: "Credential-required guard" is a tiny requirement that defines `assert_credential_remains(...)` once. This change does the same, with one scenario per call site, and edits a host requirement only where the new behaviour contradicts what is written there (self-service `/totp/disable` in `admin-auth`, `enabled` on the provider PATCH in `identity-providers`).

### Custom-role delete-and-reassign is specified now and implemented in 5b

The plan names it as a call site, but `dashboard_roles/api.py` is read-only in this release and building the transaction here would be a second PR's worth of work. Leaving it out of the spec would be worse than either: the requirement that enumerates the call sites is the thing a Phase 5b author will read, and an omission there is exactly how a guard grows a hole. It is therefore listed with its own scenario and no implementation, which is the honest state — the alternative, a stub endpoint, is banned by PLAN §4.8's second principle.

### Mapping re-evaluation is exempt, and the exemption is a fact rather than a check

Break-glass accounts are `role_source=manual`, and re-evaluation never touches manual accounts. Adding a guard call to the resolver would therefore be dead code that reads like a live protection. Instead this change makes the exemption load-bearing in the other direction: setting the designation *forces* `role_source` to `manual`, so the property the exemption depends on cannot drift.

### The resolver's demotion path pins, it does not raise

`IdentityResolver._demote()` runs on a request-serving path with no principal, and already pins the last admin instead of refusing. Routing it through a `deactivate_user()` that raises would turn a silent pin into an exception on every identity-cache TTL and one audit row per sign-in. The shared function therefore keeps the resolver's contract: refuse the deactivation, pin the account to `manual`, audit once.

### Break-glass sign-in always takes a second factor — but a designated account without one can still get in

These two sentences look contradictory and are not. "Always requires two-factor" binds an account that *has* a secret: no toggle, global or admin-level, can let it through on a password alone. An account that has no secret is by definition not qualifying, cannot be what any policy relies on, and must be able to sign in and reach `/totp/setup/*` — otherwise the migrated install could never enrol its way to a qualifying account. The policy is what keeps that from being a hole: while the policy is `enabled` nothing depends on break-glass at all, and the policy cannot leave `enabled` until a qualifying account exists.

### The rate limiter gains the username and *keeps* the address bucket

Keying only on the username would hand an attacker a free denial of service against a known account from anywhere, and would make the key attacker-controlled and unbounded. Keying only on the address is what exists today and is what the plan's "IP X does not bar A at IP Y" test rejects. The key is therefore the pair, over the *normalized* username so case variants share one budget — **and the per-address bucket stays, as a second counter that every attempt also spends.** Replacing the address bucket with the pair leaves the endpoint with no ceiling at all: the pair is local by design, so one address mints a fresh budget for every username it invents, and each invented username still costs a blocking bcrypt comparison on the loop that serves the proxy.

The numbers therefore differ from PLAN §4.4, which put 8/60 s on the address. 8/60 s moves to the pair, and the address bucket becomes a coarse 60/60 s ceiling: keeping 8/60 s on the address would make the pair unreachable and would bar a whole NATed office after eight mistyped passwords — the opposite of the "limits must be local" goal the pair exists for. 60/60 s is seven and a half times the per-account allowance, which no shared egress reaches through a login form before the per-account limit bites, while still bounding hash comparisons per address per minute. A successful sign-in clears the per-account bucket only. The coarse bucket is left to expire on its own window, because clearing it on success would let anyone with a single valid account reset the endpoint's only ceiling between sprays; one sign-in against 60 per minute is nothing an operator notices. Because a bucket is otherwise only cleared by a success, the periodic cleanup scheduler's leader pass ages the rows out — otherwise the table grows by whatever an attacker chooses to type.

### The break-glass limiter exemption is dropped

PLAN §4.4 exempts the emergency account from the failed-login limiter so that no remotely triggerable lockout can bar it during an identity-provider outage (T13/T15). That exemption was written against a limit the plan assumed was keyed on the username alone, which an attacker anywhere could exhaust. Both keys here carry the **client address**, so an attacker hammering the break-glass username from their own address cannot touch the operator signing in from a different one: the property the exemption was protecting holds without it.

Keeping it would have cost something real. Because the bucket is per (address, username), whether the ninth failure for a given username answers `429` or `401` is a remote oracle that names the emergency account to an unauthenticated caller — and that account is the single most valuable one to brute-force. Making the exemption invisible is not possible while the limiter's whole observable effect is the status code it changes. So the exemption is gone, no account is exempt from either bucket, and the byte-identical test now asserts that the break-glass username and an unknown username hit `429` at the same point.

### The CLI writes the database directly and takes no environment variable

The four commands exist for the moment the dashboard cannot be trusted to let anyone in, so anything that could be wrong with the running install must not be on their path: no FastAPI app import, no `init_db()` (it takes the SQLite lifetime lock and would fight the running server), no new connection-string flag, no environment variable of their own. They follow `codex-lb-db`: `get_settings().database_url` → `to_sync_database_url()` → a per-command engine disposed in `finally`, with the SQLite busy timeout set explicitly to 30 s so a recovery command does not fail with "database is locked" during exactly the incident it exists for. The audit row is inserted through the same sync transaction as the change rather than through `AuditService.log_async`, which needs a running loop and silently drops events after shutdown admission closes; the trade-off is that these rows bypass the sink fan-out Phase 4 will add, which is acceptable for an append-only record whose whole purpose is the database it is written to.

### The login-policy card renders without a reverse proxy; the other two do not

The Organisation group's existing requirement asserts that with no reverse-proxy row "neither card is rendered", which is right for cards that configure a reverse proxy. The login-policy card configures local sign-in, which every install has, and the install with no reverse proxy is precisely the one whose operator most needs to read `/login?local=1` and the account name — for instance after tightening the policy from the host CLI. That requirement is therefore modified rather than worked around, and its scenario now distinguishes the two kinds of child.

### `enabled` becomes writable on the provider PATCH

The plan gates "enabling a provider that has no password fallback" on a qualifying account, but 2c-1 deliberately made `enabled` a mode-driven, unwritable field. Keeping it unwritable would leave the gate specified against an API that cannot reach it, and the CLI already ships the *disable* direction; a gate with only a recovery path and no forward path is a half-feature. So the PATCH accepts `enabled`, guarded, and audits `provider_enabled`/`provider_disabled` — events PLAN §4.7 already names and nothing emits yet.

### The emergency indicator is a sibling of the account chip, not an item in its menu

The header only renders the account chip on the team and enterprise tiers with a `user` present, and a break-glass session can exist on an install the store still derives as individual. Hanging the indicator inside the menu would hide it in exactly the emergency it announces. It is a sibling in the actions row and is repeated in the mobile sheet, and it is cleared by the store's least-privilege reset so it cannot survive a sign-out.

### The session payload version stays at 2

The emergency marker is an optional claim whose absence means false. Bumping the payload version would sign every existing user out for a cosmetic pill — the opposite of what an emergency-access feature should do to an install under stress.

## Risks / Trade-offs

- **Behaviour change behind the proxy**: the replaced fallback serves *any* password-verified active account today, so the new gate is a no-op only while the policy is `enabled`. That is intended (PLAN §4.6 says so in as many words), but 2c-1's tests encode today's shape and must be read as the `enabled` case, not rewritten.
- **Two sources of the same decision**: the gate in `dependencies.py` and the description in `_decorate_session_response()` both answer "does the password cookie count". They are made to call one function; if they ever drift, the UI promises a fallback the gate refuses. The integration test asserts both from one request.
- **Ordering of refusals**: the compat `admin` is both the backfilled break-glass account and the account locked by `compat_user_locked`. `compat_user_locked` is checked first (it is a release-scoped lock on a specific row, not an invariant), so the six edge tests use a second, non-compat break-glass admin to prove the new guard rather than the old lock.
- **Spec delta size**: six modified requirements carry roughly thirty pre-existing scenarios that must be re-pasted verbatim, so the delta is much larger than the implementation. Per PLAN §6 openspec deltas are excluded from the line count, but reviewers should expect the diff shape.
