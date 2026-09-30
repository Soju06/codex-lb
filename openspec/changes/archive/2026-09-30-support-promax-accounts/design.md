## Context

See context.md for verified upstream observations and source links.
Existing code already normalizes weekly-primary payloads and retains
account-specific catalog tiers. Those mechanisms remain authoritative.

## Goals / Non-Goals

Support Pro Max without inventing included allowance or changing legacy
plan estimates. No migration, configuration, new quota engine or tier bypass.

## Decisions

1. Recognize `promax` and allow Pro-family model fallback while preserving
   its wire identity and exact account catalog restrictions.
2. Use Pro reference capacity in routing state and routing-only fallback.
   Do not add Max to subscription capacity tables.
3. Add coverage metadata to existing numeric subtotals. Whole-pool weighted
   percentages become null when reported Max windows cannot be quantified.
4. Preserve individual observed percentages and suppress incomplete fleet
   pace estimates in both backend and frontend fallback paths.
5. Display Pro 500 using existing account presentation and explicit
   incomplete-allowance copy; do not redesign the dashboard.

## Risks / Trade-offs

Pro routing weight is a heuristic, not proportional fairness based on a
known allowance. Additive metadata keeps legacy response shapes compatible;
clients that ignore it will continue to see known-capacity subtotals.
The UI therefore consumes the metadata before presenting totals or forecasts.

## Verification

Focused existing backend and frontend regression tests, diagnostics,
frontend build, strict OpenSpec validation, isolated real HTTP routes with
recording upstream fixtures, actual account-catalog evidence, and desktop /
mobile screenshots. Account scope and tier-denial controls are mandatory.
