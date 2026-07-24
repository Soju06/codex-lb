## Context

Current Codex creates a WebRTC call over `POST /backend-api/codex/realtime/calls`, extracts an opaque `rtc_...` id from the `Location` response header, and then opens a Frameless control WebSocket at `wss://api.openai.com/v1/live/{call_id}`. Both legs must use the same ChatGPT account identity. Codex-LB's generic control proxy can refresh or fail over before any response is visible, so the account initially selected by the route is not necessarily the account that ultimately created the call.

Codex-LB currently has durable typed sticky mappings and account stream leases, but no realtime-call kind. Adding a database enum/migration solely for an experimental private surface would increase rollback risk. The existing Codex-session mapping table can safely carry a disjoint, hashed internal key namespace when resolution never enters generic Codex-session selection.

## Goals / Non-Goals

**Goals:**

- Complete the two-leg Codex realtime transport through Codex-LB.
- Bind sideband attachment to the final account that successfully created the call, including after pre-visible refresh or failover.
- Scope possession of a call id to the API key that created it.
- Work across Codex-LB replicas using the existing database-backed affinity store.
- Preserve required Codex handshake headers while replacing proxy credentials.
- Keep mapping, ingress, connection, stream-capacity, and frame handling bounded.
- Avoid all token refresh and cross-account retry once a call id exists.

**Non-Goals:**

- Publish GPT-Live as a supported OpenAI model, implement the documented `/v1/realtime/calls` API, or translate between realtime protocol versions.
- Proxy WebRTC media; media continues over the SDP-negotiated peer connection.
- Parse, authorize, mutate, record, or synthesize Frameless events.
- Generate or bypass `x-oai-attestation`, product entitlement, account policy, or upstream authorization.
- Add a dashboard setting, model-catalog entry, database migration, or background cleanup scheduler.
- Change ordinary Responses WebSocket behavior.

## Decisions

### Capture the final successful control account

`codex_control_request` will accept an optional synchronous success callback carrying only the final account id. Every successful return path invokes it after upstream success, including pre-visible account failover and forced-refresh retry. The route adapter captures that id and creates the call binding only after the control request returns.

A synchronous observer avoids changing the established response type and cannot accidentally turn a local binding failure into an upstream account penalty or another account retry. Durable binding happens outside the control service's account-error handling. If binding fails, the downstream call-creation response fails closed rather than exposing a call that cannot be joined through this proxy.

### Derive an API-key-scoped opaque affinity key

The stored key is:

`codex_live_call:` + SHA-256(`api_key_id-or-anonymous`, NUL, normalized call id)

Only the digest and selected account id enter `sticky_sessions`; raw call ids, API keys, access tokens, SDP, and attestation values do not. The existing `CODEX_SESSION` kind is used as storage only, under a reserved prefix that is never passed through generic Codex-session account selection.

Mappings expire two hours after call creation. Resolution supplies the fixed maximum age, and each binding write opportunistically purges expired rows in the reserved prefix. This covers normal call setup and reconnect while bounding abandoned mappings without a new scheduler or schema migration.

### Treat the call id as hard account ownership

The WebSocket route first authenticates the Codex-LB API key and validates the `rtc_` path shape. It derives the API-key-scoped key, resolves the persisted owner, and asks the existing selector for that exact preferred account with stream leasing, assignment-scope enforcement, continuity-owner treatment, and fallback disabled.

A missing, stale, unauthorized, paused, deleted, capped, or otherwise unavailable owner fails closed. The route never selects a replacement account because another account cannot attach to the existing upstream call.

### Do not refresh on sideband attach

Successful call creation already used a valid credential. Sideband attach decrypts the bound account's currently persisted access token but does not call the refresh manager. A handshake `401`, `403`, transport error, or timeout is returned to the caller without refreshing, failing over, or changing global account health. This preserves the account-bound contract and prevents an experimental capability failure from disrupting ordinary production traffic.

### Use a dedicated upstream live connector

A shared internal WebSocket connector will retain existing direct/proxied egress, timeout, error normalization, response-header, and network-rotation behavior. The Responses connector continues to build `/codex/responses`, inject its beta header, and use its existing opt-in archive behavior. The live connector instead targets `wss://api.openai.com/v1/live/{call_id}`, preserves the downstream Frameless parser/architecture query parameters and filtered Codex handshake headers, replaces Authorization and ChatGPT account identity, never injects the Responses beta header, and explicitly disables frame-body archiving.

The live route forwards text and binary frames verbatim without invoking the debug archive, even when Responses archiving is enabled. This change adds no frame, SDP, token, attestation, transcript, or audio logging.

### Keep WebRTC media outside Codex-LB

The HTTP call-creation response remains unchanged, including SDP body and `Location`. Codex-LB only carries call creation and the server-side control sideband. Audio media continues directly over WebRTC, avoiding an unnecessary media relay and preserving the upstream transport design.

## Risks / Trade-offs

- **Private upstream protocol changes** → Keep the adapter transparent, scoped to exact routes, and covered by fixture-based handshake/relay tests; do not advertise a public model.
- **Call id is created but binding persistence fails** → Fail the downstream HTTP request before exposing the call; no cross-account recovery is attempted.
- **Abandoned bindings accumulate** → Fixed two-hour resolution expiry plus opportunistic prefix cleanup bounds active rows; a later dedicated store is possible if volume warrants it.
- **A long call reconnects after two hours** → It fails closed and must create a new call. The TTL is deliberately longer than ordinary realtime session lifetime while remaining bounded.
- **Sideband denial reflects entitlement or attestation rather than account health** → Do not penalize, refresh, or fail over the account on sideband failure.
- **API-key deletion or changed assignment after call creation** → WebSocket authentication and exact-account selection re-evaluate current policy; the stale mapping alone grants no access.
- **Realtime frames can contain audio, transcript, or tool material** → Disable frame-body archiving for this route and retain only credential-safe request/route metadata.

## Migration Plan

No schema or configuration migration is required. Deploy the route, owner binding, connector, and tests together. Rollback removes the route; reserved sticky rows are inert and age out opportunistically on a future deployment containing this change or can be purged administratively by prefix if necessary.

## Open Questions

None.
