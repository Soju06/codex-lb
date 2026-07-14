## 1. Runtime token fallback

- [x] 1.1 Detect `API rate limit exceeded` failures in the gh wrapper and switch once to `GH_FALLBACK_TOKEN`, retrying the failed call.
- [x] 1.2 Provide `github.token` as `GH_FALLBACK_TOKEN` in both label-sync workflow jobs.
- [x] 1.3 Unit coverage: fallback activates once and retries; no-op when the fallback is absent or identical; exhausted fallback still fails.

## 2. Validation

- [x] 2.1 Run the sync-script unit suite.
- [x] 2.2 Validate with `openspec validate label-sync-rate-limit-fallback --strict`.
