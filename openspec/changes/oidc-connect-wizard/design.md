## Context

Everything this change renders already exists on the wire. `GET /api/auth-providers` returns the OIDC row with its masked connection document and `testLoginVerifiedAt`; `PATCH` takes `label`, `enabled` and a whole `config`; `POST /oidc/test-login/start` answers `{authorizationUrl}` and sets a sealed flow cookie; the callback `303`s to one of four fixed in-app paths; the login hint already carries the row's label and its start path. The frontend contains, today, zero lines of OIDC: one fixture string in a login-form test and a comment in `rules.ts` saying OIDC arrives in a later phase.

Three properties of that backend shape every decision below.

**The callback talks to the window it redirected, not to the application.** It answers `303` and nothing else — no JSON, no `postMessage`, no opener callback, no state on the return URL. A pre-flight started from the settings page therefore has no first-party completion signal at all; the only authority is the provider row, re-read.

**The proof is deliberately opaque about who earned it.** `test_login_verified_at` is returned; `test_login_user_id` is not. The API will not say whose proof it is, on purpose — a UI that could read it would be tempted to treat somebody else's as its own, and the server would refuse the enable with `409` anyway.

**The refused person's identity reaches the audit row and stops there.** `_complete_sign_in` redirects to a bare `/auth/pending` with the flow cookie cleared and no session minted. The browser that lands there holds nothing, `pending_identity` is false (only the trusted-header path sets it), and `AuthGate`'s URL-only clause is scoped to `trusted_header` installs — so today an OIDC refusal lands on the ordinary login form and is told nothing at all.

## Goals / Non-Goals

**Goals**

- An operator connects Okta, Entra ID, Google Workspace, Keycloak or Authentik from one card, and cannot turn it on without having proved, from this browser and this account, that the connection they typed works.
- Every refusal the server can answer arrives next to the control that provoked it, in this product's words.
- A person the resolver refuses leaves the pending screen able to say something an administrator can act on, without the browser learning anything it did not itself supply.
- A signed-out browser learns exactly one new fact — the provider's label, which is what the button says.

**Non-Goals**

- SCIM and the automatic-account-management card (3b); audit webhooks and the audit-export card (5a); custom roles (5b). The plan's collapsed summary shape mentions those two; they contribute nothing to the line until they exist, because this product does not draw controls or claims the backend does not honour.
- A second simultaneous identity provider, and therefore a provider selector anywhere. `provider_key` is `default` and there is no create endpoint.
- Any change to the OIDC flow itself: no new redirect destination, no `next` parameter, no caller-supplied return URL, no change to what the callback validates.
- Rescuing a wizard mid-flight across a browser restart. The only thing that survives a navigation is a marker with an id and two timestamps.

## Decisions

### The pre-flight reports success by making the application ask the server again

The window the callback returns is our own origin, so the returning page can talk to the application. Once it has landed on the server's settings destination or its failure destination it does exactly one thing: it says the round trip ended. **That message carries no verdict.** The opener accepts the posted form only when `event.origin` is its own origin and `event.source` is the window handle it opened.

The verdict comes from one place: a fresh `GET /api/auth-providers`. The card arms when `testLoginVerifiedAt` has advanced past the value it read when the flow started. A message that never arrives is not a hang — the flow's own ten minutes end the watch with the same re-read, so a window closed by hand, a window that died on the identity provider's error page and a window that completed all converge on the same request and the same three outcomes (advanced, unchanged, refused).

The alternatives were both worse. Polling `popup.location` for the return URL throws while the window is at the identity provider (cross-origin) and has to be wrapped in a `try`/`catch` that swallows exactly the case it is looking for. Trusting a `postMessage` verdict would make a cross-window message the input to an authorization decision; it is not one, and writing it so that it cannot be one is cheaper than arguing about whether the origin check is sufficient.

### The opener link does not survive every identity provider, so the return does not depend on it

`Cross-Origin-Opener-Policy: same-origin` on an authorization endpoint forces a browsing-context-group switch when the window commits that page. Afterwards the opener's handle reports `closed === true` on a window that is standing open, and the window's own `window.opener` is null for good — the return navigation switches group a second time rather than handing the link back. This is not hypothetical: Microsoft served that header on `login.microsoftonline.com/…/authorize` in February–March 2026, and Entra ID is exactly the identity provider this wizard exists to connect. The public sign-in path is unaffected (it is a plain anchor navigation); only the admin pre-flight, which is window-only, meets it.

Two things follow, and both are the same rule as everywhere else here — *a signal is a reason to ask the server, never an answer*:

