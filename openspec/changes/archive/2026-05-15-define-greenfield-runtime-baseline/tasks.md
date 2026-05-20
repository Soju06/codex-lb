## 1. Service Tier Verification

- [x] 1.1 Verify Codex CLI continues to request the configured ultrafast tier through codex-lb.
- [x] 1.2 Add an operator verification command or runbook snippet that proves requested_service_tier versus actual_service_tier without printing secrets.

## 2. Greenfield Runtime Baseline

- [x] 2.1 Add operator-facing greenfield baseline guidance under OpenSpec context or existing repo installation surfaces without changing docs/.
- [x] 2.2 Update .env.example, Docker guidance, or deployment examples to show PostgreSQL-backed sustained serving with unchanged 2455 and 1455 ports.
- [x] 2.3 Ensure guidance distinguishes bridge-safe worker-pool serving from plain multi-worker serving with the bridge disabled.
- [x] 2.4 Keep SQLite quick-start guidance intact as the local/smoke path.

## 3. Observability

- [x] 3.1 Inventory existing Prometheus metrics for readiness, request status, latency, upstream failure class, DB lock pressure, bridge continuity, and tier mismatch coverage.
- [x] 3.2 Add missing metrics required by the greenfield baseline with bounded labels and no prompt or secret leakage.
- [x] 3.3 Add documented alert rules or an example alert bundle for the greenfield baseline.
- [x] 3.4 Add focused tests for any new metrics or alert-rule rendering.

## 4. Verification

- [x] 4.1 Run or capture the focused service-tier verification command.
- [x] 4.2 Run focused observability and runtime-baseline tests.
- [x] 4.3 Run openspec validate --specs.
- [x] 4.4 Capture a live snapshot command example for operators to confirm ultrafast requested tier vs actual served tier.
