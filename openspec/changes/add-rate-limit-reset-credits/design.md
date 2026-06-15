## Context

codex-lb already refreshes per-account upstream usage data in a shared 60-second background loop and builds both the Accounts page and dashboard account views from shared `AccountSummary` payloads. The new reset-credit feature needs to fit that shape instead of introducing a separate polling path or frontend-only query model.

The upstream endpoint is undocumented and returns a one-to-many list of reset-credit records per account. The approved scope is read-only: fetch, persist, locally expire, count, and display. Redemption via `POST /wham/rate-limit-reset-credits/consume` is explicitly deferred to a later change.

The main constraints are:

- preserve account ownership by always fetching with the corresponding account bearer token and `chatgpt-account-id`
- avoid clobbering stored upstream rows once inserted
- keep transient upstream failures from zeroing visible counts
- reuse existing account-summary APIs so dashboard and Accounts UI stay aligned

## Goals / Non-Goals

**Goals:**

- add a durable per-account store for upstream reset-credit records
- refresh reset-credit data inside the existing 60-second background refresh loop
- derive `availableResetCount` from persisted non-expired rows
- expose `availableResetCount` through shared account summary payloads used by `/api/accounts` and `/api/dashboard/overview`
- add Accounts/dashboard/header UI display, badges, and Accounts-page sort behavior
- keep the OpenSpec change and migration state fully coherent before implementation proceeds

**Non-Goals:**

- no consume/redeem workflow
- no per-credit drill-down UI
- no client-side expiry logic or optimistic updates
- no overwriting of existing stored upstream fields beyond local `expired` transitions

## Decisions

### 1. Store reset credits in a dedicated child table

Reset credits are a one-to-many account resource, so they do not fit the `accounts` row or existing usage-history tables. A dedicated `account_rate_limit_reset_credits` table keyed by `(account_id, credit_id)` preserves the upstream identity, leaves room for future redeem tracking, and supports efficient per-account count queries.

Alternatives considered:

- add JSON or count columns to `accounts`: rejected because it loses per-credit identity and makes future redemption history awkward
- store rows in `usage_history`: rejected because reset credits are not usage-window samples and have different lifecycle rules

### 2. Refresh reset credits in the existing 60-second scheduler

The existing `UsageRefreshScheduler` already owns the periodic per-account refresh pass, background session lifecycle, and failure isolation. Adding reset-credit fetches there keeps the feature synchronized with usage refresh instead of creating a second scheduler or a frontend-only poller.

Alternatives considered:

- separate background scheduler: rejected because it would duplicate account iteration and drift from usage refresh timing
- on-demand fetch from dashboard requests: rejected because UI counts would become stale and inconsistent across pages

### 3. Treat insert-once plus local expiry as the persistence rule

The approved behavior is intentionally conservative: new `(account_id, credit_id)` pairs are inserted once, existing upstream fields are not overwritten, and the only local mutation allowed is `status -> expired` when `now > expires_at`. This matches the requested dedupe rule while still allowing the visible count to decay correctly over time.

Alternatives considered:

- always upsert upstream status fields: rejected because it violates the approved “if the entry exists in the database, don’t update it” rule
- never mutate existing rows locally: rejected because expired stored credits would keep counting forever

### 4. Derive visible counts from persisted rows, not the latest upstream response

`availableResetCount` is computed from stored rows whose status is still `available` and whose `expires_at` is in the future. This makes the UI resilient to transient fetch failures and aligns with the requirement that one failed fetch must not zero visible counts.

Alternatives considered:

- trust `available_count` directly from upstream for rendering: rejected because a failed fetch would produce missing or unstable UI state
- maintain a separate cached aggregate table: rejected because the count can be derived cheaply from the new indexed child table

### 5. Reuse shared account-summary payloads for all UI surfaces

The Accounts page and dashboard already share `AccountSummary` shapes across backend and frontend. Adding `availableResetCount` there avoids introducing a new endpoint or client-side join logic, and it automatically keeps dashboard cards, account lists, and the Accounts page consistent.

Alternatives considered:

- add a new reset-credit summary endpoint: rejected because it adds avoidable fetch and cache complexity
- fetch counts only in the header: rejected because the Accounts page and dashboard need the same data

### 6. Keep spec requirements behavioral and move layout nuance to context

The OpenSpec delta specs need to remain durable and testable without freezing incidental styling. The behavioral requirements keep the disabled `Reset (N)` control, sorting, and badge visibility normative, while placement and compact circular badge styling stay in `context.md` and the implementation plan.

Alternatives considered:

- encode all visual detail in `spec.md`: rejected as too brittle for normative requirements
- remove the disabled control from the spec entirely: rejected because the approved design requires that future workflow entry point to be visible now

## Risks / Trade-offs

- Undocumented upstream endpoint may change shape or error behavior -> keep payload parsing strict for required fields, tolerant of extra fields, and cover failure paths in tests
- One extra per-account upstream fetch every 60 seconds increases scheduler work -> reuse existing failure isolation and keep count derivation database-backed so transient failures do not cascade into UI regressions
- Insert-once persistence means upstream status changes other than local expiry are not reconciled in this change -> document that consume/redeem reconciliation is deferred to the follow-up change
- Shared payload expansion affects both backend and frontend contracts -> add explicit OpenSpec contract requirements and regression tests for `/api/accounts` and `/api/dashboard/overview`

## Migration Plan

1. Add the OpenSpec change artifacts, including this design document, and keep them validated.
2. Add the Alembic revision for `account_rate_limit_reset_credits` with the foreign key, unique constraint, and count-oriented indexes.
3. Add ORM/repository support for insert-if-missing, local expiry normalization, and per-account available counts.
4. Integrate the reset-credit updater into the existing 60-second background refresh loop.
5. Extend shared account summary payloads with `availableResetCount` and wire the UI rendering/sort/badges.
6. Verify backend tests, frontend tests, and `openspec validate --specs` before considering the change ready.

Rollback strategy:

- if implementation has not shipped, revert the code and migration together
- if the migration must stay but the feature is disabled, counts can remain unused because the child table is additive and isolated from existing account-selection behavior

## Open Questions

- None for the read-only rollout. The main remaining questions belong to the future consume change: selection policy for multiple credits, `redeem_request_id` persistence, and how redeemed upstream state should reconcile with insert-once storage.
