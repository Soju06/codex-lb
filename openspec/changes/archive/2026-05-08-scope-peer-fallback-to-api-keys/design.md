## Context

Peer fallback targets currently live in `peer_fallback_targets` and are resolved as a global runtime pool. API keys already support scoped policy through cached `ApiKeyData`, so peer fallback URLs should be owned directly by each API key instead of selected from a Settings-managed catalog.

## Goals / Non-Goals

**Goals:**
- Store ordered peer fallback base URLs per API key.
- Resolve runtime peer fallback candidates only from the authenticated API key.
- Remove peer fallback catalog/selection from Settings.
- Keep existing loop-prevention, health-check, timeout, and error-code gates.

**Non-Goals:**
- Automatic fallback for unauthenticated or local requests.
- Environment-configured peer URLs as runtime defaults.
- Websocket peer fallback.
- Cross-instance API key synchronization.

## Decisions

- Add `api_key_peer_fallback_urls` instead of embedding URLs on `api_keys`.
  - Rationale: preserves deterministic ordering, supports replace-all updates, and keeps the API key row narrow.
  - Alternative considered: global target catalog plus assignment IDs; rejected because operators want each API key to own its peer URLs without Settings pre-registration.

- Add `peerFallbackBaseUrls` directly to API key create/update/response payloads.
  - Rationale: keeps API key policy editable through the existing API key management flow.
  - Alternative considered: separate `/api/api-keys/{id}/peer-fallback-urls` endpoint; rejected as unnecessary for the current simple replace-all policy.

- Treat an absent or empty API key peer URL list as peer fallback disabled.
  - Rationale: satisfies the requirement that fallback only uses targets connected to the API key.
  - Alternative considered: inherit global targets for empty assignments; rejected because it preserves the global fallback behavior the change removes.

- Store URL order through `priority` and return URLs ordered by priority.
  - Rationale: runtime fallback attempts are order-sensitive and should remain deterministic.

## Risks / Trade-offs

- Existing configured global targets stop affecting runtime fallback. Mitigation: require explicit peer URL entry on each API key that should fallback.
- Peer instances must accept the same API key or otherwise allow the forwarded request. Mitigation: keep forwarding the original `Authorization` header and document the operational expectation through the API key assignment behavior.
- Cached API key data can hold stale peer URL lists until invalidation. Mitigation: reuse existing API key update invalidation and cache invalidation polling.
