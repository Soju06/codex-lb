# Realtime API Compatibility Context

## Purpose and Scope

This capability preserves account continuity between private Codex Live Voice call creation and its control sideband in a pooled proxy. It covers the installed Codex app's private compatibility routes and the existing operator and dashboard contracts around them. It does not implement the documented public Realtime API or proxy WebRTC media.

See `openspec/specs/realtime-api-compat/spec.md` for normative requirements and `docs/live-voice.md` for the rendered user guide.

## Rationale and Decisions

- **Final success owns the call:** The generic Codex control request may refresh or fail over before returning a response. Ownership is captured only after the final successful account returns a supported call `Location`.
- **Possession is key-scoped:** A call id is not authorization. The registered proxy-key id participates in the ownership digest, so another key cannot attach even if it knows the id.
- **Ownership is durable but opaque:** The existing sticky-session store holds a bounded digest and owner reference in a reserved namespace. Raw call ids, credentials, SDP, attestation values, and frames are excluded.
- **Attachment is hard continuity:** Every ingress resolves the exact owner, rechecks current assignment and account state, loads current persisted identity, and acquires one stream lease. It does not refresh or select a replacement account.
- **Protocols stay explicit:** Current-app and v3 ingress connect to `/v1/live/{call_id}`. Legacy ingress preserves remaining ordered query fields and appends one normalized `call_id` to `/v1/realtime`.
- **Private authorization does not add base setup:** Live Voice requires an existing registered proxy key even when ordinary proxy authentication is disabled. The untouched proxy and dashboard remain zero-config.
- **Public boundaries stay visible:** Related SDK-documented `POST /v1/realtime/calls` and `POST /v1/realtime/client_secrets` routes provide protocol context only and are not implemented by this capability.

## Constraints

- All ids, mappings, batches, waits, messages, close reasons, and cleanup work are bounded.
- Missing or invalid keys fail before account selection; attachment rechecks current assignment and capacity.
- SDP, audio, transcripts, attestation values, frame bodies, tokens, and raw call ids are absent from persistence and diagnostics. Private call creation also keeps AuthManager metadata and shared-refresh warnings account-safe; the process-global refresh singleflight uses content-free task diagnostics regardless of caller order.
- No new setting, dependency, migration, public model, dashboard navigation item, README section, `.env.example` entry, background scheduler, or public Realtime endpoint is introduced.
- The connector does not infer a protocol, synthesize `OpenAI-Beta` or `Sec-WebSocket-Protocol`, or interpret event payloads. Client-offered WebSocket subprotocols retain their exact order through transport negotiation; downstream receives only an upstream-selected offered value.
- Reserved ownership is hidden from and protected against ordinary sticky-session list and delete operations.

## Failure Modes

- **Missing or unsupported successful `Location` or durable binding failure:** Persist the single private request row as an error, replace the unusable success with one credential-safe `503 realtime_call_binding_failed`, and never replay the created call through another account.
- **Conflicting immutable owner:** Preserve the original owner and fail closed.
- **Expired, cross-key, reassigned, paused, deleted, capped, or unavailable owner:** Deny attachment without substitution.
- **Routed handshake denial or network failure:** Preserve normalized safe context and do not replay or penalize the account; ordinary Responses network fallback remains unchanged.
- **Peer disconnect, oversize, cancellation, or close timeout:** Cancel owned work, bound both initial close and post-cancel drain, consume any late close-task result, close peers at most once, and release the lease once without waiting indefinitely for cancellation-resistant transport cleanup.
- **Reserved operator action:** Hide the row from lists and reject or skip single, bulk, filtered, and delete-all operations.

## Sanitized Example

1. Registered key `key_a` sends a call-creation request to `POST /backend-api/codex/realtime/calls`.
2. The final upstream account returns a successful response and `Location: /v1/realtime/calls/rtc_example`.
3. codex-lb stores only a key-scoped ownership digest for `rtc_example` and returns the valid response.
4. The app opens `WS /backend-api/codex/rtc_example`.
5. The shared sideband service reloads and leases the bound owner, then connects upstream to `/v1/live/rtc_example` with current persisted identity.
6. A credential-safe `realtime_live` WebSocket request remains visible in Recent Requests while the internal ownership row remains absent from sticky-session operator views.

## Operational Notes

The capability ships as one zero-config unit and uses existing request-log retention and sticky-session storage. There is no new rollout or monitoring setting. If a binding is no longer valid, create a new call rather than attempting account substitution. The dashboard parser correction is user-visible and remains subject to the repository's media evidence gate.
