## ADDED Requirements

### Requirement: Resilience toggles follow the dashboard value

Soft drain (the draining/probing health tiers), the deterministic failover decision and the circuit-breaker selection gate MUST be controlled by the `dashboard_settings` columns `soft_drain_enabled`, `deterministic_failover_enabled` and `circuit_breaker_enabled`. A NULL column MUST inherit the process environment value (the deprecated `CODEX_LB_*` alias) and then the code default, and a non-NULL column MUST win over both; the effective value MUST come from the single `configuration-tiers` resolver, and the settings API MUST report each toggle's effective value and provenance. Account selection MUST resolve the three toggles once from the dashboard-settings snapshot its caller obtained before entering runtime locks — the same snapshot that produced the concurrency caps — MUST apply that resolution to every reload of its selection inputs (sticky and non-sticky retries, exclusion- and security-filtered pools) and to opportunistic admission, and MUST NOT read the database, await the settings cache or read `get_settings().<toggle>` for them while holding a runtime lock or inside the retry loop. Force Probe settlement MUST take one snapshot before acquiring the account lock. A caller that supplies no snapshot (a code path outside a proxy request) MUST resolve the environment layer, which is the pre-dashboard behaviour. Changing a toggle in the dashboard MUST take effect on the next selection on every replica without a restart.

#### Scenario: Dashboard turns soft drain off

- **GIVEN** `CODEX_LB_SOFT_DRAIN_ENABLED` is unset (default on) and an operator sets soft drain off in the dashboard
- **WHEN** the next selection evaluates an account whose primary usage is above the fixed drain threshold
- **THEN** the account stays in the healthy tier instead of entering the draining tier
- **AND** no database read or settings-cache await happened under the runtime lock

#### Scenario: Dashboard turns deterministic failover off

- **GIVEN** an operator has set deterministic failover off in the dashboard
- **WHEN** a stream, compact or WebSocket attempt fails before the first event with a failover-eligible classification
- **THEN** the proxy surfaces the failure instead of retrying on the next account

#### Scenario: Dashboard enables the circuit-breaker gate the environment left off

- **GIVEN** `CODEX_LB_CIRCUIT_BREAKER_ENABLED=false` and an operator turns the circuit breaker on in the dashboard
- **AND** every account's breaker is open
- **WHEN** a selection runs
- **THEN** the balancer reports the upstream as degraded because the breakers are open, without a restart

#### Scenario: Inherited toggle follows a later environment change

- **GIVEN** a toggle's dashboard column is NULL
- **WHEN** the process environment value changes and the process restarts
- **THEN** the new environment value applies and the settings API reports `source: "env"` (or `"default"` when it equals the code default)
