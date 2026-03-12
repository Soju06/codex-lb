## ADDED Requirements
### Requirement: Request-path selection uses cached usage snapshots
`LoadBalancer.select_account()` on the proxy request path MUST use persisted usage snapshots that are already available in `usage_history` and MUST NOT run `UsageUpdater.refresh_accounts()` inline. Freshness MUST be provided by the background usage refresh scheduler instead of synchronous per-request refresh.

#### Scenario: Request-path selection skips inline usage refresh
- **WHEN** a stream, compact, or transcription proxy request needs account selection
- **THEN** `LoadBalancer.select_account()` MUST NOT call `UsageUpdater.refresh_accounts()` inline
- **AND** selection MUST continue using the latest cached primary and secondary usage rows already stored in the database

#### Scenario: Selection proceeds without cached usage rows
- **WHEN** account selection runs before any usage snapshot exists for an otherwise active account
- **THEN** selection MUST proceed using current account status and runtime state
- **AND** the request path MUST NOT block on a synchronous usage refresh
