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

---

*Spec: [usage-refresh-policy](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/usage-refresh-policy)*
