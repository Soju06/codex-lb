## Context

Telemetry currently resolves an unset environment override plus persisted `undecided` consent as active. The scheduler therefore transmits during its startup tick before an operator has made a dashboard decision. The existing tri-state database field, dashboard consent UI, sender guard, and explicit environment override already provide the necessary control points.

## Goals / Non-Goals

**Goals:**

- Make unresolved consent inactive without a migration.
- Guarantee that a default startup performs no telemetry network request.
- Preserve explicit opt-in through the dashboard or `CODEX_LB_TELEMETRY_ENABLED=true`.
- Keep the existing payload allowlist and sender safety checks unchanged.

**Non-Goals:**

- Remove telemetry code or its dashboard controls.
- Change the telemetry payload schema or collector endpoint.
- Rewrite previously persisted `enabled` or `disabled` decisions.

## Decisions

- Resolve persisted `undecided` consent as `active=False` while retaining `state="undecided"` and `source="default"`. This changes one policy decision without conflating an undecided operator with an explicit disabled decision.
- Continue honoring persisted `enabled` decisions. These are explicit operator choices and must survive upgrades.
- Continue honoring the environment override with highest precedence. `true` is a deployment-level opt-in and `false` remains a silent kill switch.
- Keep starting the scheduler, but rely on its consent gate to return before identity creation, snapshot aggregation, or sender invocation. This avoids special-case lifecycle wiring and preserves immediate activation after a dashboard opt-in.
- Replace the misleading startup log with a one-time notice that telemetry is disabled by default and names the opt-in paths.

## Risks / Trade-offs

- [Risk] Project maintainers receive fewer aggregate metrics from this fork. → Mitigation: explicit opt-in remains available.
- [Risk] Existing persisted `enabled` installations continue sending after upgrade. → Mitigation: document that the change affects unresolved defaults, while explicit prior decisions are preserved.
- [Risk] A future upstream merge may restore opt-out behavior. → Mitigation: retain focused regression tests and an OpenSpec delta in the fork.
