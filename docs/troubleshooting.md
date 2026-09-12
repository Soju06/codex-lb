# Troubleshooting

## Usage and quota

**Why does codex-lb still say `rate_limited` when Codex Desktop says the window reset?**
codex-lb refreshes usage on its own schedule and treats upstream samples conservatively. The full policy — refresh cadence, expiry, and why displays can briefly disagree with upstream — is documented in the
[usage refresh policy context](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/usage-refresh-policy/context.md).

## Streaming

**Codex CLI falls back to POST instead of WebSockets.**
Run the [WebSocket verification steps](client-setup.md#verify-websocket-transport). If codex-lb sits behind a reverse proxy, make sure it forwards WebSocket upgrades — see [Remote Access](deployment/remote.md).

## Fast Mode, Ultrafast, and service tiers

Fast Mode, Ultrafast, and service-tier behavior is documented in the
[Responses API compatibility context](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/responses-api-compat/context.md#fast-mode-and-service-tiers).

## Old Codex sessions missing after migrating

`codex resume` filters by `model_provider` — re-tag old sessions with the built-in retag command. See
[session retagging](client-setup.md#migrating-from-direct-openai-session-retagging).

## Locked out of the dashboard

**The company login is down, or the authenticator for the only administrator is gone.**
Four host commands act on the database directly to re-open local sign-in, reset a password, or turn a sign-in provider off — see
[Company Sign-In and Recovery](sso.md#host-recovery-commands).

## After upgrading, old pods fail every settings read

**Symptom.** The migration succeeded and the new pods are healthy, but pods still running the
previous release answer `500` on anything that loads dashboard settings — sign-in included. The
migration log carries one warning naming `20260912_010000_drop_legacy_dashboard_credentials`.

That release drops three `dashboard_settings` columns that every earlier build still maps, and those
builds load the settings row whole. The database is correct and complete: the same upgrade copied the
dashboard credentials onto the account rows before it dropped the columns they came from, so **do not
re-run or downgrade the migration**. Finish the roll instead — stop the old replicas (scale to zero,
or stop the old colour of a blue/green pair) and let the new ones serve. The upgrade refuses nothing
and needs no second command; the stop is the part that has to happen first, which is why the
[Kubernetes upgrade sequence](deployment/kubernetes.md#upgrading-to-the-release-that-drops-the-legacy-dashboard-credential-columns)
puts it before the migration Job.

---

*Spec: [usage-refresh-policy](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/usage-refresh-policy)*
