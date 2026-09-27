# Context: frontend-architecture

Normative requirements live in [`spec.md`](./spec.md). This document currently
covers the progressive-disclosure navigation and settings model.

## Progressive disclosure (nav + settings)

### Purpose

Part of the simplicity effort (PRINCIPLES.md P3, progressive disclosure): keep
the first-run dashboard surface small — import accounts, hand out an API key,
point a client at the proxy — while every power feature stays one explicit
interaction away.

### Decisions

- **Core vs Advanced split.** Nav: Dashboard, Reports, Accounts, APIs,
  Settings are core; Automations (scheduled warm-up jobs) is the only advanced
  destination today. Settings: Appearance, Import, Guest Access, Password,
  Session, TOTP, and API Keys stay flat; Routing tuning, Upstream Proxy pools,
  Model Sources, Firewall, Quota Planner, and Sticky Sessions collapse into
  the Advanced group.
- **One-item Advanced menu is intentional, not over-engineering.** The menu is
  the mandated landing zone for future power features per PRINCIPLES.md P3: a
  new page-level destination defaults to the Advanced menu unless a spec
  explicitly designates it core.
- **Advanced sections fetch on expand, not on page load — intentional.** The
  Advanced settings group unmounts its children while collapsed (Radix
  Collapsible default, no `forceMount`). Sections that issue queries on mount
  (firewall entries, quota planner, sticky sessions, model sources) therefore
  do not fire network requests when an operator merely opens `/settings`; the
  requests fire on the first expand. This trims first-paint work for the
  common path and must not be flagged as a data-loading regression.
- **Arrays stay in `app-header.tsx`.** `CORE_NAV_ITEMS` and
  `ADVANCED_NAV_ITEMS` are flat `as const` arrays in the header component (no
  separate nav-items module); the CI simplicity budget manifest
  (`.github/simplicity-budgets.toml`, `[core_nav]`) points at this file.
- **No new routes.** `/automations` deep links stay as-is. The legacy
  `/firewall` compatibility route redirects to `/settings?advanced=1#firewall`
  so Advanced expands and the firewall section is in view; plain `/settings`
  stays collapsed by default. Regression tests cover both.

### Example

A read-only guest opens `/settings`: they see Appearance, Import, and API Keys
cards plus a collapsed "Advanced settings" row. No firewall/quota/sticky-session
requests have been issued. One click on the row mounts all six advanced
sections with their controls disabled by the existing `canWrite` gating.

### Testing notes

- Tests that asserted advanced sections on load (settings-page unit test,
  firewall integration flow, header Automations link) expand/open first —
  asserting through the same one-interaction path an operator uses.
- The accounts reset-credits badge stays on the core Accounts item in both
  desktop and mobile navs.

## Dashboard partial-failure isolation

### Purpose and scope

The dashboard overview and request-log listing are independent operator surfaces. A request-log storage or listing outage should not remove healthy fleet quota and account controls. Normative behavior lives in [`spec.md`](./spec.md); while the change is active, its added requirement lives in [`../../changes/preserve-dashboard-overview-on-log-failure/specs/frontend-architecture/spec.md`](../../changes/preserve-dashboard-overview-on-log-failure/specs/frontend-architecture/spec.md).

### Decision rationale

The page composes overview-backed view data as soon as overview data exists and treats request logs as a section-local state machine: initial loading, terminal error announced through a local alert semantic, or ready. Recovery calls the existing request-log query's local refetch operation. A broader dashboard invalidation was rejected because it would refetch healthy data and could make usable incident context disappear.

### Constraints and non-goals

This boundary does not change API shapes, query keys, retry policy, polling, or backend reliability. It does not preserve stale rows after later refetch failures, introduce route splitting or global state, or define global live-region behavior. The header refresh action intentionally keeps its existing broad refresh semantics; only the Request Logs Retry action is local.

### Failure mode and example

If overview, projections, and request-log options return successfully while the initial listing reaches terminal HTTP 500, operators continue to see statistics, quota charts, and account controls. The Request Logs heading remains visible with a locally announced endpoint error and native Retry control. After the endpoint recovers, keyboard-activating Retry replaces that error with the returned rows without issuing another overview request.

### Testing notes

The product-boundary regression renders the real `/dashboard` App route with the production query retry policy and MSW handlers. It counts each request family, seeds unique values for a statistic, quota surface, projection metric, and account control, focuses and keyboard-activates native Retry, holds the recovered listing response pending long enough to assert all healthy surfaces remain mounted, and then verifies the recovered row.

