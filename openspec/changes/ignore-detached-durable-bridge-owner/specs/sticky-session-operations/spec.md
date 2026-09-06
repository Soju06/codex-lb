## ADDED Requirements

### Requirement: Detached durable bridge rows are not continuity owner evidence

When account invalidation (deactivation, re-authentication demand, proxy-binding change, or deletion) detaches a durable HTTP-bridge row, leaving it `CLOSED` with no owner account, no owner instance, and no turn-state or previous-response anchor, durable request-target lookup MUST NOT report that row as a lookup hit, whether resolved by canonical key or by alias. A request whose only durable evidence would have been such a row MUST proceed as a request without durable bridge state, and its claim MUST re-own the same canonical row. A `CLOSED` row that still names its owner account MUST remain durable owner evidence.

#### Scenario: Hard thread continuation survives owner account invalidation

- **GIVEN** a Codex `thread_header` bridge row was detached because its owner account was deactivated
- **AND** the account was later reactivated
- **WHEN** the client continues that thread without `previous_response_id`
- **THEN** the durable lookup reports no durable row for the thread
- **AND** the request is served by ordinary account selection instead of failing closed with `previous_response_owner_unavailable`
- **AND** the selected account's claim reuses the detached canonical row

#### Scenario: Ordinarily released closed row keeps its owner

- **GIVEN** a bridge row was released normally and is `CLOSED` while still naming its owner account and latest response anchor
- **WHEN** a request resolves that canonical key
- **THEN** the durable lookup still returns the row with its owner account
