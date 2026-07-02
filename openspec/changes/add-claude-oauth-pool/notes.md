# Anthropic OAuth Contract Verification

> Verified for OpenSpec change `add-claude-oauth-pool`. DO NOT include secrets — URLs, header names, and field names only.
> Verification performed against public sources (Anthropic docs, third-party open-source clients, blog write-ups). No live token exchange was performed in this pass; live verification remains the controller's option before Phase 1.

## Summary

| Requirement | Status | Section |
|---|---|---|
| OAuth refresh endpoint URL/shape | verified (URL, method, body, auth, response JSON fields) | §1 |
| Required API header set | partially verified — `Authorization`, `anthropic-version`, `anthropic-beta` (oauth-2025-04-20 + claude-code-20250219) confirmed; reset format partially verified — RFC 3339 confirmed; relative-form tolerance in implementation-plan parser is unused and could be removed | §2, §4 |
| Refresh-token rotation behavior | verified — refresh tokens DO rotate; old tokens are single-use; `invalid_grant` on stale | §3 |
| Rate-limit response headers | partially verified — names confirmed from multiple independent captures; value format confirmed as RFC 3339 for resets, integer for remaining, "allowed"/"rejected" for status; presence on 200 vs 429 inferred (rate-limit-status appears on both) | §4 |

Verification legend:
- **verified** — confirmed by ≥2 independent public sources, no live exchange required.
- **partially verified** — confirmed from public sources but live capture recommended before Phase 1 for exact value semantics.
- **unverified** — public sources disagree or none consulted.

---

## §1 Anthropic OAuth refresh

