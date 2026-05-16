## MODIFIED Requirements

### Requirement: Request-path selection uses cached usage snapshots
`LoadBalancer.select_account()` on the proxy request path MUST use persisted usage snapshots that are already available in `usage_history` and MUST NOT run `UsageUpdater.refresh_accounts()` inline. Freshness MUST be provided by the background usage refresh scheduler instead of synchronous per-request refresh.

#### Scenario: Restart-safe quota recovery uses fresh post-block usage
- **GIVEN** an account is persisted as `quota_exceeded`
- **AND** the original blocking process is no longer running
- **AND** the account record still contains a persisted block marker from when quota exhaustion was first detected
- **WHEN** a later selection pass sees a fresh governing secondary-window usage row whose `recorded_at` is newer than that persisted block marker
- **AND** the quota debounce interval has expired
- **THEN** the balancer clears the stale runtime reset guard
- **AND** the account may return to `active` without a manual reactivate action

#### Scenario: Restart-safe quota recovery does not trust stale pre-block usage
- **GIVEN** an account is persisted as `quota_exceeded`
- **AND** the account record contains a persisted block marker
- **WHEN** selection only has governing usage rows whose `recorded_at` is older than or equal to that persisted block marker
- **THEN** the balancer keeps the account in `quota_exceeded`
- **AND** the stale persisted `reset_at` guard remains in effect
