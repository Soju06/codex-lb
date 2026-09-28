# Verification: paused reset-credit visibility

## Completeness and correctness

- Three scoped production edits: live count guard, cached-detail read eligibility, summary cached visibility.
- Existing selected-account UI query renders counts; disabled reset controls remain intact.
- API tests prove successful observation retains paused state, 401 refresh/retry uses refreshed credentials, route/refresh/upstream failures retain error envelopes, cache reads retain count/expiry, and both consume routes reject paused.
- Scheduler tests retain paused polling skip and fresh-account eligibility before automatic redemption.
- Existing AuthManager and Auth Guardian paused-state preservation tests pass.

## Validation

- Focused backend suite: 123 passed (accounts API, credit API, mapper, scheduler).
- Paused credential-maintenance cases: 2 passed.
- Frontend account detail/actions/query hooks: 24 passed.
- Changed Python files: Ruff check and format check passed. `git diff --check` passed.
- Change strict validation and rate-limit-reset-credits main spec strict validation passed.
- Full `openspec validate --specs --strict`: 48 passed, 17 failed. An isolated copy of all HEAD specs produced the exact same error map; no new validation failures. Existing failures include requirements without normative keywords in account-routing and frontend-architecture. They were not modified as unrelated work.

## Review and visual evidence

Independent native reviewer `/root/paused_credit_review` (requested gpt-6-sol/high; observed model not separately exposed) reported no material findings on the frozen diff. The bound-route error integration case stubs the existing resolver failure, rather than testing actual network proxies.

Before/after synthetic-response screenshots: `docs/screenshots/paused-reset-credits/before.png` and `docs/screenshots/paused-reset-credits/after.png`. Both were visually inspected. Playwright confirmed disabled usage-reset and banked-credit buttons after showing two credits. This was a focused UI check, not a full production smoke test; unrelated runtime/telemetry/overview endpoint mocks were incomplete.

## Scope and policy

Weekly reset timestamps on paused accounts come from persisted usage. Retained credit snapshots follow that observation model, while selected-account count reads use the established credential-maintenance path. Background polling and redemption remain excluded. Reauth policy is unchanged. No production account mutation, upstream consumption, or deployment was performed.

Main specs are synchronized. No unresolved material findings; ready to archive with the pre-existing repository validation failures recorded above.

## PR preparation on upstream main

Rebased by applying the focused change onto upstream main `09a140fa9`. The same 123 backend, 2 paused-auth, and 24 frontend tests passed. `make lint` (including architecture and migration-topology checks), `make typecheck`, and `git diff --check` passed. Full specs: 49 passed, the same 17 existing failures; no new error entries. User documentation now links to the owning reset-credit spec. Screenshots use synthetic before/after API response contracts; no real credit was consumed.