## Reset-credit reconciliation

Reset mutations update the selected account through the authenticated account-summary endpoint and merge it by id into Accounts and dashboard caches. Account-specific trends and both reset-credit details queries are invalidated exactly; mutations do not reload the complete list. Dashboard quota windows/totals use the updated account values; overlapping aggregate projection refreshes share the active request.

A missing snapshot has null freshness and is shown as pending after redemption. Up to four targeted reads reconcile it; failure never retries the consume. Confirmed reset, a redeemed credit with no reset, and an unknown result have distinct messages. For example, redeeming X preserves Y's Reset count, current selection, and scroll; X can move if its new value changes the active sort. Normal periodic polling remains the fallback after bounded reconciliation.

See the [frontend requirements](spec.md) and [reset-credit operational context](../rate-limit-reset-credits/context.md) for timing, replica behavior, and recovery limits.

Reset reconciliation state is scoped to the QueryClient, so ordinary account/dashboard query replacement cannot remove the pending flag. Query generations reject responses started before a newer targeted merge, and snapshot timestamps reject older reset-credit fields. Fresh polls remain authoritative for health, policy, usage and identity; a paused/ineligible account with intentionally null snapshot freshness cannot be replaced by the old active summary. A later fresh poll clears pending state for its account. Deleting an account clears its reconciliation state.

Targeted dashboard quota totals exclude entries with no usage sample, matching backend summary eligibility. For example, a restored 100-credit account plus an unsampled 100-credit plan remains 100/100 (100%), rather than 100/200. Polls that only resolve reset freshness retain their server-provided aggregate data.

Unresolved manual request IDs also live in the QueryClient scope, surviving dialog dismissal or page/dialog remounting within the same client session. A terminal result releases the ID; errors and unknown outcomes retain it. This keeps ordinary dialog retries compatible with backend credit-level conflict protection and prevents a new request from consuming a different credit during recovery.

## Accounts grid and subscription term

Accounts defaults to the original Detail layout: a compact account selector on the left and selected-account statistics, quota trend charts, subscription term and management on the right. Mobile stacks the selector and detail. Full-width List and Grid remain additional locally remembered choices; valid existing List/Grid preferences are retained. All three modes share search, status, sort and selection, with List/Grid paginated at 24 accounts and the original compact selector internally scrollable. For example, filter for a workspace, open an account in Grid, then return to Detail: the filter and selected account remain, and its charts render inline again. Overview dialogs and inline Detail reuse the same management component, with trends/credit queries only for the visible selected account. See [recorded subscription term](../account-subscription-term/context.md) for timestamp provenance and renewal limitations.

The original selector now places a compact recorded-plan duration immediately below each status badge, such as `18d 08h`. Full-width List is the minimal scanning mode: account/workspace, plan/status, compact duration and quota/reset timing. Its two quota windows sit side by side at desktop widths, while mobile wraps the groups. Request/token totals, credentials, purchased credits, routing/warm-up details and long timestamps stay in Grid or selected details. List includes a compact available-reset count when badges are enabled. For example, List can compare seven accounts within a compact desktop panel; opening a row reveals its complete metadata and actions. Missing plan metadata reads “No data”; an elapsed recorded period remains separate from account status. Tooltip and accessible descriptions retain the recorded deadline and snapshot caveat.


### List sorting and reset counts

List headers sort Plan alphabetically, Subscription by recorded deadline, and 5h/7d quota by remaining percentage. Clicking a header starts ascending and clicking again reverses it; arrows and accessible direction labels identify the current order. The existing dropdown provides both directions at mobile widths. This reuses the shared Accounts sort state and its default Most reset credits order, so changing views retains the selected mode, while changing sort returns the filtered list to its first page. It does not introduce a new persisted setting or API request.

Each quota sort reads its own window independently of the quota appearance preference. Zero percent is data; absent windows and invalid/missing deadlines stay last in both directions. Monthly-only accounts do not substitute monthly quota for a missing 5h/7d value. Recorded subscription dates remain snapshots, so ordering an elapsed period first does not alter account health. For example, 5h highest places 90%, then 20%, then an unavailable value; 5h lowest places 20%, then 90%, then the unavailable value.

List uses a small Reset (N) indicator beside plan/status for positive available counts, following the existing reset-badge setting. It uses summary data and opens normal account details with the row; it is not an independent redemption action. Zero/missing counts omit the badge to preserve compact rows.
