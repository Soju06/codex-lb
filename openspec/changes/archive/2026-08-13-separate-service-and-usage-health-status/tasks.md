## 1. Readiness client

- [x] 1.1 Add a typed frontend client for the existing `/health/ready` response.

## 2. Status-bar presentation

- [x] 2.1 Render independent service-readiness and usage-synchronization states while preserving the existing usage freshness rule.
- [x] 2.2 Add localized labels and state text for every supported dashboard locale.
- [x] 2.3 Reserve dashboard layout space from the rendered status-bar height so wrapped rows cannot cover page content.

## 3. Regression coverage

- [x] 3.1 Add focused component tests for ready-with-stale-usage, unready-with-fresh-usage, and initial checking states.
- [x] 3.2 Add focused regression coverage for wrapped status-bar height changes.

## 4. Verification

- [x] 4.1 Run focused frontend tests, type checking, linting, OpenSpec validation, and final diff/status review.
- [x] 4.2 Re-run affected frontend checks, strict OpenSpec validation, and rendered desktop/mobile verification after the layout repair.
