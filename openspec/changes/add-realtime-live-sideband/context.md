# Realtime API Compatibility Context

Normative behavior lives in [`specs/realtime-api-compat/spec.md`](specs/realtime-api-compat/spec.md). This file records evidence, rationale, constraints, failure modes, and an example without duplicating the requirements.

## Purpose and scope

Codex Live Voice setup creates a call over HTTP and then joins a control sideband over WebSocket. Both legs must use the same ChatGPT account in a pooled proxy. This change covers only that private installed-app compatibility surface and its existing operator/dashboard contracts. It does not proxy WebRTC media or implement the documented public Realtime API.

## Evidence

- An authorized, bounded probe of Codex app 26.721.31836 (build 5828) observed `POST /backend-api/codex/realtime/calls`, a successful `Location: /v1/realtime/calls/{call_id}`, and app-derived `WS /backend-api/codex/{call_id}` against the configured proxy base. The observation retained no opaque id, credential, SDP, audio, transcript, or frame payload.
- The SDK documentation describes related public `POST /v1/realtime/calls` and `POST /v1/realtime/client_secrets` endpoints. They establish the surrounding endpoint family but are not routes implemented by this private installed-app compatibility change.
- Discovery stayed within the authorized account and endpoint workflow, sent no prompts or other content to unrelated users, and retained no media, private payload, opaque identifier, credential, or session artifact.
- First-party Codex revision `99744cfe04806ebaa1e5d08e3e790070f852472b` distinguishes v3 `/v1/live/{call_id}` from legacy v1/v2 `/v1/realtime?...&call_id={call_id}`, appends the legacy call id after shaped query fields, and uses version-dependent `openai-alpha` values. The proxy preserves caller-supplied protocol context rather than guessing from call-id syntax.
- Codex-LB's request-log producer persists the sideband as `request_kind=realtime_live` and `transport=websocket`. The current dashboard consumer enumerates accepted kinds and rejects that full row until this change adds the matching value.
- The existing sticky-session table is suitable for an opaque reserved owner mapping, but ordinary list/delete paths must exclude and protect that namespace so internal call continuity never becomes operator session data.

## Rationale

Realtime compatibility is one new `realtime-api-compat` capability, not a Responses API delta. The transports differ in endpoints, header behavior, payload sensitivity, error contracts, and resource lifecycle. Live-specific credential redaction therefore branches at the existing connector seam and is explicitly covered by an ordinary Responses regression.

A reserved hashed owner avoids a migration for a bounded private surface. The API-key id participates in the digest so knowing a call id is not authorization. The final successful account is captured after any pre-visible refresh or failover; attachment then treats it as hard ownership and never refreshes or substitutes another account.

The required-key rule does not add setup to the base install. Operators who do not use this private feature still run the proxy and dashboard untouched. Operators who do use it employ the existing key-registration mechanism; there is no new environment variable, config file, account, migration action, or dashboard control.

## Constraints and non-goals

- All ids, mappings, batches, waits, messages, close reasons, and cleanup work are bounded.
- Missing/invalid keys fail before account selection; attachment rechecks current assignment and capacity.
- SDP, audio, transcripts, attestation values, frame bodies, tokens, and raw call ids are absent from request persistence and diagnostics; ownership persistence contains only the scoped digest and owner reference required for continuity. Private call creation also keeps AuthManager metadata and shared-refresh warnings account-safe; the process-global refresh singleflight uses content-free task diagnostics regardless of caller order.
- No new setting, dependency, migration, model, dashboard navigation item, README section, `.env.example` line, background scheduler, public `/v1/realtime/calls`, or public `/v1/realtime/client_secrets` route is introduced. The user-facing guide documents this private boundary and links to the synced main `realtime-api-compat` capability.
- No protocol is inferred, no `OpenAI-Beta` or `Sec-WebSocket-Protocol` is synthesized, and no event payload is interpreted. Client-offered WebSocket subprotocols retain their exact order through transport negotiation; downstream receives only an upstream-selected offered value.

## Failure modes

- Missing or unsupported successful `Location` or durable binding failure → persist the single private request row as an error, replace the unusable success with one credential-safe `503`, and never replay the created call.
- Conflicting immutable owner → preserve the original owner and fail closed.
- Expired, cross-key, reassigned, paused, deleted, capped, or unavailable owner → deny attachment without substitution.
- Routed handshake denial or network failure → preserve normalized safe context and do not replay or penalize the account; ordinary Responses routed network fallback remains unchanged.
- Direct Live `InvalidProxy`, `InvalidHandshake`, or `OSError` → use fixed credential-safe messages; preserve ordinary Responses behavior.
- Private call-creation or sideband request row → retain request correlation and capability/status fields while omitting account identity, model content, upstream error text, failure metadata, call id, headers, query, body, and frame content.
- Peer disconnect, oversize, cancellation, or close timeout → cancel owned work, bound both initial close and post-cancel drain, consume any late close-task result, close peers at most once, and release the lease once without waiting indefinitely for cancellation-resistant transport cleanup.
- Reserved operator action → hide the row from lists and reject or skip single, bulk, and filtered deletion.
- Dashboard request-log parse → accept the backend's `realtime_live` WebSocket row; do not weaken the field to arbitrary strings.

## Sanitized example

1. Registered key `key_a` sends an SDP offer to `POST /backend-api/codex/realtime/calls`.
2. The final upstream account returns `201`, an SDP answer, and `Location: /v1/realtime/calls/rtc_example`.
3. Codex-LB stores only an API-key-scoped digest mapping `rtc_example` to that final account and returns the valid response.
4. The app opens `WS /backend-api/codex/rtc_example`.
5. The shared service reloads and leases the bound owner; the connector opens `wss://api.openai.com/v1/live/rtc_example` with current persisted identity.
6. A legacy client opening `WS /v1/realtime?call_id=rtc_example&intent=quicksilver` uses the same owner while the connector emits `wss://api.openai.com/v1/realtime?intent=quicksilver&call_id=rtc_example`.
7. A credential-safe `realtime_live` WebSocket request row remains visible in Recent Requests, while the reserved ownership row remains absent from sticky-session operator views.

## Operational notes

The capability is zero-config and ships as one coherent unit. Existing request-log retention and sticky-session storage apply. There is no new monitoring or rollout knob.
