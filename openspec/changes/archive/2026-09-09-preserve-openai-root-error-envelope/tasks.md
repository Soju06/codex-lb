## 1. Regression coverage

- [x] 1.1 Add route-level exact, slash, and child coverage for both OpenAI path families.
- [x] 1.2 Capture exact roots failing with Starlette's generic detail payload.

## 2. Error classification

- [x] 2.1 Include exact `/v1` and `/backend-api` roots in the fallback classifier.
- [x] 2.2 Preserve existing non-OpenAI and descendant-path behavior.

## 3. Verification

- [x] 3.1 Run focused route regressions and non-OpenAI controls.
- [x] 3.2 Run changed-file lint, formatting, type, and strict OpenSpec checks.
- [x] 3.3 Exercise exact and equivalent paths through a live HTTP server.
- [x] 3.4 Record cleanup and publication evidence.
