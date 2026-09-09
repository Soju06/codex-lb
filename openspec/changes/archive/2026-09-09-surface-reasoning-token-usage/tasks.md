## 1. Reports contract

- [x] 1.1 Aggregate reasoning tokens in reports summary and daily rows
- [x] 1.2 Expose `totalReasoningTokens`, `reasoningUsageKnownRequests`, and daily `reasoningTokens` from the reports API
- [x] 1.3 Add backend regression coverage for filtered and unfiltered report windows
- [x] 1.4 Preserve reasoning-only output fallback and nullable all-unknown daily reasoning aggregates

## 2. Dashboard presentation

- [x] 2.1 Render reasoning usage in request rows and request details
- [x] 2.2 Render reported reasoning totals and summary coverage in reports
- [x] 2.3 Include reasoning tokens in the daily CSV export
- [x] 2.4 Add English, Korean, and Simplified Chinese labels
- [x] 2.5 Render and export all-unknown daily reasoning usage distinctly from known zero
- [x] 2.6 Document token-bucket semantics, dashboard surfaces, and missing-usage behavior in the docs site

## 3. Validation

- [x] 3.1 Run focused backend and frontend tests
- [x] 3.2 Run backend lint/type checks and frontend typecheck/build
- [x] 3.3 Validate OpenSpec
