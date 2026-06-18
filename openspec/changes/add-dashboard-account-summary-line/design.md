## Context

The dashboard already receives an `overview.accounts` collection from `GET /api/dashboard/overview` and renders it inside the `Accounts` section through `DashboardPage` and `AccountCards`. The requested summary is a dashboard-only presentation change, so the smallest correct implementation is to derive counts locally from that existing array.

## Goals / Non-Goals

**Goals:**

- Add a compact summary line that shows registered, active, and unavailable account counts.
- Keep the change frontend-only with no API or schema additions.
- Render the summary in the dashboard `Accounts` section header using existing theme color conventions.
- Cover the behavior with focused component and page tests.

**Non-Goals:**

- Changing the Accounts page layout or account-card behavior.
- Adding new dashboard API fields or backend aggregation logic.
- Introducing new filtering, sorting, or account health semantics beyond existing status normalization.

## Decisions

### 1. Derive counts from `overview.accounts` in a small presentational component

Add `AccountSummaryLine` under the dashboard components folder and pass `overview?.accounts ?? []` from `DashboardPage`.

Rationale:

- Keeps the change isolated to the dashboard UI.
- Reuses the same account summaries the cards already render.
- Makes the count logic easy to test independently from the full page.

Alternative considered:

- Compute counts inline inside `DashboardPage`: rejected because the count formatting would be harder to unit test and reuse.

### 2. Treat only normalized `active` accounts as active

Use `normalizeStatus(account.status)` so only normalized `active` contributes to the active count. Normalized `paused`, `limited`, `exceeded`, `reauth`, and `deactivated` all count as unavailable.

Rationale:

- Matches the existing dashboard account-status presentation rules.
- Prevents duplicate status interpretation logic.

### 3. Integrate the summary into the existing Accounts header row

Render the summary at the trailing end of the existing `Accounts` header row in `DashboardPage`.

Rationale:

- Keeps the information close to the account cards it summarizes.
- Avoids adding another dashboard section or card.

### 4. Use focused tests at component and page level

Add a component test for count derivation and a page integration test proving the summary renders in the Accounts header using dashboard overview data.

Rationale:

- Component tests cover the status-count logic directly.
- The page test confirms placement and wiring without expanding unrelated dashboard coverage.

## Risks / Trade-offs

- The summary depends on the client-side overview payload already being loaded. This is acceptable because the dashboard account cards use the same data source.
- A compact header treatment leaves little room for extra explanatory text. This is intentional because the goal is quick scanability, not a new detailed status surface.
