## Context

codex-lb's quota pipeline is built around a fixed two-window model: short-window `primary` quota and long-window `secondary` quota. That assumption is encoded in usage refresh, proxy rate-limit payloads, account/dashboard mappers, trend generation, donut totals, and API-key assigned-account UI.

Free accounts no longer match that shape. Upstream now reports a single 30-day limit in `primary_window` with `limit_window_seconds == 2592000` and no `secondary_window`. The current code path tries to repair non-5h/non-7d payloads by remapping certain `primary` rows into `secondary`, which makes free accounts appear weekly-only and pollutes stored historical semantics.

This change cuts across backend normalization, persistence, migration, dashboard/account UI, and API-key dialogs, so it benefits from a technical design before implementation.

## Goals / Non-Goals

**Goals:**
- Introduce a canonical monthly-only normalization path for free-account quota.
- Preserve current paid-account 5h/7d behavior.
- Stop deriving free-account rendering from ad hoc `primary`/`secondary` slot heuristics.
- Isolate pre-change free-account usage history so new monthly semantics do not collide with legacy rows.
- Ensure account cards, overview, trends, donuts, nav progress, and API-key assigned-account UI all render the same monthly semantics.

**Non-Goals:**
- Generalizing the entire quota system to arbitrary N-window arrays.
- Changing non-free account capacity or status rules.
- Reworking unrelated dashboard layout or API-key lifecycle behavior.

## Decisions

### D1: Normalize the free-account 30d payload as an explicit monthly window

**Chosen:** Add a shared backend normalization step that recognizes `primary_window.limit_window_seconds == 2592000` with `secondary_window == null` as a monthly-only free-account quota window.

**Rationale:** The same upstream payload currently feeds usage refresh, rate-limit payloads, and dashboard/account summaries. Central normalization keeps all consumers aligned and removes repeated UI-level guesses.

**Alternative considered:** Keep the two-window model and sprinkle special cases for `2592000` across the backend and frontend. Rejected because it keeps the misleading `primary`/`secondary` semantics alive and duplicates free-account logic across many surfaces.

### D2: Extend API and frontend account models with optional monthly fields instead of replacing the two-window model wholesale

**Chosen:** Keep existing `primary` and `secondary` fields for paid accounts while adding optional monthly fields for account/dashboard-facing models.

**Rationale:** This is the smallest change that makes monthly semantics explicit without rewriting every consumer to a generic N-window structure.

**Alternative considered:** Replace all account and dashboard models with a dynamic array of windows. Rejected for this fix because it is broader than the requested behavior change and would add migration and refactor risk.

### D3: Remove semantic dependence on `primary == 604800`

**Chosen:** Stop treating `primary.limit_window_seconds == 604800` as a generic signal that the value should be interpreted as `secondary`.

**Rationale:** Window semantics must come from the explicit normalization rules, not from one special-case duration that happened to overlap with older weekly-only handling.

**Alternative considered:** Keep the current weekly-primary shortcut and bolt monthly handling on top. Rejected because the shortcut is one of the reasons the current behavior is confusing and fragile.

### D4: Migrate historical free-account rows by renaming legacy window labels

**Chosen:** Add an Alembic migration that rewrites `usage_history.window` from `primary`/`secondary` to `old-primary`/`old-secondary` for rows joined to accounts whose current plan type is `free`.

**Rationale:** This isolates pre-change history from post-change normalized monthly rows without mutating paid-account history.

**Alternative considered:** Leave old rows untouched and rely on code to infer whether a free-account row was historical or normalized. Rejected because it keeps ambiguity in the database and makes future debugging harder.

### D5: Keep 7-day trend timeframe but label monthly quota honestly

**Chosen:** Preserve the existing recent-trend timeframe while updating legends and labels so operators can see that the quota window is monthly even when the chart shows recent 7-day movement.

**Rationale:** The user explicitly requested a 7-day trend treatment for the 30d window. Keeping the timeframe avoids unnecessary chart churn while fixing semantics.

**Alternative considered:** Replace the chart timeframe with 30d whenever the account is monthly-only. Rejected because it changes more product behavior than requested.

## Risks / Trade-offs

- **Paid-account regression in 5h/7d views** -> Mitigation: branch only on the explicit monthly signature (`2592000` primary and null secondary), and add backend/frontend regression tests for normal paid accounts.
- **Model spread across many UI surfaces** -> Mitigation: centralize monthly detection in shared mappers/formatters instead of each component inventing its own rules.
- **Historical free-account rows become harder to compare with new rows** -> Mitigation: rename legacy rows explicitly so operators and developers can distinguish pre-change history from post-change monthly semantics.
- **Dashboard donuts and nav progress may under/over-count zero-credit accounts** -> Mitigation: treat zero-credit assigned accounts as excluded from the 5h/weekly donut totals and verify this with focused tests.
- **Long-window reset formatting may surface awkward countdown text** -> Mitigation: verify existing relative-time formatting against 30d reset deadlines and adjust only if the current formatter produces invalid output.

## Migration Plan

1. Add the Alembic migration that renames legacy free-account `usage_history.window` values to `old-primary` / `old-secondary`.
2. Update usage-refresh normalization and persistence so new free-account payloads are written and exposed as monthly-only semantics.
3. Update account/dashboard/API-key response models and mappers to carry monthly fields.
4. Update frontend rendering for account surfaces, overview, trends, donuts, and assigned-account UI.
5. Run backend and frontend verification covering free monthly accounts plus paid-account regression cases.

Rollback strategy:

- Code rollback is straightforward.
- Database rollback would require a follow-up data migration to rename `old-primary` / `old-secondary` back to their original values for affected free-account rows if full rollback were necessary.

## Open Questions

- None for this change. The monthly window signature, desired UI behavior, and migration scope were clarified during design review.
