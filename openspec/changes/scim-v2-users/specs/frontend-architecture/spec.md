## ADDED Requirements

### Requirement: Automatic account management card

The Organisation group SHALL render an automatic account management card, last, in the group's card shape (a `<section>` with its own anchor, the icon header, an `AlertMessage` slot beneath it, then the controls), taking its query, the group's provider rows, the group's shared mutations and the group's `disabled` flag as props and calling no permission hook of its own. It SHALL show the base URL to give the identity provider as a copyable monospaced fact, the tokens that exist by label, prefix and last sync, and controls to issue, rotate and revoke. The address and its copy control SHALL be drawn only once the credential list has answered with the base path, and SHALL be absent while that answer is pending and after it fails: the site origin on its own is not an endpoint any identity provider can reach, and a copy control that hands the operator one is worse than no address at all. Whether the card is usable SHALL be decided by a pure predicate in the organisation rules module with its own unit test, reading whether a sign-in provider other than the local password is **enabled** — from the provider rows the group already holds rather than from `accessSummary`, which is absent for a caller holding `security:write` without `users:manage` and would tell that caller nothing is connected when something is; a row that is stored but switched off does not count, because provisioning people into an install they cannot then sign in to is the state the gate exists to prevent. It SHALL NOT be a condition written inline in the card. Until one is, the issue control SHALL be disabled and the card SHALL state the reason in this product's words ("Connect company sign-in first to turn this on"); the card SHALL NOT be hidden, and it SHALL NOT draw a control the server would refuse. When a company sign-in provider is enabled but it does not link identities by e-mail, the card SHALL carry one advisory line saying that a person this surface provisions will get a second account on their first sign-in, next to the setting that changes it.

A newly issued or rotated secret SHALL be shown exactly once, in a dialog whose state is owned above any subtree the issue itself re-renders, because issuing the first token flips the disclosure tier and re-renders the group. The secret SHALL live in component state only: never in a query cache, in storage, in a URL, in a mutation's retained variables, or in an error. The mutation that returned it SHALL be reset as soon as the card has copied it into that state, rather than when the dialog is dismissed or by cache expiry: a settled mutation keeps its `data` for as long as anything observes it, and these mutations are owned by the group above the card, so a `gcTime` of zero only disposes of them on an unmount the dismissal may never reach. The list refetch and the session refresh that the group's shared settle performs SHALL be deferred until the dialog is dismissed. The card SHALL render a refusal where it was earned, translated from the group's explained-code set, and SHALL NOT render the raw server message for a code it explains. A `#organisation-automatic-accounts` hash SHALL expand the group and scroll to the card through the existing deep-link helper.

#### Scenario: Before a company sign-in provider exists

- **GIVEN** an install whose only enabled sign-in method is the local password
- **WHEN** an admin expands the Organisation group
- **THEN** the card is rendered with its issue control disabled and states that company sign-in must be connected first
- **AND** no credential is issued

#### Scenario: The secret is shown once and survives the tier flip

- **GIVEN** an install with no tokens, so the disclosure tier is not yet `enterprise`
- **WHEN** an admin issues the first token
- **THEN** the plaintext is shown once in a dialog that stays mounted while the group re-renders, with a copy control
- **AND** dismissing it refreshes the list and the session, and the plaintext is nowhere in the refreshed data

#### Scenario: The plaintext is in component state and nowhere else

- **WHEN** an admin issues a token and the dialog showing the plaintext is still open
- **THEN** neither the mutation cache nor the query cache holds the plaintext in its `data` or its retained variables

#### Scenario: The address waits for the answer that carries it

- **GIVEN** an install whose credential list fails to load
- **WHEN** an admin expands the group
- **THEN** the card explains that the list could not be loaded and draws neither the address nor its copy control

#### Scenario: Rotation replaces the secret in place

- **WHEN** an admin rotates a token
- **THEN** the new plaintext is shown once, the row keeps its label and its last sync, and the previous value is never rendered again

