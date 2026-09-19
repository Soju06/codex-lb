## Context

2c-1 shipped the provider rows, the `IdentityResolver` and the identity cache; it deliberately left "mappings, `no_match_role_id`/`skip_role_sync` behaviour, the groups header, the organisation UI" to this change (its design.md Non-Goals). Everything here therefore hangs off code that already exists: one new table, one new field on `ExternalIdentity` that is already declared and never populated, behaviour inside `IdentityResolver._provision`/`_seen`, and the first cards of the collapsed Organisation group.

## Goals / Non-Goals

**Goals**
- One rule list per provider, evaluated by the resolver that already runs, with a single winner and no ties by construction.
- Every re-evaluation outcome in PLAN §4.6's table is a spec scenario, including the three "do nothing" rows, which are the ones an upgrade can regress.
- An install that adds no rule behaves exactly as it does today (D10), and an install that adds one can never lock itself out.
- The Organisation group costs nothing until it is opened: collapsed means unmounted means zero enterprise requests.
- Every act on a rule — writing, retargeting, deleting, reordering — is gated by the role that rule hands out, so the rules can never widen the caller's own reach.

**Non-Goals**
- `local_login_policy`, break-glass, the CLI, `docs/sso.md` (PR-2d); OIDC (3a); SCIM (3b); audit sinks (Phase 4); the custom role editor; a general audit-log page.

## Decisions

### `RESTRICT` on the mapping's role, `SET NULL` on the provider's roles (no second migration)

The PR brief says `no_match_role_id` is `FK RESTRICT`; 2c-1 shipped it — and spec'd it in "Sign-in providers are rows with an implementation" — as `ON DELETE SET NULL`, which is also what the resolver wants (`NULL` already means "disable"). Flipping it would need a second migration and a MODIFIED requirement for no behavioural gain. `RESTRICT` is used only where it means something: `dashboard_role_mappings.role_id`, where losing the role would silently change who gets what. Note that SQLite enforces this only with `PRAGMA foreign_keys` on, so the delete path that matters (the custom-role editor, Phase 5b) will still need an application check.

### A stale renumber is a reload, not a 500

Both renumbering paths (create and reorder) decide the new numbers from a snapshot they read first. A second writer landing in between makes that snapshot stale and the renumber collides with `UNIQUE(provider, provider_key, priority)`. Rather than invent a second refusal, the collision is rolled back and raised as the stale-order refusal the reorder path already defines, so the caller is told to reload — and both paths go through one renumber-and-commit helper, so neither can forget.

### Priorities are dense, server-owned, and renumbered in one transaction

`UNIQUE(provider, provider_key, priority)` makes reordering a multi-row swap that collides mid-update on both backends (SQLite has no deferred constraints). The server therefore owns the numbers: after every write the rows of one `(provider, provider_key)` are `N…1` with `N` the winner, a create appends at `1` and shifts the rest up, a delete closes the gap, and `PUT /api/role-mappings/order` takes the full ordered id list. Each renumber runs as one transaction that first parks the rows on a disjoint offset and then writes the final values, so the constraint never trips halfway. Clients never invent a priority.

### The first rule re-evaluates the accounts the provider created — say so before saving

