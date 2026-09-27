## Context

Accounts already shares one sort mode between Detail, List, Grid and selected-account fallback. The minimal List has four groups; a subsequent operator request restores its available reset count while preserving its short height.

## Goals / Non-Goals

**Goals:** Sort by Plan, Subscription, 5h and 7d remaining quota; show available reset counts compactly; preserve filters, selection and pagination behavior.

**Non-Goals:** API changes, billing requests, persisted sort settings, deployment, or adding token/request metadata back to List.

## Decisions

- Extend the existing typed sort modes and comparator. Plan uses alphabetic plan labels (A–Z/Z–A) rather than an arbitrary paid-tier ranking. Subscription uses its recorded deadline; comparing deadlines orders remaining time without extra timers. Each quota mode reads its own remaining percent independently of display preference.
- Missing numeric data sorts last in either direction; zero percent is real data. Missing/unknown plans sort last. Keep existing stable reset/name/id tie breakers. Existing default remains Most reset credits; correct the stale default wording in the older explicit-sort requirement.
- Native desktop header buttons show arrows, active state and an accessible direction description. The existing dropdown exposes all modes for mobile. Both paths reset pagination and use the same shared mode.
- A small Reset (N) badge sits beside plan/status and uses the already-loaded availableResetCredits. Positive counts respect showResetCreditBadges; zero/missing counts omit the badge, as in the original selector. It is informational, so clicking the row still opens details without redeeming a credit.

## Risks / Trade-offs

- Missing monthly-only account windows must remain unknown for 5h/7d sorts; never substitute monthly quota.
- An elapsed subscription snapshot remains an informational recorded period, not account-health state. Earlier recorded dates sort first for soonest order.
- Extra reset count can wrap at narrow widths; verify desktop row height and no horizontal overflow.

## Example

Choose 5h highest: an account with 90% precedes one with 20%, then an unknown account. Clicking the 5h header changes to lowest, placing 20% before 90% while unknown stays last. A row with three available reset credits shows Reset (3) beside status without adding a metadata panel.