- **The returning window announces itself on a same-origin `BroadcastChannel` as well as to its opener.** The channel needs no origin check (nothing outside this application can address one) and it does not care about the opener, because the window is back on this origin by the time it sends. It still carries no verdict. With no opener the return hook lets the application mount rather than betting on `window.close()`: a disowned window is not script-closable everywhere, and a second copy of Settings in a small window is a better worst case than a blank one.
- **`handle.closed` stops being terminal.** It re-reads — a window genuinely closed by hand may have completed first — and then the poll clears itself, because a handle that has reported `closed` is worthless either way. The return signals and the deadline stay, so the round trip that is still running can still land. The cost is that a window closed by hand no longer produces the "did not finish" sentence at once; it produces it when the flow's ten minutes are up, or immediately if the window comes back. That is the honest reading, because after COOP the two cases are the same observation.

Without this the first bug is the loud one: within half a second of starting, the card tells an Entra ID operator the test sign-in failed while they are still typing their password. The second is the expensive one — the round trip that *did* succeed reports to nobody, a second dashboard mounts inside the window, and **Turn on** stays disabled on the attempt that earned the proof.

### The same-tab fallback exists because the window is often blocked, and it is a full navigation

`window.open` must be called **synchronously in the activation handler**. `POST /oidc/test-login/start` is `await`ed and is itself step-up gated, which can interpose a dialog; a window opened after that await is blocked by every browser. So the handler opens a blank window first and assigns its location when the start answers — and closes it if the start is refused, so a `502` never leaves an empty window behind.

When `window.open` returns `null` the flow continues in the same tab. There is no return URL to negotiate — the server fixed it — so the only thing the tab must leave behind is enough to recognise its own return: `{providerId, purpose, verifiedAt}` in `sessionStorage`, consumed once on return. It holds no connection document and never the client secret; nothing in this change writes a secret to storage, to a URL or to a query.

The return lands on `/settings?org=1#oidc`, which the frontend does not currently understand: `shouldExpandOrganisationSettings` takes only `hash` and knows three organisation anchors. It grows the same signature its Advanced sibling already has — `(search, hash)` — accepts `org=1`, and scrolls to the card whose id is the one the backend's constant already names. The group's existing `waitForQueryKeys` gate handles the rest: the anchor lives behind the body's spinner.

One wrinkle is specific to the same-tab path. A **failed** pre-flight returns to `/login?sso=failed`, and the operator is still signed in, so the router sends a signed-in session at `/login` to the dashboard — losing the card. With a marker in flight, that redirect target becomes the organisation deep link instead. It is one pure helper and one `Navigate` target, and without it the most likely failure (a mistyped issuer, on a browser that blocks windows) ends by dumping the operator on the dashboard with no explanation.

### The turn-on control is armed by this card's own round trip, and by nothing else

`testLoginVerifiedAt` being non-null is **not** sufficient and the UI must not treat it as such: the proof is bound to `test_login_user_id`, which the API deliberately withholds, so a colleague's fresh proof looks identical and the enable would answer `409`. The arming fact is therefore local: this card started a flow, and a re-read afterwards shows the stamp strictly newer than the value observed at the start. A `null` observed value is handled by the same comparison, which matters because writing the connection clears the stamp.

It is held in memory and is cleared by three things: writing the connection (the server clears the proof, so the control must follow), a successful enable (the server *spends* the proof, so off-then-on needs a new one), and a reload. The reload case is a deliberate fail-closed: the stored proof may well still be live, but the interface cannot tell whose it is, and offering a control the server will refuse is worse than one extra test login.

The server stays the authority, which is why the `409` path is a rendered refusal and not an impossible state. Two admins racing, a proof that expires between the re-read and the click, and a connection written from another tab all produce it, and all of them read the same: run the test login again.

### Steps (a) and (b) are one write, so an abandoned wizard leaves one of two states

The backend refuses partial `config` writes on purpose — a write that kept the old secret while repointing the issuer would send the operator's credential to a server they had not yet typed it for — and the pre-flight runs against the row **as stored**, `404`ing when nothing is stored. That fixes the order: the connection and the claim names are one `PATCH` issued when the operator leaves step (b), and step (c) cannot run before it.

So there is no half-configured provider to design for. Closing the dialog leaves either **nothing written** — the row is untouched, the card still invites — or **a stored connection on a row that is still off**, which is inert: it is not in the login hint, no button appears on any login screen, and nobody can sign in through it. The card says which of the two it is and re-opening resumes at the test login. The one thing that does not survive is the client secret, and it cannot: the server returns it masked and requires it in full on every connection write. Resuming the pre-flight needs nothing; *editing* the connection means typing it again, and the dialog says so rather than letting an operator discover it at the refusal.

The card re-reads the row after every write rather than assuming what the write left behind. There is an open review thread on #2411 about whether editing the connection of an *enabled* row should refuse or auto-disable; today it silently repoints with the proof cleared. Rendering from the re-read row means all three behaviours are already drawn correctly.

