## Context

Live Voice has two authenticated legs: HTTP call creation and a control sideband WebSocket. The serving upstream account must remain identical across both legs. Official Codex supplies one ChatGPT OAuth bearer plus `chatgpt-account-id`; Key-based clients supply a registered Codex-LB Proxy API Key.

Ordinary proxy traffic already has a zero-key admission contract. When global API-key authentication is disabled, requests are accepted only from loopback or an explicitly configured raw-socket peer CIDR. Proxy-header projection preserves the raw peer and resolves trusted proxy chains fail closed.

## Goals

- Preserve `model_provider = "openai"` and official OAuth.
- Reuse the ordinary proxy's existing zero-key network boundary.
- Preserve the registered-Key Codex profile and every Key authorization semantic.
- Apply one operator-managed upstream pool to admitted keyless Live callers.
- Preserve exact-owner affinity across call creation and sideband.
- Add no setting, secret, or caller-registration entity.

## Decisions

### Classify bearer before authorization

`sk-clb-` bearers use strict Proxy Key validation. Other bearers require `chatgpt-account-id` and enter the keyless OAuth lane. Both call-create and all three sideband routes share this resolver.

### Reuse zero-key origin admission

The keyless lane calls the same proxy authorization dependency with no Proxy Key. Global API-key mode therefore fails closed. Disabled mode retains the existing loopback, trusted-proxy consensus, raw-socket capture, and `proxy_unauthenticated_client_cidrs` checks.

The network boundary supplies trust. The OAuth credential pair supplies call ownership and separation between admitted clients.

### Derive credential-safe affinity locally

A purpose-specific HMAC key is derived from the existing persistent encryption key. The caller scope is:

`oauth-local:HMAC-SHA256(derived-key, bearer + NUL + normalized-chatgpt-account-id)`

Only this digest participates in the existing call-owner digest. Raw bearers, account headers, call ids, SDP, attestation values, frames, audio, and transcripts remain outside persistence and diagnostics. Replicas sharing the existing encryption key derive the same scope without another setting.

A refreshed bearer produces a new caller scope. A sideband using a different bearer cannot attach an older call; the client creates a new call after credential rotation. This preserves possession-based ownership without an upstream identity request.

### Use one global policy

`oauth_live_global_policy` contains singleton id `1`, active state, and timestamps. `oauth_live_global_policy_accounts` links it to imported serving Accounts. Dashboard writes replace the complete set transactionally. Active policy writes require a non-empty known set; runtime lookup filters to currently active Accounts.

Every admitted keyless caller shares this pool. Each credential pair receives isolated affinity material, so learning another call id does not grant sideband attachment.

### Keep Key and keyless lanes compatible

Key callers keep `api_key.id` affinity, assignments, limits, reservations, last-used updates, and request-log attribution. Keyless callers select within the global pool and write request logs with `api_key_id = NULL`.

### Keep the Settings job singular

The Live Voice card answers one operator question: which upstream Accounts may serve locally admitted keyless Live calls? It exposes one global enable switch, one explicit account multi-select, and one save action. The compact selector keeps selected unavailable Accounts visible so operators can identify and remove stale assignments while other unavailable Accounts remain excluded.

## Migration and rollback

One revision creates the global singleton policy table and its allowed-account relationship table. The initial API state is disabled with an empty pool. Downgrade removes the relationship table before the singleton table.

## Risks

- Every process admitted by the existing zero-key network boundary can use the configured OAuth Live pool, matching ordinary zero-key proxy access.
- Bearer rotation changes affinity and requires a new Live call.
- Incorrect trusted-proxy configuration can alter locality decisions, so the existing raw-peer and forwarded-header regression suite remains part of acceptance.
- Experimental client route keys can drift, so each bundled Codex upgrade requires call-create, sideband, and audible Live E2E acceptance.
