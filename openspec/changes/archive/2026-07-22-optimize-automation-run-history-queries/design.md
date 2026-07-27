## Context

`AutomationsRepository.list_run_cycles_page()` currently builds the complete effective-cycle-status graph for every request, even when no status filter is present. It then executes one statement for page IDs, a second statement for representative run rows, and a third statement for the total; the page and total each repeat the same expensive aggregation.

`list_run_filter_options()` duplicates that status graph and expands it into four independent facet statements. The service consumes only the account and model facets because status and trigger options come from canonical enums. Warm-cache measurements at 10,000 cycles / 80,000 runs show that repeated full-history work, rather than a missing index, dominates both SQLite and PostgreSQL.

The query contract is unusually subtle: candidate-cycle filters apply before whole-cycle status calculation, manual and scheduled cycles use different ordering timestamps, account filters can match snapshot-only members, and effective status depends on current account eligibility and time. The refactor must reduce repeated work without changing those semantics or introducing backend-specific SQL.

## Goals / Non-Goals

**Goals:**

- Make the common no-status grouped-history page avoid dynamic eligibility/status aggregation.
- Keep one authoritative builder for the full effective-status graph used by status-filtered pages and options.
- Return representative rows and exact totals from one SQL snapshot for non-empty pages.
- Reduce repository page selection from three statements to one and filter-option facets from four statements to one.
- Preserve behavior on SQLite and PostgreSQL, including existing filter asymmetries.

**Non-Goals:**

- Changing API parameters, response schemas, polling cadence, or dashboard rendering.
- Changing cycle-status, account-eligibility, representative-run, or ordering semantics.
- Adding indexes, schema columns, cached summaries, database views, or migrations.
- Batching `_enrich_runs_with_progress()`; that independent per-cycle enrichment hot path remains a separate follow-up.
- Unifying the historical account-facet semantics between requests with and without a status filter.

## Decisions

### Split lightweight cycle selection from dynamic status calculation

When `statuses` is empty, build only the filtered-run scope, distinct candidate cycles, representative-run ranking, and the minimum cycle aggregation required for correct ordering. The lightweight path does not join current account state or compute pending/visible outcome counts.

When `statuses` is present, use the existing full eligibility and effective-status calculation. Extract that graph into a private builder shared by page and options code so hidden manual placeholders, pending windows, deleted/completed accounts, and paused-account policy cannot drift between endpoints.

Keeping the current full graph for status-filtered requests was chosen over persisting status because effective status changes with wall-clock time and account state even when no automation row is written.

### Select page rows and total together

Use `COUNT(*) OVER ()` on the ordered cycle scope and join the representative `AutomationRun` plus job metadata in the same statement. A non-empty result therefore carries one exact total from the same database snapshot as its rows.

An offset beyond the final row has no window result from which to read the total. In that uncommon case, execute one count statement over the same cycle scope. Returning an incorrect zero or changing the public offset contract was rejected.

### Load only consumed option facets in one portable statement

Build account and model facet selects over the matching run/cycle scope, tag them with a literal facet kind, and combine them with `UNION ALL`. Execute the combined statement once and group values in Python. Status and trigger options remain the service's canonical complete enums; querying distinct stored values for them is redundant and can hide valid choices when history is sparse.

Without a status filter, account filters and both facets are evaluated directly on matching `AutomationRun` rows. With a status filter, either an observed run or snapshot membership may qualify a candidate cycle; effective status is computed over the whole cycle, and account/model facets are then expanded from observed `AutomationRun` rows in matching cycles. Snapshot-only account IDs qualify candidates but are not synthesized into facet output. Unifying that asymmetry would be an observable behavior change and is outside this performance PR.

### Do not add speculative indexes or denormalized summaries

Trials of `(cycle_key, started_at DESC, id DESC)`, `(account_id, cycle_key)`, and `(cycle_key, account_id)` indexes improved representative benchmarks by 0–6% in most cases while adding about 11 MB at 80,000 rows and increasing write amplification. They do not eliminate repeated full-history aggregation.

A persisted cycle summary would need backfill and invalidation for time passage, account pause/reactivation/rate-limit/deletion, placeholder hiding, snapshot membership changes, and claim reclamation. That risk is disproportionate when a query-only change removes the dominant work.

## Risks / Trade-offs

- [Risk] The lightweight and full scopes return different representatives or ordering. -> Share candidate-cycle and representative-run primitives and add characterization tests for manual/scheduled ordering, ties, search, model snapshots, account membership, and all filter dimensions.
- [Risk] A window count is unavailable for an offset beyond the last page. -> Use one explicit count fallback only when the page statement returns no row.
- [Risk] SQL accepted by SQLite behaves differently on PostgreSQL. -> Use SQLAlchemy Core constructs supported by both and run the same API/query-count suite against both backends.
- [Risk] Refactoring the duplicated status graph changes hidden-placeholder or eligibility semantics. -> Move the existing expressions without simplifying them and retain the full status regression matrix.
- [Risk] Query-count assertions become brittle. -> Ratchet only the repository statements this change owns; exclude later progress enrichment from the count contract.

## Migration Plan

No data or schema migration is required. Deploy the query refactor with the application. Rollback restores the previous repository implementation and reads the same tables without compatibility work.

## Open Questions

None.