### The reference code is the server's masking of an address the browser itself just presented

The plan asks the pending screen to show "provider + masked e-mail reference code" so an administrator can find the matching refusal. Nothing in the browser can produce it: the callback clears the flow cookie, mints no session, and passes no parameter, and the address exists only in the `login_failed reason=unknown_identity` audit row behind `audit:read`. Inventing one client-side would be worse than showing none — it would not match what the administrator sees.

So the refusal redirect sets one sealed marker cookie, in the shape the flow cookie already uses (`TokenEncryptor`, `HttpOnly`, `SameSite=Lax`, `Secure` on HTTPS, ten minutes, path-scoped), carrying the provider row's id and `mask_email(email)` — `a***@example.com`, the same projection the accounts API already applies. Every other destination clears it. The session response projects it as `login.pending_arrival = {provider, reference}`, resolving the label from the row, and sets `pending_identity` true for that browser — which makes `AuthGate`'s existing condition match.

**The refusal with no address is the one that needed the URL-only clause widened.** An identity provider that asserts no e-mail leaves no marker — there would be no reference to quote, and the refusal row it would be matched against carries no address either — so the session that follows reports `pending_identity: false` and the screen the server redirected to would have rendered the login form instead. The fix is on this side of the wire, not the backend's: the clause now admits `/auth/pending` wherever a sign-in method that sends a browser away and back exists (a `trusted_header` install, or a hint entry carrying a `login_url`), and nowhere else. Minting a reference-less marker for it was rejected. The marker exists to carry the reference; one that carried nothing would be a cookie whose only job is to decide which screen renders, and the URL already does that job — more robustly, since it survives a blocked cookie, a ten-minute expiry and a **Try again** after it. And it is safe to honour, because the screen it draws without a marker names no provider, no address and no account: the disclosure the marker protects is the reference, and there is none. A password-only install still answers that URL with the login screen, since nothing there could have redirected anybody.

**Why this discloses nothing.** The value is a lossy function of an identity this browser just presented at the identity provider; it returns less than the caller supplied. It is bound to that one browser by a seal it cannot read or forge and expires in ten minutes, so it cannot be requested for an address of somebody's choosing. It is emitted precisely when **no** account matched, so it asserts no account exists — and it carries no subject, no groups, no claim name, no issuer and no address in clear. The provider's label is already public: it is the login button's text.

Two rejected alternatives are worth recording. The audit row's id would be a perfect reference and is refused: a monotonic integer handed to an unauthenticated browser leaks the audit log's size and rate. A query parameter on the redirect is refused too — it would put the masked address in the browser history, the `Referer` and every access log on the way, which is strictly worse than a sealed cookie for the same benefit.

The match is completed on the administrator's side: the refused sign-ins sheet renders the same masked form beside the full address it already shows, computed by a pure helper that mirrors the server's rule. The person quotes `a***@example.com`, the administrator finds that exact string in the sheet. Two refusals from the same domain with the same initial mask identically; the person also knows when they tried, the sheet shows timestamps, and that is enough. It is a reference, not an identifier, and making it unique would mean handing out something that is.

### The card sits first, and the "no company sign-in method" sentence retires

The plan's card order for the group is (a) login methods — OIDC card, then reverse-proxy card — then (b) rules, then (c) login policy. The company sign-in card therefore mounts first, and it renders whenever the group does: unlike the reverse-proxy card it is not describing a deployment topology that may or may not exist, it is the thing every install can now do. That makes the body's current `provider === null` branch — one sentence saying there is no company sign-in method to configure — false on the day this ships. It becomes a neutral line about the reverse-proxy alternative, which is what the plan asks for anyway: a solo install behind Authelia must not read its own layout as a misconfiguration.

### The collapsed line names the provider from the login hint, not from `access_summary`

The plan's shape is "Okta connected · …". `access_summary.providers_enabled` carries provider **kinds**, never labels, and the first test in the group's file pins that a collapsed group issues no request — so the label cannot come from either. It comes from `loginHint.providers`, which the auth store already holds for every caller and which lists exactly the active rows: zero requests, zero new disclosure, and it is the same string the login button shows.

The label is named only when exactly one non-password provider is active; with two the sentence stays unnamed rather than guessing which one is meant. The selection ladder itself is unchanged and still driven by `access_summary`, so a session without `users:manage` still falls closed to the plain one-liner.

The banned-word rule needs one clarification it did not need before, because an operator may type "SSO" as their label: the ban governs the copy this product writes. A label the operator chose is quoted verbatim and is not rewritten, abbreviated or decoded — the alternative is a product that edits the customer's own name for their own system, which is worse than a word.

