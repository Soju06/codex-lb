## Context

`expand-dashboard-permission-vocabulary` gave every sensitive route a named permission but deliberately left the guest read surface untouched. The guest grant table (`dashboard:read`, `accounts:read`) still lets a guest read the API-key inventory, egress-proxy topology, sticky bindings with account e-mails, and unmasked account identity. Guest sessions are Fernet cookies with a no-op `delete()`; the only way to end them today is to wait for expiry.

## Goals / Non-Goals

**Goals**
- A guest sees aggregate health, usage, request logs (already redacted), reports, and account *status* — not who the accounts belong to upstream, not the key fleet, not network topology.
- Operators can log every guest out, and every guest-credential or guest-access change does so automatically.
- Zero configuration; built-in admin unchanged; frontend contract unchanged apart from masked/null identity fields that the client already tolerates.

**Non-Goals**
- Frontend changes (separate change `guest-ui-hides-restricted-surfaces`).
- Admin session revocation (Phase 1, per-user `session_generation`).
- Redacting operator-chosen `alias` (it is a label the operator wrote for exactly this audience).

## Decisions

### Gate by the permission the data belongs to, not by role

`GET /api/api-keys*` is an `api_keys:read` surface; egress-proxy topology, connect address, and sticky bindings are operational configuration (`ops:write`); OAuth flow state belongs to account onboarding (`accounts:write`). Re-using the vocabulary means a future `operator`/`viewer` preset inherits the right answer without touching routes again. Guest lacks all three. The 403 shape is the existing `permission_required` + `param`.

Alternative rejected: returning redacted/empty inventories to guests. An empty key list is a false statement about the system; a 403 is honest, and the client can hide the surface.

### Redact identity in the single account mapper

`build_account_summaries` already threads `include_auth`; `redact_identity` is a second flag on the same path, so both `GET /api/accounts` and the overview embed the same projection. `email` is masked rather than dropped because the client schema requires a string; masked values may collide (`alice@` and `adam@` both become `a***@example.com`), which is acceptable because the client keys rows by `accountId` and operators disambiguate with `alias`. `displayName` falls back to alias or the masked e-mail. Identity fields that are nullable in both schemas (`chatgptAccountId`, `workspaceId`, `workspaceLabel`) become null. `isEmailDuplicate` stays boolean-only.

The gate signal is `accounts:write`, not `accounts:read`: reading account *status* is exactly what a viewer needs, but knowing *which* upstream seat it is only matters to whoever can act on it.

### Close the request-log e-mail oracle and the options inventory

Guest request-log rows were already redacted, but `search=` still matched `Account.email`, so a guest could confirm an e-mail by probing. The same applies to the key inventory: rows carried `apiKeyId`/`apiKeyName` and search matched `ApiKey.name`, so both are withheld without `api_keys:read` (`include_api_key_identity`). The filter builder gains `include_account_identity` (mirroring `include_sensitive_metadata`), keyed into the count cache so an admin's cached count cannot answer a guest. `GET /api/request-logs/options` keeps working for guests but returns `apiKeys: []` without `api_keys:read`; the schema requires the key to be present, so an empty array is the compatible shape.

### Generation counter instead of a session table

A `dashboard_settings.guest_session_generation` integer is the smallest thing that makes stateless guest cookies revocable: the cookie carries `gg`, validation compares it with the settings row (already cached and cross-replica invalidated via the `settings` namespace), and any trigger that changes what a guest is bumps the counter inside the same optimistic-locked UPDATE. Guest-password set/clear bump in the auth repository closures (which already retry once on version conflict, so `+= 1` stays a single net increment); the guest-access on→off transition bumps inside `SettingsRepository.update`; `POST /guest/logout-all` bumps explicitly. Re-saving `guestAccessEnabled=true` or unrelated settings does not bump.

Legacy guest cookies have no `gg`; the store surfaces them with `None`, which never equals the stored integer, so they die at upgrade. Admin cookies never carry `gg` (creating a guest session without a generation is a programming error and raises). A per-user `session_generation` follows the same shape in Phase 1.

### Migration shape

`20260908_000000_add_guest_session_generation` adds the column with `server_default=text("0")`, `nullable=False`, inside `batch_alter_table` (SQLite rebuild / PG plain ALTER), guarded by an inspector check for idempotency; downgrade drops it under the same guard. The model uses the identical `server_default` so `check_schema_drift` stays clean. No backfill is needed: the seeded settings row gets 0.

## Risks / Trade-offs

- [Risk] Guest UI shows error cards until the client change lands. → Stacked PR `guest-ui-hides-restricted-surfaces` follows immediately; behaviour is a visible but harmless "failed to load" state, not a crash.
- [Risk] A future viewer role that *should* see key names for filters. → `GET /api/request-logs/options` is the single place; grant `api_keys:read` or add a names-only projection then.
- [Risk] A rolling upgrade where an old replica issues a guest cookie without `gg`. → The new replica rejects it; the guest re-logs in once. Acceptable for a read-only role.
- [Trade-off] `alias` remains visible to guests. Intentional: it is operator-authored labelling for exactly this audience.
