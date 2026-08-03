# Realtime API Compatibility Context

## Purpose and Scope

This capability preserves account continuity between private Codex Live Voice call creation and its control sideband in a pooled proxy. It covers the installed Codex app's private compatibility routes and the operator contracts around them. WebRTC media remains peer-to-peer.

See `openspec/specs/realtime-api-compat/spec.md` for normative requirements and `docs/live-voice.md` for the rendered user guide.

## Supported caller profiles

| Profile | Client authorization | Serving-account scope |
| --- | --- | --- |
| Built-in `openai` | Official ChatGPT OAuth bearer plus `chatgpt-account-id` | Settings → Live Voice global OAuth pool |
| Registered Key | `sk-clb-` bearer from `env_key`; Codex keeps `requires_openai_auth = true` | Existing Key assignments and limits |

The built-in profile preserves the official OpenAI provider and requires no client-side Codex-LB Key. The registered-Key profile preserves the established codex-lb contract for conversations and Live Voice. Both profiles route call creation and sideband through codex-lb.

## Rationale and Decisions

- **Authentication dispatch is explicit:** `sk-clb-` bearers enter strict Proxy Key validation. Other bearers require verified ChatGPT OAuth identity.
- **Caller identity and serving accounts are separate:** OAuth validation derives a stable principal from verified claims. The global policy selects which imported Accounts may serve that principal.
- **Possession is caller-scoped:** A call id alone grants no access. The caller scope participates in the ownership digest, isolating Keys and OAuth principals from each other.
- **Final success owns the call:** The generic Codex control request may refresh or fail over before returning a response. Ownership is captured after the final successful account returns a supported call `Location`.
- **Ownership is durable and opaque:** The sticky-session store holds a bounded digest and owner reference in a reserved namespace. Raw call ids, credentials, SDP, attestation values, and frames remain outside persistence.
- **Attachment enforces hard continuity:** Every ingress resolves the exact owner, rechecks current caller scope and account state, loads current persisted identity, and acquires one stream lease.
- **Protocols stay explicit:** Current-app and v3 ingress connect to `/v1/live/{call_id}`. Legacy ingress preserves remaining ordered query fields and appends one normalized `call_id` to `/v1/realtime`.
- **Base setup remains zero-config:** OAuth Live starts disabled. Registered-Key Live keeps its existing configuration and behavior.

## Constraints

- All ids, mappings, batches, waits, messages, close reasons, and cleanup work are bounded.
- Missing or invalid caller credentials fail before account selection.
- OAuth policy lookup returns only active imported Accounts from its explicit pool.
- SDP, audio, transcripts, attestation values, frame bodies, tokens, and raw call ids remain absent from persistence and diagnostics.
- The feature adds one reversible migration and one Settings card. It adds no environment setting, dependency, dashboard navigation item, README section, `.env.example` entry, background scheduler, public model, or public Realtime endpoint.
- The connector preserves client-offered WebSocket subprotocol order and returns only an upstream-selected offered value.
- Reserved ownership stays hidden from ordinary sticky-session list and delete operations.

## Failure Modes

- **OAuth policy inactive or empty:** Deny OAuth Live before account selection with `403 oauth_live_not_enabled`.
- **Missing or unsupported successful `Location` or durable binding failure:** Persist one private error request row and return `503 realtime_call_binding_failed`.
- **Conflicting immutable owner:** Preserve the original owner and fail closed.
- **Expired ownership or owner outside the current caller scope:** Deny attachment without account substitution.
- **Routed handshake denial or network failure:** Preserve normalized safe context and avoid replaying or penalizing the account.
- **Peer disconnect, oversize, cancellation, or close timeout:** Cancel owned work, bound drain time, close peers at most once, and release the lease once.

## Examples

### Built-in OAuth profile

1. Codex uses `model_provider = "openai"` and sends official ChatGPT OAuth credentials.
2. codex-lb validates a stable OAuth principal and loads the active global OAuth pool.
3. The final serving Account owns the new call under an OAuth-principal digest.
4. Sideband revalidates the principal and reconnects to that exact owner.

### Registered-Key profile

1. Codex uses a custom provider named `openai`, `requires_openai_auth = true`, and `env_key = "CODEX_LB_API_KEY"`.
2. The registered Key keeps its existing account assignments, limits, request attribution, and affinity input.
3. Normal conversations and Live Voice calls traverse the same Key-authenticated proxy contract.

## Operational Notes

Both realtime base URLs must point at codex-lb. Each bundled Codex upgrade receives a call-create route probe, sideband route probe, normal-conversation check, and audible Live Voice check. A revoked OAuth policy affects subsequent OAuth authorization; registered-Key traffic continues under its existing Key policy.
