## ADDED Requirements

### Requirement: Company sign-in connect wizard

The Organisation group SHALL render a company sign-in card (`id="oidc"`) for the OIDC provider row — the one the provider seeder writes, disabled, on every install — as the first of its child cards, above the reverse-proxy card, because connecting a company identity provider is the login method the group exists for. It SHALL be gated on nothing beyond the group's own `security:write`: a session without that permission SHALL learn nothing about it — no card, no disabled placeholder, no "coming soon", and no `GET /api/auth-providers` — and a session that holds it SHALL see the card whether or not a reverse-proxy row exists, so an install with no proxy is no longer told there is nothing here to configure.

With no connection document stored the card SHALL read as an invitation — **Connect company sign-in** — and SHALL NOT assert that nothing was ever set up, because the API returns the same empty `config` map for a row that was never configured and for one whose sealed document will not decrypt.

The control SHALL open a dialog **inside the card** — not a first-run flow, not a route, not a nav item — with three steps in this order: **(a)** the connection: issuer, an optional discovery URL, client id, client secret and the redirect URI this install serves, offered pre-filled from the running origin and the callback path and copyable, because the operator has to paste it into the identity provider; **(b)** the claim names: subject, e-mail, name and groups, each optional and each showing the default it falls back to; **(c)** the test login. It SHALL ask for nothing the backend does not store.

Steps (a) and (b) SHALL be written as **one** `PATCH /api/auth-providers/{id}` carrying the whole `config` document, issued when the operator leaves step (b) and not before: the server refuses a partial write, and step (c) runs against the row as stored. The dialog SHALL apply the server's own field rules before it sends — required fields, `https`, the redirect URI's callback suffix, the length bounds — because everything the request model rejects comes back as `422 validation_error` with no `param` and cannot be attached to a field. It SHALL apply them to the values it will actually send: a field the dialog trims away to nothing SHALL be refused inline as missing rather than sent as the empty string the request model refuses without naming it, and surrounding space the dialog strips on its own SHALL NOT be a refusal. The client secret is the exception in both directions, because it is the one field sent verbatim.

The client secret SHALL be required on every connection write and typed by the operator each time. The masked value the API returns SHALL be shown only as evidence that a secret is stored; it SHALL NEVER be placed in an input, sent back to the server, written to browser storage, or put in a URL, and no draft of this dialog SHALL be persisted anywhere. The typed value SHALL NOT outlive the connection write it was typed for in any state the dialog does not itself own — in particular the request state the group's shared mutation retains after it settles, which the page keeps long after the dialog is gone.

Closing the dialog SHALL leave the server in one of exactly two states and the card SHALL name whichever one it is: **nothing written**, when the operator left before step (b) completed, or a **stored connection on a row that is still off**, which admits nobody and appears on no login screen. Re-opening SHALL resume at step (c) without asking for the secret again, and SHALL require it again only when the operator edits the connection itself.

Every refusal SHALL be surfaced inline, on the control it belongs to, through the group's explained-error vocabulary rather than the raw server message: `invalid_provider_config` on the field its `param` names, mapping the server's snake-case name to the field the form shows; `config_not_supported` and `insufficient_delegation` on the dialog; `oidc_provider_unreachable` on the test-login control that provoked it, in words that name the issuer and discovery addresses as the thing to check, because a pre-flight that cannot fetch the discovery document is telling the operator about the URL they just typed — it is answered by the start rather than by the write, so it arrives on the step the connection fields are no longer on. A refusal SHALL leave every narrowing control — turning the provider off — available, since the server never gates that direction.

#### Scenario: Empty state invites rather than asserts

- **GIVEN** an admin holding `security:write` on an install whose OIDC row has no stored connection
- **WHEN** the Organisation group is expanded
- **THEN** the first card offers "Connect company sign-in" and says nothing about whether anything was ever configured

#### Scenario: The connection is one write

- **WHEN** the operator completes steps (a) and (b) and continues to the test login
- **THEN** exactly one `PATCH /api/auth-providers/{id}` is sent, its body is `{config: {…}}` carrying every field of the document including `clientSecret`, and no request was sent while step (a) was open

#### Scenario: The dialog judges the value it will send

