## 1. Beta PR creation parity

- [x] 1.1 Add helper coverage for releasable Conventional Commit detection.
- [x] 1.2 Skip beta PR creation when the latest beta tag already covers all
      releasable changes and HEAD only contains non-releasable release/meta
      commits.
- [x] 1.3 Keep creating/updating beta PRs when feat/fix/perf/deps/revert or
      breaking-change commits exist after the latest beta tag.

## 2. Beta PR changelog

- [x] 2.1 Generate beta PR changelog text from commits between the latest beta
      tag and HEAD.
- [x] 2.2 Include the changelog in the beta PR body whenever the sync workflow
      creates or updates the beta PR.

## 3. Verification

- [x] 3.1 Run unit tests for release version helpers.
- [x] 3.2 Validate OpenSpec change strictly.
- [x] 3.3 Run repo lint/typecheck gates required for release automation changes.
