## Context

WebRTC Live Voice has an HTTP call-create leg and a control sideband WebSocket. The first-party Codex client reuses the same ChatGPT OAuth bearer and account id on both legs. Codex-LB already verifies that credential pair for the Codex usage endpoint and already persists the final call owner under a key-scoped digest. The missing layer is caller authorization: a valid ChatGPT identity alone does not grant access to every imported upstream account.

An authorized local probe of ChatGPT.app 26.727.51351 / bundled Codex `0.146.0-alpha.9.2` confirmed that both legs carry Bearer authorization and `chatgpt-account-id`. With `experimental_realtime_ws_base_url` set to `/v1`, the sideband reaches `/v1/realtime?intent=...&call_id=...`; the call-create override remains `/backend-api/codex`.

## Goals / Non-Goals

**Goals:**

- Preserve built-in `model_provider = "openai"` and official ChatGPT OAuth.
- Keep every existing Proxy API Key Live behavior byte-for-byte compatible where persisted affinity is concerned.
- Verify OAuth credentials against upstream before trusting token claims or selecting an account.
- Map a caller to one imported seat and authorize an explicit account set.
- Keep call owner continuity immutable, bounded, private, and fail closed.
- Default the feature off through absent policy state and add no global setting.

**Non-Goals:**

- Standalone public Realtime WebSocket keyless auth.
- Public `/v1/realtime/calls` or `/v1/realtime/client_secrets` endpoints.
- OAuth account import, reauthorization, refresh-token recovery, Keychain storage, or cross-machine token synchronization.
- Session retagging, WebRTC media relay, frame interpretation, or transcript/audio persistence.

## Decisions

### Classify the bearer before authorization

The shared Live resolver extracts one Bearer token. A token beginning with `sk-clb-` is always treated as a Proxy API Key and follows the existing required-key validator; invalid keys return 401 without OAuth fallback. Every other bearer requires `chatgpt-account-id` and enters verified OAuth resolution. Missing or invalid credentials return the existing credential-safe OpenAI-shaped 401 envelope.

### Verify upstream, then trust bounded claims

OAuth resolution keys cache and singleflight work by `SHA256(token + NUL + normalized chatgpt-account-id)`. It first identifies a route candidate without authorizing a pool, calls the existing upstream usage endpoint with the supplied credential pair, and only after upstream success decodes the bearer payload for `chatgpt_user_id`/`sub`, expiry, and workspace hints. Positive cache TTL is the minimum of 60 seconds and the token's remaining lifetime; bounded 5-second negative entries cover credential denials. Upstream 429 and availability failures keep their existing typed errors and are not cached as identity success.

The resolver returns a typed identity containing the internal caller Account id, normalized ChatGPT account id, verified usage payload, and resolved route. It stores no raw token in logs or persistence. The existing usage dependency remains a wrapper that projects this identity onto its established request-state fields.

### Resolve exactly one imported seat

When a verified token exposes a stable seat id, account lookup requires that seat id plus the claimed ChatGPT account/workspace identity. Legacy credentials without a usable seat id may resolve only when the verified workspace/account candidate set contains exactly one eligible imported Account. Missing, inactive, or ambiguous matches fail before policy or upstream account selection.

### Policy presence is the feature switch

`oauth_live_policies` has one row per caller Account with `is_active` and timestamps. `oauth_live_policy_accounts` links that caller to explicitly allowed upstream Accounts. An OAuth caller is authorized only when the policy is active and the allowed set contains at least one currently active Account. Account deletion cascades policy/assignment cleanup. Dashboard writes replace the complete allowed set transactionally and reject an active policy with an empty set.

### Carry one typed realtime caller scope

`RealtimeCallerScope` contains caller kind, affinity scope material, optional `ApiKeyData`, optional caller Account id, and optional allowed-account ids. Key callers use the raw existing `api_key.id` as affinity material. OAuth callers use `oauth:{caller_account_id}`. The reserved sticky-session prefix, digest formula, kind, two-hour TTL, immutable insert, and cleanup remain unchanged.

Key callers retain existing assignments, key limits, reservations, last-used behavior, and API-key request-log attribution. OAuth callers select only within their policy set, create no synthetic API key, bypass key-specific limit/reservation work, and persist `api_key_id = NULL`. Sideband reattach recomputes the caller scope, resolves the immutable owner, and confirms that owner remains allowed and active before leasing it.

### Keep the UI inside Accounts

The selected Account detail exposes an OAuth Live Voice policy card with an active switch and an allowed-account multi-select. Existing Account status, reauth, export, and delete actions remain unchanged. The page uses dedicated policy GET/PUT/DELETE endpoints guarded by dashboard write access. No global environment variable, Settings-page control, or core navigation entry is added.

## Risks / Trade-offs

- OAuth validation adds an upstream usage call on cache miss; short bounded caching and singleflight prevent handshake fan-out.
- Seat claims are decoded locally only after upstream validates the bearer/account pair; legacy ambiguity fails closed.
- The change touches auth, proxy, database, and Accounts UI seams. Atomic commit layers and current-main rebase keep review and conflict handling tractable.
- Experimental Codex route overrides can drift; every bundled Codex upgrade requires the real call-create and sideband route probe plus Live E2E.

## Migration Plan

Add the two empty policy tables on upgrade. Existing installs remain behaviorally unchanged because no policy exists. Downgrade removes the relationship table before the policy table. SQLite and PostgreSQL upgrade/downgrade tests must preserve a single Alembic head. Rollback restores the previous package/config/database snapshot; policy rows have no effect under the previous binary.