- **WHEN** the operator types a client id of nothing but spaces
- **THEN** the client id is marked missing inline and no request is sent
- **AND** an issuer, an optional discovery URL or a claim name typed with surrounding space is accepted and sent trimmed

#### Scenario: The typed secret does not outlive its write

- **WHEN** the connection write settles, either accepted or refused, and the wizard moves on
- **THEN** the client secret is in no request state the page keeps after the dialog closes, and in no part of the document, its URL or browser storage

#### Scenario: The stored secret is never handed back

- **GIVEN** a row whose `config.clientSecret` comes back masked
- **WHEN** the operator re-opens the dialog to edit the connection
- **THEN** the issuer, discovery URL, client id, redirect URI and claim names are pre-filled, the secret field is empty and marked as required again, and the masked value appears only as text
- **AND** no request, storage entry or URL in the interaction contains the masked value

#### Scenario: Closing before the connection is saved writes nothing

- **WHEN** the operator fills in step (a) and closes the dialog
- **THEN** no request was issued and the card still offers "Connect company sign-in"

#### Scenario: Closing after the connection is saved leaves an inert row

- **WHEN** the operator saves the connection and closes the dialog without running the test login
- **THEN** the card says the connection is saved and not turned on yet, and offers the test login
- **AND** re-opening the dialog resumes at the test login without asking for the secret

#### Scenario: A field-named refusal lands on its field

- **WHEN** the connection write answers `422 invalid_provider_config` with `param: "redirect_uri"`
- **THEN** the message is rendered against the redirect URI field, the explained sentence is shown instead of the raw server message, and the dialog stays open on step (a)

#### Scenario: An unreachable identity provider is the addresses' problem

- **WHEN** the test login start answers `502 oidc_provider_unreachable`
- **THEN** the explanation is rendered where the test login was run, names the issuer and discovery addresses as what to check, and is shown instead of the raw server message
- **AND** no browser window is left open, the turn-on control stays disabled, and nothing suggests the operator's account lacks permission

#### Scenario: A delegation refusal explains itself and leaves the way back

- **GIVEN** a custom `security:write` role that cannot delegate every administrator grant
- **WHEN** the connection write answers `403 insufficient_delegation`
- **THEN** the dialog explains that changing the connection needs an account holding every administrator permission, and does not echo the server message
- **AND** the card's control for turning the provider off stays available

#### Scenario: A session without security:write learns nothing

- **GIVEN** a signed-in session that does not hold `security:write` on an install with a connected identity provider
- **WHEN** Settings renders
- **THEN** no Organisation group, no company sign-in card and no mention of a provider is rendered, and no `/api/auth-providers` request is issued

### Requirement: The pre-flight test login arms the turn-on control

The company sign-in card's **Turn on** control SHALL be disabled until a test login started from this card has been observed to succeed, and the card SHALL say that in plain words rather than leaving a dead control. The backend spends the proof on the enable and binds it to the account that earned it, so the card SHALL treat its own completed round trip as the only arming event and SHALL NOT arm from a non-null `testLoginVerifiedAt` alone, which may be another administrator's proof.

Starting a test login SHALL open a browser window **synchronously in the activation handler**, before `POST /api/dashboard-auth/oidc/test-login/start` is awaited, and point it at the returned authorization URL once the answer arrives; a start that is refused SHALL close that window and render the refusal inline. When the browser refuses the window, the flow SHALL continue in the same tab: it SHALL record an in-flight marker carrying the provider id, the purpose and the `testLoginVerifiedAt` observed before starting — and nothing else, never the connection document and never the secret — and then navigate the tab to the authorization URL. There SHALL be no caller-supplied return URL: the server returns the browser to the settings page it chose, and the frontend SHALL honour that destination by expanding the Organisation group for `?org=1` and scrolling to the card for `#oidc` through the existing deep-link helper, which SHALL take the query string as well as the hash.

Completion SHALL be reported by the application to itself. A window that lands on the server's settings or failure destination SHALL announce that its round trip ended — to its opener at its own origin whenever it still has one, and on a same-origin broadcast channel in every case — and SHALL close itself before the settings page paints when it still has an opener to close for. The opener SHALL accept the posted message only from the window it opened and only from its own origin; the broadcast needs no such check, because nothing outside this application can address the channel. Neither signal SHALL carry a verdict. The card SHALL take the verdict only from a fresh `GET /api/auth-providers`, arming exactly when `testLoginVerifiedAt` has advanced past the value it observed when the flow started.

