# Troubleshooting

## Usage and quota

**Why does codex-lb still say `rate_limited` when Codex Desktop says the window reset?**
codex-lb refreshes usage on its own schedule and treats upstream samples conservatively. The full policy — refresh cadence, expiry, and why displays can briefly disagree with upstream — is documented in the
[usage refresh policy context](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/usage-refresh-policy/context.md).

## Streaming

**Codex CLI falls back to POST instead of WebSockets.**
Run the [WebSocket verification steps](client-setup.md#verify-websocket-transport). If codex-lb sits behind a reverse proxy, make sure it forwards WebSocket upgrades — see [Remote Access](deployment/remote.md).

## Live Voice

**The Live Voice entry is missing in Codex Desktop.**
Keep official OpenAI authentication enabled in the client profile. A registered-Key profile uses `requires_openai_auth = true`; the built-in OAuth profile keeps `model_provider = "openai"`. Restart Codex Desktop after changing `config.toml`.

**Voice chat takes too long to start and codex-lb receives no Live request.**
The client timed out before call creation. Confirm microphone permission and local audio-device initialization first. A codex-lb authentication failure always produces a request at one of the documented [Live Voice routes](live-voice.md#supported-private-routes).

**Call creation succeeds but the sideband does not reach codex-lb.**
Set both experimental base URLs. The WebRTC base ends in `/backend-api/codex`; the WebSocket base ends in `/v1`:

```toml
experimental_realtime_webrtc_call_base_url = "http://127.0.0.1:2455/backend-api/codex"
experimental_realtime_ws_base_url = "http://127.0.0.1:2455/v1"
```

**OAuth Live returns `401 invalid_api_key`.**
The OAuth lane uses the zero-key origin boundary and is available while global Proxy API Key authentication is disabled. Loopback works without CIDR configuration. Remote clients use a registered Proxy API Key.

**OAuth Live returns `403 oauth_live_not_enabled`.**
Open **Settings → Live Voice**, select at least one active upstream Account, enable **OAuth Live access**, and save.

**A sideband returns `404 realtime_call_not_found`.**
The ownership binding is missing, expired, or scoped to another `chatgpt-account-id`. OAuth bearer refresh preserves the binding when the account header remains stable. Start a new Live call after an account-scope change.

## Fast Mode and service tiers

Fast Mode and service-tier behavior is documented in the
[Responses API compatibility context](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/responses-api-compat/context.md#fast-mode-and-service-tiers).

## Old Codex sessions missing after migrating

`codex resume` filters by `model_provider` — re-tag old sessions with the built-in retag command. See
[session retagging](client-setup.md#migrating-from-direct-openai-session-retagging).

---

*Specs: [usage-refresh-policy](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/usage-refresh-policy) · [realtime-api-compat](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/realtime-api-compat)*
