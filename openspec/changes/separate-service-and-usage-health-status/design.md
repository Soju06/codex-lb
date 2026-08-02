## Context

The fixed status bar currently derives a generic live indicator from
`DashboardOverview.lastSyncAt`. The backend already exposes `/health/ready`,
whose contract covers local infrastructure readiness and intentionally excludes
transient upstream account and provider health.

## Goals / Non-Goals

**Goals:**

- Make service readiness and usage synchronization independently visible.
- Reuse the existing readiness endpoint without changing its backend contract.
- Preserve the existing usage timestamp and 60-second freshness rule.
- Keep loading, failure, and stale states explicit in text as well as color.

**Non-Goals:**

- Changing readiness, liveness, upstream-health, or usage-refresh behavior.
- Adding scheduler lag metrics or other observability signals.
- Changing any other dashboard surface, permission, retention, or navigation.

## Decisions

- Add a small typed frontend health client for `GET /health/ready`. This keeps
  response validation at the API edge instead of embedding fetch and schema
  logic in the layout component.
- Poll readiness independently from the dashboard overview query. A successful
  response with `status: "ok"` is ready; an initial pending request is
  `Checking`, and any failed or non-`ok` result is `Not ready`.
- Keep usage freshness derived solely from `lastSyncAt`. A timestamp less than
  60 seconds old is `Synced`; older, absent, or invalid data is `Stale`.
- Render explicit localized state text next to each signal so status is not
  communicated by color alone.

## Risks / Trade-offs

- [Risk] Readiness polling adds one small periodic request per open dashboard.
  → Mitigation: use the existing lightweight endpoint and the status bar's
  existing background-polling posture.
- [Risk] A transient readiness request failure is visible as `Not ready`.
  → Mitigation: this is the truthful state when the dashboard cannot confirm
  readiness; usage freshness remains independently visible.