`_provision` stamps `role_source=mapping` on every JIT account, including the ones that got their role from `unknown_identity_role_id`. The moment an operator adds their first rule, those accounts become eligible for re-evaluation and any of them matching no rule moves to `no_match_role_id` (the seed's viewer). That is what "the login method manages these accounts" has to mean — the alternative, stamping `manual` at JIT, would make mappings inert for exactly the accounts they exist to manage — but it must not be a surprise: the rules card states it in the empty state and in the save confirmation, and every demotion is audited `role_demoted_no_mapping`. Accounts a human touched are `manual` and are never in scope.

### Re-evaluation obeys the last-admin invariant by pinning, not by refusing forever

A demotion or a disable that would leave zero active accounts holding the admin preset is not applied; instead the account's `role_source` flips to `manual` and `role_source_overridden` is audited with `reason: last_admin_protected`. Refusing without pinning would retry on every TTL and spam the audit log; applying it would lock the install out from its own reverse proxy.

The count behind that decision is read the way the account-management path reads it: `acquire_write_intent()` first (SQLite `BEGIN IMMEDIATE`, PostgreSQL `SELECT … FOR UPDATE` over the active admins), then the count, then the write — all on the resolver's one session, which is also the session `commit_user` commits, so the lock is still held when the demotion lands. A plain read-then-write would let two replicas re-evaluating two mapping-managed admins at the same moment each see the other survive, and an install with zero admins cannot be recovered from the dashboard.

### Re-evaluation rides the existing throttle and writes only on change

The identity cache (5 s, keyed `(provider, provider_key, subject)`) already defines "one resolver run per sign-in" for a header install; re-evaluation happens inside that run and issues an UPDATE only when the role, the status or the stored group snapshot actually differs. Every mapping write invalidates the provider registry, which bumps the dashboard-users namespace and so clears the identity cache on every replica — otherwise an operator's new rule appears to do nothing for a TTL, and cached refusals keep refusing someone the rule now admits.

### A duplicated groups header means "no groups", not "merge them"

The identity header is honoured only when it appears exactly once. The groups header follows the same discipline for the same reason — a second occurrence means something upstream is not stripping a client-supplied copy — but fails closed to an empty group set rather than refusing the request, so a misconfigured proxy demotes people instead of locking them out. Values are comma-separated, trimmed, case-folded for comparison, deduplicated and bounded (100 groups, 200 characters each). The setting also refuses to equal the identity header, which would otherwise turn the username into a group claim.

### The refused-sign-ins link opens a sheet, not an audit page

PLAN §4.7 promises a deep link to the audit log filtered on `login_failed reason=unknown_identity`; the backend filters exist, but the dashboard has no audit-log page, route or client at all, and building one here would double this PR. The rules card therefore renders the count and, for a caller who also holds `audit:read`, a **View** action that opens a small sheet listing those rows from `GET /api/audit-logs` — a real destination, no dead control (PLAN §4.8 principle 2), and the sheet is a drop-in for the future page's deep link. Without `audit:read` the count is plain text.

### Group visibility is derived from the session, not from a probe

Rendering the Organisation group only when a child exists must not cost a request on every Settings load, and `access_summary` is `null` for a principal without `users:manage` — so a `security:write` custom role would lose the group it is supposed to edit. Visibility therefore comes from facts every session already carries: `security:write` plus `authMode === "trusted_header"` (the reverse-proxy card's own condition). `access_summary`, when present, only decides whether the collapsed line shows the status summary; when it is absent the neutral one-liner is shown.

The same permission split reaches inside the group: the roles list is `users:manage`, and `assignable_role_ids` is empty without it, so a `security:write` custom role could open the group and edit nothing — every picker empty, every role control disabled. Rather than widen the `users:manage` roles read (its gate is a security contract the route matrix pins), the rules API serves its own smaller read, `GET /api/role-mappings/assignable-roles`: id, slug, name, kind, locked, for the roles that pass the two rules the writes already apply (`role_assignable_to_users` and `assert_can_delegate`). It is a better list than the client-side filter it replaces — that one intersected with the static preset ids in the session, which are not filtered by the caller's grants at all — and it means the group has exactly one role source regardless of the caller's other permissions.

### `AdvancedSettingsGroup` gains label props, keeps its own labels

The component hardcodes `settings.advanced.*`. It gains optional label props defaulting to those keys, so the Advanced group's accessible name ("Show advanced settings") stays byte-identical — the Playwright smoke and `settings-page.test.tsx` both click it by name.

### "Settings page" is re-merged from both siblings

Three unarchived changes now write that requirement: main, `guest-ui-hides-restricted-surfaces` (write-only surfaces paragraph and its read-only scenario) and `access-settings-card` (the Access card, which copied main and dropped the guest-ui edits). This delta copies main's block with **both** siblings' edits re-applied, so whichever order the three archive in, nothing is lost.

### The People-tab additions are an ADDED requirement

"People tab" is already MODIFIED by two active changes; chaining a third copy of that block would add ~80 unrelated lines to this delta for two controls. The badge and the take-over action are specified as their own requirement instead, which composes with it.

## Risks / Trade-offs

- **Silent demotion window**: an account demoted by a rule keeps its old session until `session_generation` is bumped; re-evaluation bumps it on a role change, so the person is asked to sign in again — deliberate, and the same behaviour as an admin-driven role change.
- **Rule count**: rules are evaluated per sign-in; the list is bounded at 100 per provider (`409 mapping_limit_reached`) so the resolver's work stays trivially bounded.
- **Reordering UX**: no drag-and-drop library is installed. Rows are reordered with Move up / Move down (keyboard reachable) and native `draggable` as an enhancement; the write is one `PUT …/order`, so a dropped drag never leaves a half-applied order.
