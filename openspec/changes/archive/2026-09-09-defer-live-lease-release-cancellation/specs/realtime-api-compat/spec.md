## ADDED Requirements

### Requirement: Live cancellation completes account-lease cleanup

The Live WebSocket proxy MUST release its selected account lease exactly once
when the downstream handler is cancelled. Cancellation MUST be deferred while
that release is in progress, without changing the established upstream and
downstream close behavior.

#### Scenario: Cancelled Live handler waits for lease release

- **WHEN** the downstream Live handler is cancelled while account-lease release
  is waiting on load-balancer cleanup
- **THEN** the release completes exactly once
- **AND** both peer cleanup paths retain their existing close semantics
- **AND** the original cancellation is raised afterward
