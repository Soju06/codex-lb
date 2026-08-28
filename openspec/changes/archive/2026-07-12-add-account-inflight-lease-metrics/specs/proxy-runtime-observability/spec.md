## MODIFIED Requirements

### Requirement: Responses concurrency pressure is observable

The service MUST expose low-cardinality logs and metrics for account-local in-flight create count, active stream count, leased token/cost pressure, cap rejections, lease stale reclaims, soft-affinity reroutes, and local-vs-upstream 429 classification. Observability MUST avoid raw prompt text, raw affinity keys, API keys, emails, request ids, session ids, and request payload content.

The service MUST expose a Prometheus gauge named `codex_lb_account_inflight_leases` labeled by `account_id` and `kind`, where `kind` is either `stream` or `response_create`. The gauge value MUST equal the current in-process account lease count for that account and kind. The gauge MUST update when a lease is acquired, explicitly released, or reclaimed as stale. Gauge labels MUST NOT include raw prompt text, raw affinity keys, API keys, emails, request ids, session ids, or request payload content.

#### Scenario: Local and upstream 429s are separated

- **WHEN** local admission rejects a request and upstream later returns a rate limit for another request
- **THEN** logs and metrics distinguish local overload reasons from normalized upstream `upstream_rate_limit`
- **AND** preserved upstream wire payloads may retain upstream codes such as `rate_limit_exceeded`, `usage_limit_reached`, or `insufficient_quota`

#### Scenario: Active account leases update gauge

- **WHEN** the proxy acquires a `stream` lease for account `acc_1`
- **THEN** `codex_lb_account_inflight_leases{account_id="acc_1",kind="stream"}` increases to the current active stream lease count
- **AND** `codex_lb_account_inflight_leases{account_id="acc_1",kind="response_create"}` remains the current active response-create lease count

#### Scenario: Released account leases reset gauge

- **WHEN** the proxy explicitly releases or stale-reclaims the last active `stream` lease for account `acc_1`
- **THEN** `codex_lb_account_inflight_leases{account_id="acc_1",kind="stream"}` is set to `0`
