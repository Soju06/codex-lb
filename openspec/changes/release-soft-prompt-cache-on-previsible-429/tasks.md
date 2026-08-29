## 1. Streaming retry

- [x] 1.1 After a first-event `failover_next` exclusion, set
      `reallocate_sticky=True` when the request is not file-pinned,
      turn-state-owned, or otherwise required-owner bound, unless
      `_move_verified_fresh_replay_from_owner` already released it
- [x] 1.2 Apply the same release on the post-refresh `failover_next` path

## 2. Tests

- [x] 2.1 Add a `stream_responses` regression: inline image +
      `prompt_cache_key` + pre-visible 429 reallocates and completes on a
      second image-capable account
- [x] 2.2 Add a file-pin counterpart that stays fail-closed on the owner
      after the same 429

## 3. Specs

- [x] 3.1 Sync the delta requirement into
      `openspec/specs/responses-api-compat/spec.md`
- [x] 3.2 Record the hole and example in
      `openspec/specs/responses-api-compat/context.md`
- [x] 3.3 Validate the scoped OpenSpec change
