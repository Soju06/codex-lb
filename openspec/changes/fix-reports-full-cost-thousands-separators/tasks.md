## 1. Specification

- [x] 1.1 Add a `frontend-architecture` delta defining grouped rendering for full-value Reports USD surfaces while retaining intentional compact notation.

## 2. Implementation

- [x] 2.1 Route full-value Cost summary and average-per-day displays through the existing shared USD formatter.
- [x] 2.2 Route the Daily Breakdown Cost cell and Cost by Day tooltip through the existing shared USD formatter.
- [x] 2.3 Leave compact distribution-chart Cost labels and CSV numeric export unchanged.

## 3. Verification

- [x] 3.1 Add focused regression tests for four-digit full-value Cost rendering.
- [x] 3.2 Run focused Reports tests, frontend typecheck, and lint.
- [ ] 3.3 Run strict OpenSpec validation if the repository tool is available. (Not run: validator is unavailable in this checkout.)
- [x] 3.4 Run an independent GPT-5.6 SOL review on the final diff and address blocking findings.