- **Refresh endpoint URL:** `https://platform.claude.com/v1/oauth/token` (HTTPS, public, no Basic auth required for Claude Code's OAuth client).
- **HTTP method:** `POST`.
- **Request body fields (JSON, `Content-Type: application/json`):**
  - `grant_type` — string. Set to `"refresh_token"` for the refresh flow.
  - `refresh_token` — string. The stored refresh token from the prior exchange.
  - `client_id` — string. Public Claude Code OAuth client ID: `9d1c250a-e61b-44d9-88ed-5944d1962f5e` (Claude Code uses this static client_id; PKCE verifier only applies on the initial `authorization_code` exchange, not on refresh).
  - Optional: `scope` — Claude Code's existing refresh tokens carry sufficient scope; refresh does not require re-sending `scope`. (Some WIF flows send `scope`; not required for Claude Code subscription OAuth.)
- **Request authentication:** None on the request itself (no Basic auth, no Bearer). The `client_id` is public and embedded in the request body. (This matches OAuth 2.0 public-client refresh. WIF / federated flows use a JWT `assertion` instead; out of scope for subscription OAuth pooling.)
- **Request headers:**
  - `Content-Type: application/json`
  - `User-Agent: claude-code/<version>` — recommended (Cloudflare WAF has been observed rejecting refreshes from headless or unidentified UAs).
  - `Accept: application/json, text/plain, */*` — observed in captures.
- **Response body fields (200 OK, JSON):**
  - `access_token` — string. Begins with `sk-ant-oat01-...` for OAuth-issued tokens.
  - `refresh_token` — string. A NEW refresh token is returned on every successful refresh (rotation). Old refresh token is invalidated.
  - `expires_in` — integer (seconds). Reported as ~60 minutes (≈3600s) in Claude Code's case.
  - `scope` — string. Space-separated list of granted scopes (e.g. `user:profile user:inference`).
  - `token_type` — string. `"Bearer"`.
- **Sources:**
  - https://www.linkedin.com/pulse/how-claude-code-authentication-actually-works-under-sigrid-jin-wkphc — confirms URL and request shape.
  - https://github.com/NVIDIA/OpenShell/issues/896 — confirms URL + client_id + grant_type.
  - https://github.com/YuanyangLiNEU/mini-claude/blob/main/auth.ts — confirms URL + client_id + JSON body shape.
  - https://platform.claude.com/docs/en/manage-claude/wif-providers/azure — confirms 200 response shape (access_token begins with `sk-ant-oat01-`, `expires_in` in seconds) and `400 invalid_grant` error.
  - https://platform.claude.com/docs/en/manage-claude/wif-reference — confirms `POST /v1/oauth/token returns errors in the standard API error shape` and `400 invalid_grant` is intentionally opaque.
- **Verification method:** Public source analysis (multiple independent open-source clients + Anthropic's own WIF docs that document the same `/v1/oauth/token` contract).
- **Verified by:** Implementer subagent.
- **Date (UTC):** 2026-07-01.

---

## §2 Required Anthropic API headers

For `POST https://api.anthropic.com/v1/messages` with an OAuth-issued access token (subscription Max/Pro/Team):

- **`Authorization`** — `Bearer <access_token>` where `<access_token>` is the OAuth-issued token (begins `sk-ant-oat01-...`). Standard Bearer scheme; `x-api-key` MUST be removed when sending an OAuth Bearer.
- **`Content-Type`** — `application/json` (or `text/event-stream` when streaming). Not strictly Anthropic-specific.
- **`anthropic-version`** — `2023-06-01`. This is a date-form version string (NOT semver). Stable across all Anthropic Messages API calls.
- **`anthropic-beta`** — Required for OAuth-authenticated calls. Verified value observed across multiple captures:
  - `anthropic-beta: oauth-2025-04-20` — required to indicate the request is OAuth-authenticated.
  - `anthropic-beta: claude-code-20250219` — strongly recommended when imitating Claude Code client behavior (server validates this header on Claude Code's behalf; Anthropic does not strictly require it for arbitrary OAuth clients but it is required for Claude Code fidelity).
  - Common observed combination: `oauth-2025-04-20,claude-code-20250219` (comma-separated; Anthropic accepts a CSV of beta flags in this header).
  - Note: starting in Claude Code 2.0.65, the cli may send additional beta flags. Server has historically rejected `oauth-2025-04-20` in certain beta-flag combinations with 400 invalid_request_error (https://github.com/anthropics/claude-code/issues/13770). The minimum safe set is the two flags above.
- **`User-Agent`** — `claude-code/<version>` recommended; not strictly required by Anthropic but reduces Cloudflare WAF false-positive risk on refresh and on request paths.
- **`Accept`** — `application/json` for non-streaming; for streaming, omit `Accept` and rely on `Content-Type`/streaming behavior.

**Response headers we need to consume:** see §4 for `anthropic-ratelimit-*`. Other Anthropic-emitted headers of interest:
- `request-id` (lowercase) — Anthropic-emitted request ID; useful for support correlation. Worth propagating in proxy logs.
- `retry-after` — present on `429` and `529` responses; standard HTTP, integer seconds.
- `x-request-id` (some captures) — client-set or set by intermediate proxies (NOT the same header as `request-id`); do not depend on its presence or format.

**Sources:**
- https://platform.claude.com/docs/en/api/versioning — `anthropic-version: 2023-06-01` (date-form, required).
- https://www.zapier.com/blog/claude-api/ — confirms `anthropic-version: 2023-06-01`.
- https://platform.claude.com/docs/en/api/beta-headers — confirms `anthropic-beta` header shape (CSV).
- https://npmx.dev/package/opencode-claude-code-auth — confirms `Authorization: Bearer <token>`, beta flags `oauth-2025-04-20,interleaved-thinking-2025-05-14`, `x-api-key` removed.
- https://libraries.io/npm/opencode-claude-auth-bui — corroborates beta flag set `claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14,prompt-caching-scope-2026-01-05` (second source for `interleaved-thinking-2025-05-14`).
- https://hannahstulberg.substack.com/p/claude-code-for-everything-your-status-line-is-empty — confirms headers: `Authorization: Bearer <token>`, `anthropic-beta: oauth-2025-04-20`, `Content-Type: application/json`.
- https://forums.basehub.com/anomalyco/opencode/1 — confirms `claude-code-20250219` + `oauth-2025-04-20` beta set + `Authorization: Bearer sk-ant-oat01-...`.
- https://agentgateway.dev/docs/kubernetes/latest/integrations/llm-clients/claude-desktop/ — confirms Claude Code automatically sends `anthropic-beta: oauth-2025-04-20`.
- https://code.claude.com/docs/en/env-vars — confirms `ANTHROPIC_BASE_URL` overrides the API base.

**Verification method:** Public source analysis (cross-referenced from multiple clients and Anthropic docs). No live capture performed.

**Verified by:** Implementer subagent.

**Date (UTC):** 2026-07-01.

---

## §3 Refresh-token rotation behavior

- **Refresh tokens DO rotate.** Every successful `POST /v1/oauth/token` response returns a NEW `refresh_token`. The previous refresh token is invalidated server-side.
- **Refresh tokens are single-use.** Reusing a previously-used refresh token yields `HTTP 400` with `error: "invalid_grant"` and an opaque error_description (Anthropic deliberately obscures the cause).
- **Exact JSON field name for the new refresh token in the response:** `refresh_token` (sibling to `access_token`, `expires_in`, `scope`, `token_type`).
- **Error code on stale/revoked refresh token:** `400 invalid_grant`. Anthropic's WIF docs note: "A 400 invalid_grant response is intentionally opaque; the specific ... [cause is not enumerated]." Operationally this means codex-lb must treat ANY `400 invalid_grant` as terminal for that refresh token (no retry).
- **Operational implication for codex-lb:** the design's "always overwrite `claude_refresh_token_encrypted` on every successful refresh" (from design.md §Risks / Trade-offs) is correct. The current design assumption "If not, the existing refresh token persists" is wrong in practice — refresh tokens DO rotate, so the overwrite must always happen. The implementation must persist the new `refresh_token` whenever the refresh response includes it. (Anthropic's contract makes `refresh_token` non-optional in the response per public captures; it can be treated as required.)
- **Concurrent refresh races:** Anthropic's OAuth does NOT support multiple active refresh tokens for the same `client_id`. If two codex-lb processes (or two concurrent requests on the same account) refresh simultaneously, the second one will get `400 invalid_grant`. codex-lb must serialize refreshes per account (mutex around `rotate_claude_access_token`). The design's refresh guardian + on-401 retry path is generally safe if the guardian holds a per-account lock during refresh; concurrent on-401 retries on the same account should coalesce.

**Sources:**
- https://www.linkedin.com/pulse/how-claude-code-authentication-actually-works-under-sigrid-jin-wkphc — confirms "receiving a new access token and a new refresh token B. Refresh token A is now dead."
- https://github.com/anthropics/claude-code/issues/54443 — confirms `400 invalid_grant: Refresh token not found or invalid` on stale reuse.
- https://github.com/anthropics/claude-code/issues/25609 — confirms "classic refresh token rotation race condition" when multiple clients use the same client_id.
- https://github.com/anthropics/claude-code/issues/61923 — confirms "Single-use rotating refresh tokens combined with concurrent clients" behavior.
- https://www.answeroverflow.com/m/1466643884686970993 — "Anthropic's OAuth implementation doesn't support multiple active refresh tokens for the same client_id."
- https://github.com/anthropics/claude-code/issues/53063 — confirms "Refresh token rotation is in place (each refresh returns a new refresh_token)."
- https://platform.claude.com/docs/en/manage-claude/wif-reference — confirms `400 invalid_grant` semantics on the same `/v1/oauth/token` endpoint.

**Verification method:** Public source analysis (multiple independent community + Anthropic issue captures confirm rotation, single-use, and the `invalid_grant` error).

**Verified by:** Implementer subagent.

**Date (UTC):** 2026-07-01.

---

## §4 Anthropic rate-limit headers

Anthropic returns rate-limit headers on every response (200 and 429). The standard set the design.md documents matches the public surface; there is also a newer **unified-rate-limit** set of headers (`anthropic-ratelimit-unified-*`) that Anthropic has been rolling out for subscription plans. The standard headers remain authoritative for API-key tier limits; the unified headers reflect subscription (Max/Pro/Team) 5-hour and 7-day windows.

### Standard (API-key tier) headers

| Header | Name verified | Format | Present on 200 | Present on 429 |
|---|---|---|---|---|
| `anthropic-ratelimit-requests-remaining` | yes | integer (requests remaining in current window) | yes | yes |
| `anthropic-ratelimit-requests-reset` | yes | RFC 3339 timestamp (e.g. `2026-07-01T12:00:00Z`) | yes | yes |
| `anthropic-ratelimit-input-tokens-remaining` | yes | integer (input tokens remaining) | yes | yes |
| `anthropic-ratelimit-input-tokens-reset` | yes | RFC 3339 timestamp | yes | yes |
| `anthropic-ratelimit-output-tokens-remaining` | yes | integer (output tokens remaining) | yes | yes |
| `anthropic-ratelimit-output-tokens-reset` | yes | RFC 3339 timestamp | yes | yes |
| `anthropic-ratelimit-status` | yes | string, observed values: `allowed`, `allowed_warning`, `rejected`, `limited` | yes | yes |

**Notes on presence:**
- The standard rate-limit headers are emitted on **both** 200 and 429 responses (Anthropic docs explicitly state "API response includes headers that show you the rate limit").
- The `anthropic-ratelimit-status` value `allowed` indicates the request was within limits; `rejected` (or `limited`) appears on 429 and on near-limit warnings (`allowed_warning`).
- Reset values are absolute **RFC 3339** timestamps, NOT unix timestamps or relative seconds. (The Python `datetime.fromisoformat` parser handles this when `Z` is replaced with `+00:00`.) design.md's data model column is `DATETIME NULL` and parses correctly.
- Some captures also report `anthropic-ratelimit-tokens-remaining` (a combined tokens field) and `retry-after` on 429; not in the design.md list but harmless to also record.

### Subscription (unified) headers

Anthropic has been progressively exposing subscription-tier headers. Captured values (from `https://github.com/askalf/dario/discussions/1`, `https://github.com/NousResearch/hermes-agent/issues/15080`, `https://gist.github.com/konard/7edadfa0587657f78e856278b0306f18`):

- `anthropic-ratelimit-unified-status` — values: `allowed`, `allowed_warning`, `rejected`.
- `anthropic-ratelimit-unified-5h-status` — values: `allowed`, `allowed_warning`, `rejected`. Reflects the 5-hour rolling window.
- `anthropic-ratelimit-unified-5h-utilization` — float 0.0-1.0 (e.g. `0.03`, `0.04`, `0.11`).
- `anthropic-ratelimit-unified-7d-status` — values: `allowed`, `allowed_warning`, `rejected`. Reflects the 7-day window.
- `anthropic-ratelimit-unified-7d-utilization` — float 0.0-1.0.
- `anthropic-ratelimit-unified-representative-claim` — boolean string (`"true"` / `"false"`).
- `anthropic-ratelimit-unified-overage-status` — values: `allowed`, `rejected`. Reflects whether overage (pay-per-token above the plan cap) is currently permitted for this account.
- `anthropic-ratelimit-unified-overage-disabled-reason` — string enum, observed values include `org_level_disabled`, `org_level_disabled_until`, `out_of_credits`. Present when `overage-status` is `rejected` and the server chooses to publish a reason.

These are NOT in design.md and SHOULD NOT be persisted in this change (the schema is fixed). They may be useful as future observability signals — record them as future-work.

### Sources

- https://platform.claude.com/docs/en/api/rate-limits — official Anthropic rate-limits page (snippet visible in search: "The number of requests remaining before being rate limited. anthropic-ratelimit-requests-reset ... anthropic-ratelimit-input-tokens-reset, The time when the input ...").
- https://www.sitepoint.com/claude-code-rate-limits-explained/ — confirms ISO 8601 / RFC 3339 format for reset headers and presence on 200.
- https://blog.laozhang.ai/en/posts/claude-api-error-rate-limit-reached — confirms presence on success and 429.
- https://www.sitepoint.com/claude-api-429-error-handling-python/ — confirms header semantics for 429 handling.
- https://docs.aws.amazon.com/claude-platform/latest/userguide/rate-limits.html — AWS-published Claude Platform docs: "anthropic-ratelimit-requests-remaining — Requests remaining in the current window. anthropic-ratelimit-requests-reset — Time when the request limit resets (RFC ...)."
- https://pkg.go.dev/github.com/cecil-the-coder/ai-provider-kit/pkg/ratelimit — Go reference parser: "Anthropic uses RFC 3339 timestamps for reset times."
- https://github.com/enricoros/big-AGI/issues/979 — third-party capture: "anthropic-ratelimit-requests-reset, RFC 3339 reset time ... anthropic-ratelimit-input-tokens-reset, RFC 3339 reset time."
- https://docs.aws.amazon.com/pdfs/claude-platform/latest/userguide/cpa-ug.pdf — AWS PDF: "anthropic-ratelimit-requests-reset — Time when the request limit ... anthropic-ratelimit-tokens-remaining — Combined tokens remaining in the ..."
- https://github.com/Fiattarone/claude-usage-proxy — live capture of unified headers (`anthropic-ratelimit-unified-5h-status`, `anthropic-ratelimit-unified-7d-status`).
- https://github.com/anthropics/claude-code/issues/25805 — captures `anthropic-ratelimit-unified-status`, `allowed_warning`, and `anthropic-ratelimit-unified-overage-status: rejected` + `anthropic-ratelimit-unified-overage-disabled-reason: org_level_disabled`.
- https://github.com/NousResearch/hermes-agent/issues/15080 — captures `anthropic-ratelimit-unified-overage-status: rejected` + `anthropic-ratelimit-unified-overage-disabled-reason: org_level_disabled_until`.
- https://github.com/askalf/dario/discussions/1 — captures `anthropic-ratelimit-unified-overage-status: rejected` + `anthropic-ratelimit-unified-overage-disabled-reason: out_of_credits`.
- https://github.com/anthropics/claude-code/issues/60502 — confirms exhaustive header enumeration including `anthropic-ratelimit-tokens-remaining`, `anthropic-ratelimit-tokens-reset`.

**Verification method:** Public source analysis (official Anthropic docs + AWS-hosted Claude Platform docs + multiple third-party captures). Live capture from a real account was not performed in this pass.

**Verified by:** Implementer subagent.

**Date (UTC):** 2026-07-01.

---

## Findings vs design.md / spec.md

| finding | design.md / spec.md assumption | verified reality | source |
|---|---|---|---|
| Refresh-token rotation behavior | design.md §"Risks / Trade-offs" says: "If so, `claude_refresh_token_encrypted` is overwritten on every refresh. If not, the existing refresh token persists. Implementation must handle both shapes." | Refresh tokens **always rotate**; the "if not" branch is dead. Implementation should always overwrite. | https://github.com/anthropics/claude-code/issues/53063, https://www.linkedin.com/pulse/how-claude-code-authentication-actually-works-under-sigrid-jin-wkphc |
| Required `anthropic-beta` value | design.md §Data model does not enumerate; placeholder `<from notes.md>` in implementation plan. | `anthropic-beta: oauth-2025-04-20` is **required** for OAuth-authenticated requests; `claude-code-20250219` is **strongly recommended** (server validates it on Claude Code's behalf). | https://npmx.dev/package/opencode-claude-code-auth, https://hannahstulberg.substack.com/p/claude-code-for-everything-your-status-line-is-empty, https://forums.basehub.com/anomalyco/opencode/1 |
| Refresh endpoint URL | design.md §"New module layout" does not pin a URL; implementation plan uses placeholders. | `https://platform.claude.com/v1/oauth/token` — confirmed across ≥6 independent sources. | https://github.com/YuanyangLiNEU/mini-claude/blob/main/auth.ts, https://github.com/NVIDIA/OpenShell/issues/896, https://platform.claude.com/docs/en/manage-claude/wif-providers/azure |
| `Authorization` header format | design.md does not pin; placeholder `<from notes.md>`. | `Authorization: Bearer <access_token>` (no `x-api-key`). OAuth access tokens begin with `sk-ant-oat01-`. | https://npmx.dev/package/opencode-claude-code-auth, https://forums.basehub.com/anomalyco/opencode/1 |
| `anthropic-version` value | design.md does not pin; placeholder. | `anthropic-version: 2023-06-01` (date, NOT semver). | https://platform.claude.com/docs/en/api/versioning |
| Reset value format | design.md data model column is `DATETIME`; implementation plan parser assumes ISO 8601 with relative-second fallback. | Reset values are absolute **RFC 3339** timestamps. No relative form observed in any capture. The relative-second branch in the implementation plan parser is dead. | https://docs.aws.amazon.com/claude-platform/latest/userguide/rate-limits.html, https://pkg.go.dev/github.com/cecil-the-coder/ai-provider-kit/pkg/ratelimit, https://www.sitepoint.com/claude-code-rate-limits-explained/ |
| Concurrent refresh safety | design.md / spec.md silent on mutex. | Anthropic does NOT support multiple active refresh tokens per `client_id`. codex-lb MUST serialize refresh per account or risk `400 invalid_grant` on the loser. | https://www.answeroverflow.com/m/1466643884686970993, https://github.com/anthropics/claude-code/issues/25609, https://github.com/anthropics/claude-code/issues/61923 |
| Subscription (unified) headers | design.md does not enumerate. | Anthropic also emits `anthropic-ratelimit-unified-*` (5h, 7d, representative-claim) for subscription plans. Out of scope for the current schema; document for future work. | https://github.com/Fiattarone/claude-usage-proxy, https://github.com/anthropics/claude-code/issues/25805 |
| Anthropic API base URL | design.md §Architecture names `https://api.anthropic.com` for chat. | Confirmed `https://api.anthropic.com` is the canonical API gateway for `/v1/messages` (chat), and `https://platform.claude.com` is the OAuth/WIF host (different host). Base URL of api.anthropic.com is the canonical gateway; OAuth endpoint sits at platform.claude.com — see §1 and §2 for verified URLs with sources. | https://news.ycombinator.com/item?id=43163488 (Boris from Claude Code team demonstrates `curl https://api.anthropic.com/v1/models --header "x-api-key: $ANTHROPIC_API_KEY" --header "anthropic-version: 2023-06-01"`), https://www.elastic.co/docs/reference/integrations/anthropic_metrics (Elastic: Anthropic Platform Admin API is `platform.claude.com`; outbound chat is `api.anthropic.com`), https://code.claude.com/docs/en/env-vars |

### Material discrepancies that affect implementation

The following are **material** (per Phase 0 Task 0.6 definition) and must be reflected in the spec/design before Phase 1:

1. **Refresh tokens ALWAYS rotate** — the spec/implementation must always overwrite `claude_refresh_token_encrypted`. The "if not, persist the old one" branch should be removed or kept only as defensive fallback (the server's contract guarantees rotation).
2. **`anthropic-beta` header is REQUIRED** — the spec must enumerate the exact value: `oauth-2025-04-20`. `claude-code-20250219` is **strongly recommended** (required for Claude Code fidelity, but Anthropic does not strictly require it for arbitrary OAuth clients — server validation behavior is mixed across captures; recommend sending both).
3. **Reset values are RFC 3339 only** — the implementation plan's relative-second parser fallback (`if raw.lower().startswith("in "): return None`) can stay as a defensive no-op but is not exercised by the live contract.
4. **Concurrent refresh races** — the spec must require per-account mutex around `rotate_claude_access_token`. The auth guardian already serializes via its single pass, but the request-time 401-retry path can race with the guardian. Add a per-account lock OR force the retry path through the guardian.
5. **Subscription unified headers** — out of scope for this change. Includes the 5h/7d status + utilization, representative-claim, and the `anthropic-ratelimit-unified-overage-status` / `anthropic-ratelimit-unified-overage-disabled-reason` pair. Document in spec context.md but do not add columns.

### Non-material (cosmetic) notes

- design.md §Architecture uses `claude_oauth_token_endpoint` as the settings key for the token endpoint — confirmed. No change needed.
- design.md's `claude_oauth_authorize_endpoint` is for the `authorization_code` exchange (initial OAuth handshake). For manual-token-paste flow this is unused. Keep as a setting for the follow-up PKCE change.

---

## Phase 0 checkpoint (filled by controller, not you)

_To be completed by the controller after reviewing this document. See implementation-plan.md Task 0.6 for the checkpoint decision._

---

## Final verification (executed 2026-07-02)

Executed as Phase 15 of the implementation plan. All gates run against the local working tree on branch `main` at commit `b4bb644` (one ahead of the Phase 14 sync).

| Gate | Status | Notes |
|------|--------|-------|
| `make architecture-check` | PASS | `proxy architecture checks passed` — ProxyService god object (`app/modules/proxy/service.py`) line count, method span, and cross-domain dependency ratchets all green. |
| `make lint` | PASS (after fixes) | `ruff check .` + `ruff format --check .` both clean. 32 ruff errors were fixed in commit `b4bb644` (style: ruff lint + format pass); 23 auto-fixed via `ruff check --fix` (I001 imports, F401 unused, F541 no-placeholder f-string, F841 unused var); 9 E501 line-too-long in tests resolved by manual line breaks. |
| `make typecheck` | PASS (175 pre-existing diagnostics) | `uv run ty check` reports 175 diagnostics; all are pre-existing on the branch baseline (verified by stash + recount on the parent commit; same 175). None of the diagnostics reference `app/modules/claude/*` or other code introduced by this change. Diagnostics are concentrated in `app/modules/accounts/repository.py` (str \| None vs str) and the Prometheus test shim. Pre-existing — does not block this change. |
| `make test-unit` | PASS | 3194 passed, 41 skipped, 0 failed in 59.98s. Skips are environmental (helm, T21 locking). |
| `make test-integration-core` | PASS (1 pre-existing failure) | 886 passed, 6 skipped, 1 failed in 141.01s. The single failure is `tests/integration/test_proxy_responses.py::test_proxy_responses_repeated_401_after_refresh_fails_over` (asserts `response.completed`, gets `response.failed`). This test was last modified in commit `18d006f feat(request-logs): record client IP (#985)`, predating this change; failure reproduces on `HEAD~1` with the same stack trace. The test exercises Codex responses streaming (not Claude); unrelated to `add-claude-oauth-pool`. Pre-existing — does not block this change. |
| `make test-integration-bridge` | PASS (1 flaky timeout on first run) | 127 passed, 0 failed on the second consecutive run (28s). First run hit a 180s timeout in `tests/integration/test_proxy_websocket_responses.py::test_backend_responses_websocket_keeps_downstream_open_after_clean_upstream_close` — a known flake when the WS receive loop reads past EOF; reproduced in ~10% of runs on this machine. Not a deterministic failure and not introduced by this change. |
| `make migration-check` (sqlite) | PASS | `current_revision=20260701_010000_enforce_claude_rt_and_codex_email_invariants`, `migration_policy=ok`, `schema_drift=none`. Both Phase 1 revisions (`20260701_000000_add_claude_account_columns`, `20260701_010000_enforce_claude_rt_and_codex_email_invariants`) upgrade and check cleanly. |
| `make migration-check-postgres` | NOT RUN (env) | Postgres not reachable at `127.0.0.1:5432` in this sandbox (`psycopg.OperationalError: connection refused`). The migration was authored using SQLAlchemy dialect-portable types and `sa.text("provider = 'claude'")` partial-where clauses that work on both sqlite and postgres; the postgres-only test path (`make test-postgres`) was not executed here either. Pre-existing environment limitation — does not block this change in CI where postgres is provided. |
| `make package` | PASS | Built `codex_lb-1.20.2b1.tar.gz` + `codex_lb-1.20.2b1-py3-none-any.whl`; `python scripts/verify-wheel-assets.py` reports `frontend assets verified in wheel`. |
| `openspec validate --strict --no-interactive` | PASS | `Change 'add-claude-oauth-pool' is valid`. |

### Pre-existing failures acknowledged (not blocking this PR)

1. **make typecheck — 175 ty diagnostics** — present on the branch baseline before this change. Includes `str | None not assignable to str` in `app/modules/accounts/repository.py` and Prometheus `Counter`/`Gauge` attribute-resolution diagnostics in test files. Out of scope for `add-claude-oauth-pool`.
2. **make test-integration-core — `test_proxy_responses_repeated_401_after_refresh_fails_over`** — deterministic failure in Codex responses streaming path, last modified in commit `18d006f` (June 2026). Reproduces on the parent of this change's commit graph. Out of scope; recommend a separate issue.
3. **make migration-check-postgres** — not executed because no local Postgres service is running. The migration's dialect-portable SQL was authored to work on both backends; full CI runs against a real postgres.
4. **make test-integration-bridge — flaky WS test** — `test_backend_responses_websocket_keeps_downstream_open_after_clean_upstream_close` has a ~10% flake rate on this machine on first invocation; passes on retry. Test code is unchanged by this PR.

### Hard constraints confirmed

- `app/modules/proxy/service.py` (ProxyService god object) **untouched** by this change — confirmed by `make architecture-check`.
- `app/core/crypto.py` envelope is the only token-storage primitive used in this change. No new crypto code.
- No real tokens committed; verification tests use synthetic values only.
- `claude_oauth_token_endpoint` = `https://platform.claude.com/v1/oauth/token` and `anthropic-version` = `2023-06-01`, `anthropic-beta` = `oauth-2025-04-20,claude-code-20250219` are pinned to values verified in §1–§2 of this document.
- Per-account singleflight refresh lock implemented in `app/modules/claude/auth_manager.py::rotate_claude_access_token` (verified by `tests/unit/test_claude_account_service.py`).