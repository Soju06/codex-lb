## Context

The dashboard top stats grid builds its card metadata in `frontend/src/features/dashboard/utils.ts`. For the `Est. API Cost` card, the current meta line starts with the average cost for the selected timeframe and then appends both `API estimate` and the cached-token count when cached input exists.

The title already communicates that the metric is estimated, and cached-token volume is already represented on the Tokens card. This change is limited to the frontend display text for the existing cost card.

## Goals / Non-Goals

**Goals:**

- Make the `Est. API Cost` card meta line show only the averaged cost string.
- Preserve the existing title, total value, timeframe behavior, and comparison indicator.
- Keep the change frontend-only and covered by a focused regression test.

**Non-Goals:**

- Changing how total cost is calculated or sourced.
- Changing the Tokens card cached-token metadata.
- Adding new API fields, tooltips, or alternate labels.

## Decisions

### 1. Replace the cost-card meta formatter with the average-cost string directly

The card will reuse the existing `costAverage` string as the full meta line, with no additional suffix.

Rationale:

- This is the smallest implementation change.
- It removes both redundant phrases in one place.
- It does not require backend schema or API updates.

Alternative considered:

- Keep `API estimate` while removing cached tokens: rejected because the title already carries the estimate qualifier.

### 2. Keep cached-token context on the Tokens card only

The Tokens card will continue to surface cached-token count and percentage through `formatCachedTokensMeta`.

Rationale:

- Cached-token volume is token-specific metadata, not cost-specific metadata in the current overview payload.
- Restricting it to the Tokens card reduces duplication while keeping the information visible.

### 3. Cover the behavior with an existing dashboard view-model test

The regression test will stay in `frontend/src/features/dashboard/utils.test.ts`, where dashboard stat metadata is already asserted.

Rationale:

- The behavior is produced by the dashboard view-model builder, so that test file exercises the right level.
- A focused assertion avoids unnecessary component-test churn.

## Risks / Trade-offs

- Removing `API estimate` makes the meta line less explicit about how the value is derived. → Mitigation: the card title remains `Est. API Cost`, so the estimate qualifier is still visible.
- Future operators may want cost-specific cached-input detail. → Mitigation: add a dedicated cost field later if the backend starts exposing one; do not reuse token-count copy for that purpose.