The broadcast is required because the opener link is not guaranteed to survive the trip. An identity provider serving `Cross-Origin-Opener-Policy: same-origin` on its authorization endpoint — Entra ID, one of the providers this wizard exists to connect, has shipped exactly that — severs it when the window commits that page, and it does not come back when the window returns to this origin. On such a provider the opener's handle reports the window closed within half a second of the start, while the person is still signing in, and the returning document has no opener to post to. **A handle reporting itself closed SHALL therefore be a reason to re-read and never the end of the flow**: the poll SHALL stop (the handle proves nothing from then on, whichever happened) while the return signals and the deadline stay in place, and the card SHALL NOT say the test login did not finish on that signal alone. The flow's own ten-minute lifetime SHALL end the watch — past it the server has nothing left to complete — so an abandoned flow ends in a re-read rather than in a spinner that never resolves or a poll that outlives the page.

The arming fact SHALL live in memory for the interaction that earned it. It SHALL be cleared by writing the connection, by a successful enable, and by a reload — after which the control is disabled again even though the stored proof may still be inside its ten-minute window — because the API deliberately does not say whose proof it is and a control the server will refuse must not be offered. The refusal SHALL nevertheless stay renderable: `409 oidc_test_login_required` SHALL be explained inline on the turn-on control as "run the test login again", never as a raw message. `429 oidc_rate_limited` SHALL be explained with its `Retry-After` wait and SHALL NOT be retried automatically, because the budget it spends is shared with the public sign-in start and a retry loop would lock the operator out of signing in.

#### Scenario: A test login opens a window and the card waits for the server's word

- **GIVEN** a saved connection on a row that is off
- **WHEN** the operator runs the test login
- **THEN** a window is opened in the activation handler before the start request is awaited, and it is pointed at the authorization URL the start returned
- **AND** the turn-on control stays disabled until a re-read of the provider row shows `testLoginVerifiedAt` newer than the value observed at the start

#### Scenario: The window is blocked

- **GIVEN** a browser that refuses the window
- **WHEN** the operator runs the test login
- **THEN** the in-flight marker is recorded and the current tab is navigated to the authorization URL
- **AND** on returning to the settings destination the Organisation group is expanded, the card is scrolled to, the marker is consumed once, and the control is armed from the re-read row

#### Scenario: The window is closed without finishing

- **WHEN** the operator closes the window at the identity provider
- **THEN** the card re-reads the provider row, finds `testLoginVerifiedAt` unchanged and keeps the turn-on control disabled
- **AND** it does not say the test login failed on that alone, because a handle reporting itself closed is exactly what an identity provider's opener policy produces on a window that is still open
- **AND** a window left standing at the identity provider ends the same way once the flow's ten minutes are up — with the card saying the test login did not complete — rather than being watched for as long as the page lives

#### Scenario: An identity provider that severs the opener still arms the control

- **GIVEN** an identity provider serving `Cross-Origin-Opener-Policy: same-origin` on its authorization endpoint, so the opener's handle reports the window closed half a second after the start and the returning document has no opener
- **WHEN** the operator completes the sign-in and the window returns to the settings destination
- **THEN** nothing in between says the test login failed, the return arrives on the broadcast channel, and the card arms the turn-on control from the re-read row on that same attempt

#### Scenario: A failed test login says only that it failed

- **GIVEN** a test login the identity provider or the callback refuses, which the server ends at its one failure destination
- **WHEN** the window returns
- **THEN** the card says the test login did not complete and offers to run it again, and invents no reason
- **AND** a same-tab return lands back on the card rather than on the dashboard

#### Scenario: Editing the connection disarms the control

- **GIVEN** a card armed by a successful test login
- **WHEN** the operator saves any change to the connection
- **THEN** the turn-on control is disabled again and the card re-reads the row rather than assuming what the write left behind

#### Scenario: Turning on spends the proof

