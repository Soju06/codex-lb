# Realtime API Compatibility Context

## Purpose and Scope

This capability preserves account continuity between private Codex Live Voice call creation and its control sideband in a pooled proxy. It covers the installed Codex app's private compatibility routes and the operator contracts around them. WebRTC media remains peer-to-peer.

See `openspec/specs/realtime-api-compat/spec.md` for normative requirements and `docs/live-voice.md` for the rendered user guide.

## Supported caller profiles

| Profile | Client authorization | Serving-account scope |
| --- | --- | --- |
| Built-in `openai` | Locally admitted official ChatGPT OAuth bearer plus `chatgpt-account-id` | Settings → Live Voice global OAuth pool |
| Registered Key | `sk-clb-` bearer from `env_key`; Codex keeps `requires_openai_auth = true` | Existing Key assignments and limits |

The built-in profile preserves the official OpenAI provider and requires no client-side Codex-LB Key. It inherits the ordinary proxy's zero-key origin boundary. The registered-Key profile preserves the established codex-lb contract. Both profiles route call creation and sideband through codex-lb.

## Rationale and Decisions

- **Bearer dispatch is explicit:** `sk-clb-` bearers enter strict Proxy Key validation. Every other bearer enters the keyless lane.
- **Zero-key origin admission is shared:** Keyless Live calls reuse the ordinary proxy's loopback, trusted-proxy consensus, preserved raw-socket peer, and existing unauthenticated CIDR checks. Global API-key mode closes this lane.
- **Possession is caller-scoped:** A purpose-separated HMAC of the bearer and normalized account header participates in the ownership digest. A call id alone grants no access.
- **Final success owns the call:** Ownership is captured after the final successful account returns a supported call `Location`.
- **Ownership is durable and opaque:** The sticky-session store holds a bounded digest and owner reference. Raw call ids, credentials, SDP, attestation values, and frames remain outside persistence.
- **Attachment enforces hard continuity:** Every ingress recomputes caller scope, resolves the exact owner, rechecks policy and account state, loads current persisted upstream identity, and acquires one stream lease.
- **Protocols stay explicit:** Current-app and v3 ingress connect to `/v1/live/{call_id}`. Legacy ingress preserves remaining ordered query fields and appends one normalized `call_id` to `/v1/realtime`.
- **Base setup remains zero-config:** OAuth Live starts disabled. Registered-Key Live keeps its existing configuration and behavior.

## Constraints

- All ids, mappings, batches, waits, messages, close reasons, and cleanup work are bounded.
- Missing credentials and connections outside the zero-key boundary fail before policy lookup and account selection.
- OAuth policy lookup returns only active imported Accounts from its explicit pool.
- The HMAC key is derived from the existing persistent encryption key with a dedicated domain label; no new secret or setting is introduced.
- SDP, audio, transcripts, attestation values, frame bodies, tokens, account headers, and raw call ids remain absent from persistence and diagnostics.
- The feature adds one reversible migration and one Settings card. It adds no environment setting, dependency, dashboard navigation item, README section, `.env.example` entry, background scheduler, public model, or public Realtime endpoint.
- The connector preserves client-offered WebSocket subprotocol order and returns only an upstream-selected offered value.
- Reserved ownership stays hidden from ordinary sticky-session list and delete operations.

## Failure Modes

- **Source outside the zero-key boundary or global API-key mode enabled:** Deny the keyless lane with `401 invalid_api_key`.
- **OAuth policy inactive or empty:** Deny keyless Live before account selection with `403 oauth_live_not_enabled`.
- **Bearer, account header, or encryption key changed:** The ownership namespace changes; an existing sideband receives the credential-safe not-found response and the client creates a new call.
- **Missing or unsupported successful `Location` or durable binding failure:** Persist one private error request row and return `503 realtime_call_binding_failed`.
- **Conflicting immutable owner:** Preserve the original owner and fail closed.
- **Expired ownership or owner outside the current caller scope:** Deny attachment without account substitution.
- **Routed handshake denial or network failure:** Preserve normalized safe context and avoid replaying or penalizing the account.
- **Peer disconnect, oversize, cancellation, or close timeout:** Cancel owned work, bound drain time, close peers at most once, and release the lease once.

## Examples

### Built-in OAuth profile

1. Codex uses `model_provider = "openai"` and sends official ChatGPT OAuth credentials from loopback.
2. codex-lb applies its existing zero-key origin guard and derives an opaque credential-pair scope locally.
3. The final serving Account owns the new call under that scope.
4. Sideband presents the same credential pair and reconnects to that exact owner.

### Registered-Key profile

1. Codex uses a custom provider, `requires_openai_auth = true`, and `env_key = "CODEX_LB_API_KEY"`.
2. The registered Key keeps its existing account assignments, limits, request attribution, and affinity input.
3. Normal conversations and Live Voice calls traverse the Key-authenticated proxy contract.

## Operational Notes

Both realtime base URLs must point at codex-lb. Each bundled Codex upgrade receives a call-create route probe, sideband route probe, normal-conversation check, and audible Live Voice check. Loopback requires no CIDR configuration. The existing unauthenticated raw-peer CIDR setting remains an advanced operator opt-in.
