# usage-refresh-policy (delta)

## ADDED Requirements

### Requirement: Compact budget-exhausted terminals settle the API-key reservation before raising

On the HTTP bridge / forwarded compact path the caller passes an `api_key_reservation_override` with `owns_reservation` false, making `compact_responses` the SOLE settler of the API-key usage reservation; therefore EVERY budget-exhausted terminal raise in the compact request path that is reached with a held, unsettled reservation MUST settle the compact API-key usage reservation (release it via `_settle_compact_api_key_usage` with `response` `None`) BEFORE raising the budget-exhausted `ProxyResponseError` (`upstream_request_timeout`), so held API-key quota is not leaked. This MUST apply to the outer-loop preflight budget terminals (before the freshness check, before the freshness reserve, and after the freshness check) and to the post-401 forced-refresh preflight budget terminal, each of which propagates straight to the outer `except ProxyResponseError` handler (which does not settle) and the `finally` (which only writes a request log). The terminal MUST preserve its prior escalation: it still raises the same `502` `upstream_request_timeout` error after settling, and it MUST still release the selected account's `response_create` lease where it already did so. A budget-exhausted terminal that is caught by an enclosing handler that already settles the reservation before raising — the inner upstream-call budget terminals, whose `upstream_request_timeout` error is settled by the retry loop's `upstream_request_timeout` / account-neutral branch — MUST NOT settle a second time, so the reservation is never double-settled.

#### Scenario: Compact preflight budget exhaustion settles the reservation before raising

- **GIVEN** a compact-responses request invoked through the HTTP bridge / forwarded path with an `api_key_reservation_override` and `owns_reservation` false
- **WHEN** a budget-exhausted terminal fires on the compact preflight (the request budget is already exhausted at a freshness-check preflight or post-401 forced-refresh preflight budget check)
- **THEN** the compact API-key usage reservation is settled (released via `_settle_compact_api_key_usage`) before the budget-exhausted error is raised, so it does not leak held API-key quota
- **AND** the client receives the `502` `upstream_request_timeout` error unchanged

#### Scenario: Inner upstream-call budget terminal is not double-settled

- **GIVEN** a compact-responses request whose inner `_call_compact` budget check finds the request budget exhausted and raises the budget-exhausted `upstream_request_timeout` error
- **WHEN** the enclosing retry-loop `except ProxyResponseError` handler settles the reservation on the `upstream_request_timeout` branch before raising
- **THEN** no additional settle is performed at the inner terminal, so the reservation is settled exactly once