- **GIVEN** an armed card
- **WHEN** the operator turns the provider on and the write succeeds
- **THEN** the card shows the provider as on, the arming fact is cleared, and turning it off and on again offers the test login first

#### Scenario: A reload fails closed

- **GIVEN** a successful test login and a page reload before the provider was turned on
- **WHEN** the card renders
- **THEN** the turn-on control is disabled and the card asks for a test login, without claiming the earlier one did not happen

#### Scenario: Enabling without a fresh proof is explained

- **WHEN** the enable write answers `409 oidc_test_login_required`
- **THEN** the card renders the explained sentence against the turn-on control, the switch still shows the provider as off, and the raw server message is not shown

#### Scenario: A rate-limited start is not retried

- **WHEN** the test login start answers `429` with `Retry-After`
- **THEN** the card names the wait, issues no further start on its own, and no window is left open

### Requirement: Company sign-in on the login screen

When a provider other than the local password is active, the login screen SHALL offer it first, above the local password block, as "Continue with `<label>`" linking to the start path the session hint gives for that row. The label SHALL be the operator's own `label` from the provider row, rendered verbatim: no surface SHALL derive a name from the provider kind, invent one, or decorate it. A row the hint gives no start path for SHALL be a sentence rather than a control, because a button the backend cannot honour is worse than none. The local password form SHALL keep following the login policy exactly as it does today, and the presence of a company sign-in button SHALL change nothing about that rule.

The screen SHALL read the sign-in failure marker the server redirects to and SHALL show one neutral notice that the sign-in did not complete, with no reason: the server collapses every cause into one marker precisely so an unauthenticated caller cannot tell them apart, and the screen SHALL NOT enrich it. Whether the URL carries that marker SHALL be decided by a pure helper over `location.search` with its own unit test, so the login form keeps rendering without a router. Nothing on this screen SHALL name an account, state that an account does or does not exist, or reveal the issuer, the client id or any claim name — the label is the one fact about the connection a signed-out browser may see, because it is what the button says.

#### Scenario: The company button comes first and is labelled by the row

- **GIVEN** a session hint listing an active provider labelled by the operator alongside the local password
- **WHEN** the login screen renders
- **THEN** the "Continue with `<label>`" control precedes the local password block in the document, uses the label exactly as the row carries it, and links to the start path from the hint

#### Scenario: A provider with no start path is a sentence

- **GIVEN** an active provider whose hint entry carries no start path
- **WHEN** the login screen renders
- **THEN** it renders an explanatory sentence naming the provider and no control

#### Scenario: A refused company sign-in explains nothing

- **WHEN** the browser returns to the login screen carrying the sign-in failure marker
- **THEN** one neutral notice says the sign-in did not complete, no cause is named or implied, and no account name appears anywhere on the screen

#### Scenario: The local form still follows the policy

- **GIVEN** `login.localLogin` is `break_glass_only` and a company provider is active
- **WHEN** the login screen renders at `/`
- **THEN** the company sign-in control is rendered and no password field or reveal link is
- **AND** the same screen at `/login?local=1` renders the password field beneath the company control

## MODIFIED Requirements

### Requirement: Organisation settings group

The Settings page SHALL render a collapsed **Organisation** group (`id="organisation"`) after the Advanced settings group, reusing the Advanced group's component so that collapsing genuinely unmounts its children. It SHALL render for every session holding `security:write` — this is where a single-person install first meets the company-login machinery, so the one line is the discovery surface and is drawn before anything is configured. Visibility SHALL be derived from the permission alone, a fact the session already carries: it SHALL NOT depend on `accessSummary`, which is absent for a principal without `users:manage`, and SHALL NOT depend on any request, because none may be issued while the group is collapsed.

While nothing is configured the collapsed line SHALL read as one plain sentence about what the group is for — "Organisation — company login, automatic account management, audit export" — and SHALL NOT contain the words user, role, SSO, SCIM, IdP or RBAC, in any casing. Once `accessSummary` reports something configured (a non-password provider, role mappings, custom roles, SCIM tokens, audit sinks, or a tightened local login policy) the same line SHALL become a status summary of what is on; the summary SHALL name features, not role names, and SHALL obey the same word ban. When exactly one non-password provider is active the summary SHALL name it — "`<label>` connected" — taking the label from the session's own login hint, which every caller already holds, so the collapsed group still issues no request; with none or several it SHALL keep the unnamed sentence. The word ban governs the copy this product writes: a label the operator typed is quoted verbatim and SHALL NOT be rewritten, abbreviated or decoded. The selection ladder itself SHALL stay driven by `accessSummary`, so a session without `users:manage` still falls closed to the plain sentence.