### Rules cards do not follow the provider yet, and this change says so rather than promising it

`rulesOf` filters by the provider's kind and key and the body passes it the trusted-header row, so the one rules card the group renders holds that row's rules and writes that row's `provider`/`providerKey`. An install whose only company sign-in is OIDC therefore finishes the wizard with a provider that refuses everybody (the seeded `unknown_identity_role_id` is NULL, by design) and no surface anywhere to write the rule that admits them. That is a real gap and the right shape for it is known — one card per company sign-in provider with a stored connection, in the same order as the sign-in cards above them, each naming its provider, with the refused sign-ins header on the first only, because the audit read behind it filters on action and reason and **not** on provider, so repeating it would show the same number twice and imply a per-provider count nobody asked for.

None of that is in this change. It is a second concern with its own write path and its own tests, and the `Group-to-role rules card` requirement here is therefore the one `role-mappings-and-organization-group` already states — a single card over the reverse-proxy row's rules — plus the one thing this change really does to it: the sheet now shows each entry's masked address beside the address itself, so the reference the pending screen hands a refused person matches an entry as text. A follow-up change carries the rest; a requirement written now would be a promise the code does not keep.

### The step-up dialog's identity-provider branch arrived with the backend, and is not restated here

`account_step_up_methods` returns `["oidc"]` for an account holding neither a password hash nor a TOTP secret — exactly the administrator an OIDC install provisions, and exactly the person who then needs to operate this card. Without a branch for it the dialog would render an empty form whose Confirm `POST`s an empty body to an endpoint that can only answer `403 step_up_unavailable`.

#2411 closed that itself: `needsProvider` renders no field and no body submit and offers one re-authenticate control, and `oidc-provider-backend`'s "Re-authentication at the identity provider is a step-up factor for an account that has no other" is the requirement it keeps. Its shape is deliberately **not** this change's window flow. The dialog settles the interrupted request as *not verified* and navigates the whole page to the authorization URL, because nothing can replay a request across a page load; the person repeats the action after the callback returns them, inside the step-up's five-minute stamp. A second copy of that requirement here — describing a popup, a refreshed session and a replayed original — would contradict both the merged spec and the merged code, so this change states nothing about the dialog and does not touch the file.

### Everything URL-derived is a pure helper, and everything pure lives in `rules.ts`

`?sso=failed`, `?org=1#oidc`, the in-flight marker, the masking mirror, the provider selectors and the arming comparison are all pure functions with their own unit tests, following the module the group already keeps for exactly this. The login form still renders without a router because the gate computes what it needs and passes props, which is what lets it be tested in a bare `render()`; that stays true.

## Risks / Trade-offs

- **This is three surfaces plus a backend carrier in one PR, against a ~650-line budget, and PR-3a-1 ran nine times its own.** What shipped is the split: the card and its dialog, the login screen's notice, the pending screen and its carrier. The step-up branch turned out to be #2411's and is not restated here; the per-provider rules cards are deferred to a follow-up, at the stated cost — an OIDC-only install still has no surface on which to write a rule, and until it has one an operator who connects a provider has connected one that admits nobody. The pending carrier is not separable from the pending screen; it could only move to #2411.
- **A fail-closed arming rule costs a second test login** after a reload or after an enable. The alternative is offering a control the server refuses; the `409` is rendered either way, so the cost is one round trip and the benefit is that the disabled state never lies.
- **The reference is not unique.** Same domain, same initial, same mask. Accepted deliberately over a unique identifier that would have to be either the audit row's id (leaks the log's size to an unauthenticated browser) or a new opaque column.
- **The marker cookie is a new unauthenticated-response surface**, small as it is. It is sealed, `HttpOnly`, ten minutes, path-scoped, cleared by every other destination and by logout, and carries two values, one of them already masked.
- **The label in the collapsed summary is operator-controlled text.** It is rendered as text by React and interpolated by i18next, which escapes; the banned-word test asserts against the product's copy, and a second test pins that the label is not rewritten.
- **i18n parity is exact key-set equality across `en`, `ko` and `zh-CN`** (1,986 keys today, flat and dotted, plurals counted separately). A wizard's worth of strings lands in all three in this commit or the suite goes red.
- **`handler-coverage.test.ts` fails in both directions**: the two OIDC starts need an MSW handler *and* an `EXPECTED_ENDPOINTS` entry.
- **The screenshot harness waits for `input[type="password"]` on the login capture.** A fixture with a company provider under `break_glass_only` has no password input and would hang it; the capture fixture keeps the local form.
- **Fixtures are credential-shaped by nature.** Client ids and secrets in tests are assembled from parts and pairs are expressed as lists, following the backend's own convention, because GitGuardian is a required check and reads `<identity>:<secret>` strings and two-tuples as credentials.
