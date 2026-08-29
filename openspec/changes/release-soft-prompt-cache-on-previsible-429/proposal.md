# Why

A streaming Responses request with an inline image and a soft `prompt_cache_key`
treats a pre-visible upstream HTTP 429 as retryable and excludes the failed
account, but leaves prompt-cache affinity pinned to that excluded account. The
next selection pass then reports no eligible accounts even when another
image-capable account can serve the request (#1924).

# What Changes

- On a pre-visible streaming `failover_next` (including HTTP 429), release
  soft prompt-cache affinity by setting `reallocate_sticky=True` after the
  failed account is excluded, matching the adjacent pre-dispatch failover path.
- Keep file-pinned, turn-state, and other required-owner requests fail-closed;
  they MUST NOT cross accounts.
- Add a product-path regression for inline-image + `prompt_cache_key` 429
  failover, plus a file-pin fail-closed counterpart.
- Document the recovery rule in `responses-api-compat`.

This change does **not** add settings, mix probe/warmup work, or change
post-visible replay rules.

# Capabilities

### New Capabilities

- None

### Modified Capabilities

- `responses-api-compat`: a pre-visible 429 on a soft prompt-cache streaming
  request MUST release that affinity and retry an eligible compatible account;
  file-owned requests remain account-pinned.

# Impact

- `app/modules/proxy/_service/streaming/retry.py` (`failover_next` after
  pre-visible first-event and post-refresh failures)
- Streaming unit coverage at `ProxyService.stream_responses`
- `openspec/specs/responses-api-compat/spec.md` and its context notes
- No new `CODEX_LB_*` settings, API fields, or dashboard surfaces