Expanding SHALL take one interaction and SHALL mount the child cards in order: company sign-in card, reverse-proxy card, group-to-role rules, then login-policy card. The company sign-in card and the login-policy card SHALL render whenever the group does, because every install can connect a company identity provider and every install has a local sign-in — the install with no reverse proxy is exactly the one whose operator needs both. The reverse-proxy card SHALL render only when a reverse-proxy row exists; in its place the group SHALL show one neutral line naming the reverse-proxy alternative, which SHALL NOT imply that anything is missing or misconfigured. A `#organisation` hash SHALL expand the group and scroll to it, through the existing deep-link helper; the helper SHALL additionally accept the destination the sign-in flow returns to — `?org=1` expands the group and `#oidc` scrolls to the company sign-in card — so a completed pre-flight or re-authentication comes back to the card that started it rather than to a collapsed page.

The roles both cards offer SHALL be read from `GET /api/role-mappings/assignable-roles` — the read gated by the same `security:write` that gates this group — and NOT from the `users:manage` roles list, so a session holding `security:write` without `users:manage` can still name and choose what its rules hand out. That list already contains exactly the roles the caller may delegate, so the group SHALL NOT filter it again against session state (`assignableRoleIds` is empty for such a session) and SHALL NOT issue the `users:manage` roles request.

The group's trigger SHALL have its own accessible name; the Advanced group's trigger name SHALL be unchanged by the component becoming reusable.

#### Scenario: Nothing configured

- **GIVEN** a signed-in admin holding `security:write` on an install where none of this is configured (no non-password sign-in method is active and there are no rules)
- **WHEN** the Settings page renders
- **THEN** the Organisation group shows one collapsed line reading "Organisation — company login, automatic account management, audit export"
- **AND** that line contains none of the words user, role, SSO, SCIM, IdP or RBAC

#### Scenario: Something configured

- **GIVEN** the same install after one group-to-role rule exists
- **WHEN** the Settings page renders
- **THEN** the collapsed line summarises what is on (company login set up, N sign-in rules) and still contains none of the banned words

#### Scenario: A connected provider is named in the summary

- **GIVEN** an install whose only non-password provider is active and labelled by the operator
- **WHEN** the Settings page renders for an admin
- **THEN** the collapsed line names that label as connected, alongside what else is on, and still contains none of the banned words
- **AND** no request is issued while the group is collapsed

#### Scenario: No reverse proxy to configure

- **GIVEN** an install whose provider rows contain no reverse-proxy row
- **WHEN** an admin expands the group
- **THEN** the company sign-in card and the login-policy card are rendered and the reverse-proxy card is not
- **AND** the line that stands in for it is neutral about the reverse proxy and does not suggest the install is incomplete

#### Scenario: Permission gate

- **GIVEN** a session without `security:write`
- **WHEN** Settings renders on a reverse-proxy install
- **THEN** no Organisation group is rendered and no organisation request is issued

#### Scenario: security:write without users:manage still sees the group

- **GIVEN** a session holding `security:write` whose `accessSummary` is absent
- **WHEN** Settings renders on a reverse-proxy install
- **THEN** the Organisation group renders with the unconfigured one-liner

#### Scenario: security:write without users:manage can still choose a role

- **GIVEN** the same session, whose `assignableRoleIds` is empty and which may not read the roles list
- **WHEN** it expands the group
- **THEN** the role pickers are enabled and offer exactly the roles `GET /api/role-mappings/assignable-roles` returned, and the rules and provider controls are editable

#### Scenario: Deep link expands the group

- **WHEN** the person opens `/settings#organisation`
- **THEN** the group is expanded and scrolled into view, and the Advanced group stays collapsed

#### Scenario: The sign-in flow's own return URL opens the card

