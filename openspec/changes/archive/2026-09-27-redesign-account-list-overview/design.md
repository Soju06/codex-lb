## Context

The account summary already contains the fields needed by the Grid view. The List view currently delegates to a compact `AccountListItem`, so important fields are visible only after opening details.

## Goals / Non-Goals

**Goals:** Make List view a dense, responsive, scannable overview; preserve the existing selection and detail actions; avoid per-row requests.

**Non-Goals:** Change account sorting/filter semantics, API contracts, quota calculations, or management actions.

## Decisions

- Add a dedicated `AccountListOverviewRow` used only when `viewMode="list"`; keep the compact item for legacy dashboard surfaces and Grid card headers.
- Use a CSS grid with fixed semantic columns on wide screens and stacked labeled cells on narrow screens. The row remains one keyboard-focusable button, preserving current selection behavior and privacy masking.
- Reuse `AccountSubscription`, token-state formatting and quota bars/countdowns. Render request usage totals, credits, routing policy, warm-up, workspace, and token refresh metadata from the already loaded summary.
- Use the full overview width in both modes; selecting a row opens the same detail dialog as Grid. Preserve deep links through the selected URL parameter, and remove it on close. Paginate both modes at 24 accounts to bound rendering for installations with thousands of accounts.

## Risks / Trade-offs

- More content increases row height → use compact typography and responsive columns, with aligned desktop columns and two-column labeled groups on mobile.
- Some accounts have unknown fields → use neutral em dashes/unknown labels and never infer values.
- Shared row click can conflict with nested buttons → use one row button and avoid nested interactive controls.