#### Scenario: A refusal lands on the card

- **GIVEN** a session holding `security:write` without the admin preset's grants
- **WHEN** it tries to issue a token
- **THEN** the card explains the refusal in this product's words, the raw server message is not rendered, and no dialog opens

#### Scenario: The card is gated by the group, not by itself

- **GIVEN** a session without `security:write`
- **WHEN** Settings renders
- **THEN** no Organisation group and no automatic account management card are rendered, and no token request is issued

## MODIFIED Requirements

### Requirement: Organisation settings group

The Settings page SHALL render a collapsed **Organisation** group (`id="organisation"`) after the Advanced settings group, reusing the Advanced group's component so that collapsing genuinely unmounts its children. It SHALL render for every session holding `security:write` — this is where a single-person install first meets the company-login machinery, so the one line is the discovery surface and is drawn before anything is configured. Visibility SHALL be derived from the permission alone, a fact the session already carries: it SHALL NOT depend on `accessSummary`, which is absent for a principal without `users:manage`, and SHALL NOT depend on any request, because none may be issued while the group is collapsed.

While nothing is configured the collapsed line SHALL read as one plain sentence about what the group is for — "Organisation — company login, automatic account management, audit export" — and SHALL NOT contain the words user, role, SSO, SCIM, IdP or RBAC, in any casing. Once `accessSummary` reports something configured (a non-password provider, role mappings, custom roles, SCIM tokens, audit sinks, or a tightened local login policy) the same line SHALL become a status summary of what is on; the summary SHALL name features, not role names, and SHALL obey the same word ban. When exactly one non-password provider is active the summary SHALL name it — "`<label>` connected" — taking the label from the session's own login hint, which every caller already holds, so the collapsed group still issues no request; with none or several it SHALL keep the unnamed sentence. The word ban governs the copy this product writes: a label the operator typed is quoted verbatim and SHALL NOT be rewritten, abbreviated or decoded. The selection ladder itself SHALL stay driven by `accessSummary`, so a session without `users:manage` still falls closed to the plain sentence.

Expanding SHALL take one interaction and SHALL mount the child cards in order: company sign-in card, reverse-proxy card, group-to-role rules, login-policy card, then the automatic account management card. The company sign-in card and the login-policy card SHALL render whenever the group does, because every install can connect a company identity provider and every install has a local sign-in — the install with no reverse proxy is exactly the one whose operator needs both. The automatic account management card SHALL render whenever the group does too, last, and SHALL be disabled with its reason stated until a company sign-in provider is enabled, rather than hidden: the collapsed line already promises automatic account management, so an install that cannot use it yet is owed the reason and not a silence. The reverse-proxy card SHALL render only when a reverse-proxy row exists; in its place the group SHALL show one neutral line naming the reverse-proxy alternative, which SHALL NOT imply that anything is missing or misconfigured. A `#organisation` hash SHALL expand the group and scroll to it, through the existing deep-link helper; the helper SHALL additionally accept the destination the sign-in flow returns to — `?org=1` expands the group and `#oidc` scrolls to the company sign-in card — so a completed pre-flight or re-authentication comes back to the card that started it rather than to a collapsed page.

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

#### Scenario: A credential that outlived its company sign-in still reads honestly

- **GIVEN** an install whose `accessSummary` reports one automatic account management credential, no non-password provider and an unrestricted password sign-in
- **WHEN** the Settings page renders
- **THEN** the collapsed line says that accounts are added and disabled automatically — the word for what a deprovisioning push does, which leaves the account in place and discoverable — does not claim that password sign-in is restricted, and still contains none of the banned words

#### Scenario: The automatic account management card mounts last

- **GIVEN** an install with a reverse-proxy row
- **WHEN** an admin expands the group
- **THEN** the cards appear in the order company sign-in, reverse proxy, group-to-role rules, login policy, automatic account management

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