- **WHEN** the browser returns to `/settings?org=1#oidc`
- **THEN** the group is expanded and the company sign-in card is scrolled into view once the group's queries have settled

### Requirement: Pending identity screen

`/auth/pending` SHALL be a public route rendered by `AuthGate` before the login branch. It SHALL show "Your account is not ready yet", explain that the sign-in method recognised the person but the dashboard has no account for them, and offer **Try again** (refreshes the session) and **Logout**; when `login.providers` lists `password`, `localPasswordConfigured` is true and `login.localLogin` is not `break_glass_only` it SHALL also render the local login form so the break-glass admin can sign in behind the proxy; under `break_glass_only` it SHALL instead render the same link to `/login?local=1` the login screen uses, so the policy has exactly one door and the pending screen cannot become a second one. `AuthGate` SHALL render the same screen — and navigate to `/auth/pending` — whenever the session reports `login.pendingIdentity` for an unauthenticated caller, except on other public routes (`/invite/:token`, `/login…`), which keep their URL. The URL alone SHALL render the pending screen wherever a sign-in method that sends a browser away and back again exists — a `trusted_header` install, or a provider the session's own login hint gives a start path for — and nowhere else, so an install with only the local password keeps answering that URL with the login screen. It has to be admitted there, because a company sign-in refused for an identity the provider asserted no address for leaves no marker at all (there would be no reference for the person to quote): the session that follows reports no pending identity, and the destination the server chose is then the only thing that knows what happened. Admitting it discloses nothing — without a marker the screen carries only its general copy, which names no provider, no address and no account, and states nothing about whether any account exists. The router SHALL map `/auth/pending` to the dashboard for an authenticated session so a retry after provisioning lands in the app.

When the session carries the refused arrival (`login.pendingArrival`), the screen SHALL name the provider the person arrived through and SHALL show the reference the server computed from the identity that was refused, as a short line the person can read out or copy, labelled as the thing to give an administrator. The screen SHALL render that reference verbatim and SHALL NOT compute, complete or embellish it: the masking rule is the server's, and the same rule applied to the full address in the refused sign-ins sheet is what lets an administrator match the two by eye. With no such block — a reverse-proxy arrival, a marker that has expired, or an identity provider that asserted no address — the screen SHALL fall back to today's copy and SHALL NOT guess which provider refused the person, even when exactly one is active. Nothing on this screen SHALL state or imply whether any account exists, name another account, or reveal the issuer, the client id, a claim name, the subject or the groups the identity provider asserted.

#### Scenario: Refused proxy identity

- **WHEN** the session response carries `login.pendingIdentity: true` and `authenticated: false`
- **THEN** the pending screen renders instead of the login form and Try again refreshes the session
- **AND** with `localPasswordConfigured` true the local login form renders beneath it

#### Scenario: Refused company sign-in names the provider and the reference

- **GIVEN** a browser returned to `/auth/pending` after the identity provider authenticated somebody the resolver refused
- **WHEN** the session response carries `login.pendingIdentity: true` with a `pendingArrival` block
- **THEN** the pending screen renders with the provider's label and the server's reference, presented as the thing to give an administrator
- **AND** the screen names no account, no issuer, no claim and no subject

#### Scenario: An administrator can find the matching refusal

- **GIVEN** the refused sign-ins sheet listing the same refusal
- **WHEN** an administrator opens it
- **THEN** each entry shows the same masked form of its address beside the address itself, so the reference the person quotes matches one entry exactly as text

#### Scenario: An expired marker degrades to the plain screen

- **GIVEN** a pending screen opened after the arrival marker has expired
- **THEN** the screen renders its general copy with no provider name and no reference, and does not infer either from the active providers

#### Scenario: A refusal with no address still lands on the pending screen

- **GIVEN** a standard-mode install whose company sign-in is active, and a browser the server returned to `/auth/pending` after refusing an identity the identity provider asserted no address for — so no marker was set and the session that follows reports `pendingIdentity: false`
- **WHEN** the app loads at that URL
- **THEN** the pending screen renders with its general copy, no reference is shown and the provider is not named
- **AND** the same URL on an install with no sign-in method that redirects a browser renders the login screen instead

#### Scenario: Public routes are not redirected

