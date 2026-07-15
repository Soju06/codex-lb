# Context — dashboard-progressive-disclosure

## Purpose

Part of the simplicity effort (PRINCIPLES.md P3, progressive disclosure): keep the first-run dashboard surface small — import accounts, hand out an API key, point a client at the proxy — while every power feature stays one explicit interaction away.

## Decisions

- **Core vs Advanced split.** Nav: Dashboard, Reports, Accounts, APIs, Settings are core; Automations (scheduled warm-up jobs) is the only advanced destination today. Settings: Appearance, Import, Guest Access, Password, Session, TOTP, and API Keys stay flat; Routing tuning, Upstream Proxy pools, Model Sources, Firewall, Quota Planner, and Sticky Sessions collapse into the Advanced group.
- **One-item Advanced menu is intentional, not over-engineering.** The menu is the mandated landing zone for future power features per PRINCIPLES.md P3: a new page-level destination defaults to the Advanced menu unless a spec explicitly designates it core. Establishing the boundary now is the point.
- **Advanced sections fetch on expand, not on page load — intentional.** The Advanced settings group unmounts its children while collapsed (Radix Collapsible default, no `forceMount`). Sections that issue queries on mount (firewall entries, quota planner, sticky sessions, model sources) therefore no longer fire network requests when an operator merely opens `/settings`; the requests fire on the first expand. This trims first-paint work for the common path and must not be flagged as a data-loading regression.
- **Arrays stay in `app-header.tsx`.** `CORE_NAV_ITEMS` and `ADVANCED_NAV_ITEMS` are flat `as const` arrays in the header component (no separate nav-items module); the CI simplicity budget manifest points at this file.
- **No route changes.** `/automations` deep links and the legacy `/firewall` → `/settings` redirect are compatibility surfaces and keep working; regression tests cover both.

## Example

A read-only guest opens `/settings`: they see Appearance, Import, and API Keys cards plus a collapsed "Advanced settings" row. No firewall/quota/sticky-session requests have been issued. One click on the row mounts all six advanced sections with their controls disabled by the existing `canWrite` gating.

## PR notes

- The committed before/after screenshots were captured against a frontend without a live backend, so a "Failed to fetch" banner appears in them — a capture-environment artifact, not a regression.

## Failure modes considered

- Tests that asserted advanced sections on load (settings-page unit test, firewall integration flow, header Automations link) were updated to expand/open first — asserting through the same one-interaction path an operator uses.
- The accounts reset-credits badge stays on the core Accounts item in both desktop and mobile navs.
