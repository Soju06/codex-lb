## Context

Warmup already returns a structured result for each submitted account, but `_submit_warmup_request` conditionally re-raises `ProxyAuthError` and `ProxyRateLimitError` when the submission pool contains only one account. The production FastAPI exception handlers then return top-level 401 or 429 envelopes instead of the warmup response model.

## Goals / Non-Goals

**Goals:**
- Make ordinary auth and rate-limit failures use the existing failed-account representation for every pool cardinality.
- Preserve request logging, result ordering, bounded scheduling, and response schema.
- Prove the behavior through the production FastAPI route.

**Non-Goals:**
- Change API-key authentication for calling the warmup endpoint.
- Change account selection, eligibility, concurrency, or upstream routing.
- Change invalid-mode or strict-eligibility `ValueError` handling.
- Change global auth or rate-limit exception envelopes for other endpoints.

## Decisions

### Decision: Normalize at the existing per-account submission boundary

Always convert `ProxyAuthError` and `ProxyRateLimitError` inside `_submit_warmup_request`, where account identity and request-log fields are already available. This removes the cardinality-dependent re-raise without adding route-specific exception handling or changing global handlers.

Alternative considered: catch these exceptions in `_run_v1_warmup`. This was rejected because the route no longer has the per-account result context and would duplicate service normalization.

### Decision: Keep the existing response model unchanged

Use the existing `WarmupFailedAccountData` mapping and error codes (`auth_error` and `rate_limit_exceeded`). No API schema or scheduling changes are required.

## Risks / Trade-offs

- **[Risk] A caller may have relied on the undocumented one-account 401/429 behavior** -> **Mitigation:** the cardinality-independent HTTP 200 summary is already the normative contract and existing multi-account behavior.
- **[Risk] Broad exception handling could accidentally change unrelated failures** -> **Mitigation:** remove only the conditional re-raise for the two named exception classes and cover both through FastAPI integration tests.