- **WHEN** a pending-identity session opens `/invite/abc123`
- **THEN** the invite screen renders and the URL is unchanged

#### Scenario: Signed in on the pending route

- **WHEN** an authenticated session opens `/auth/pending`
- **THEN** the router redirects to `/dashboard`

#### Scenario: The pending screen is not a second door

- **GIVEN** `login.localLogin` is `break_glass_only` and a local password is configured
- **WHEN** the pending screen renders
- **THEN** no password field is rendered and a link to `/login?local=1` is offered instead

### Requirement: Group-to-role rules card

Inside the Organisation group, the group-to-role rules card SHALL list the rules of the reverse-proxy provider from `GET /api/role-mappings` in priority order, the winner first, each row showing what it matches (a group name, or an e-mail domain) and the role it gives, with edit and delete actions. The order SHALL be changeable with keyboard-reachable Move up / Move down controls (pointer dragging MAY be offered in addition), and a reorder SHALL be written as one `PUT /api/role-mappings/order` carrying the whole order, so a cancelled interaction never leaves a partial order. Adding a rule SHALL use the shared role picker.

With no rules the card SHALL show one honest empty state that says what actually happens today: "Everyone arriving through company login is refused." when the provider refuses unknown arrivals, and "Everyone arriving through company login is admitted as `<role>`." when it admits them (the shipped D10 default). It SHALL NOT promise a refusal the backend would not perform. Alongside it the card SHALL offer a one-click quick-add of "everyone at `<e-mail domain>` as `<role>`", the domain pre-filled from the domain most of the recent refusals came from — the only evidence the dashboard holds about who is knocking — and editable. Before the FIRST rule is saved the card SHALL state that accounts this login method created are re-evaluated the next time each person arrives, and that anyone matching no rule moves to the fall-back role shown on the reverse-proxy card.

The card header SHALL show "N refused sign-ins in the last 7 days" with a **view** action opening a sheet that lists those entries (when, which identity), counted from `GET /api/audit-logs` filtered on `action=login_failed`, `reason=unknown_identity` and the last seven days, whenever N is greater than zero. Each listed entry SHALL show the masked form of its address beside the address itself, using the same rule the server applies to the pending screen's reference, so the two can be matched as text. Because that read is gated on `audit:read`, a session without it SHALL see neither the line nor the action and SHALL NOT issue the request; the rules themselves stay editable. Neither the count nor the sheet SHALL be requested while the group is collapsed. `#organisation-refused` SHALL expand the group with that sheet already open.

#### Scenario: Empty state is honest

- **GIVEN** a reverse-proxy install with no rules whose provider admits unknown arrivals as Admin
- **WHEN** the group is expanded
- **THEN** the card reads "No rules yet." with "Everyone arriving through company login is admitted as Admin." and offers the quick-add pre-filled with the domain most of the recent refusals came from
- **AND** on a provider that refuses unknown arrivals the same card reads "Everyone arriving through company login is refused."

#### Scenario: Quick-add writes one rule

- **WHEN** the admin accepts the quick-add with the member role
- **THEN** one `POST /api/role-mappings` carries `claimName: "email_domain"` and that domain, and the row appears in the list

#### Scenario: First rule warns about re-evaluation

- **WHEN** the admin is about to save the first rule
- **THEN** the card states that existing accounts created by this login method are re-evaluated and that unmatched ones move to the fall-back role

#### Scenario: Reordering is one write

- **GIVEN** three rules
- **WHEN** the admin moves the last one up twice with the keyboard
- **THEN** the list shows the new order and exactly one `PUT /api/role-mappings/order` per interaction carries the full order

#### Scenario: Refused sign-ins with audit access

- **GIVEN** four `login_failed` rows with `reason=unknown_identity` in the last week and a session holding `audit:read`
- **WHEN** the card renders
- **THEN** the header shows "4 refused sign-ins in the last 7 days" with a view action that opens a sheet listing the four identities
- **AND** with no such rows the header shows no refusal line at all

#### Scenario: Refused sign-ins without audit access

- **GIVEN** the same rows and a session without `audit:read`
- **WHEN** the card renders
- **THEN** no refusal line is shown, no audit request is issued, and the rules stay editable
