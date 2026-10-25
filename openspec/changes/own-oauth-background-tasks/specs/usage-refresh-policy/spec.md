## ADDED Requirements

### Requirement: Refresh completion tasks remain owned until done
The refresh singleflight owner MUST retain each asynchronously spawned completion task until that task is done. Completion, failure and cancellation MUST release the retained reference without changing the existing successor guard, refresh-failure cooldown, or waiter-cancellation semantics. Completion diagnostics MUST NOT include credential values.

#### Scenario: Completion waits behind another owner operation
- **WHEN** a refresh completes while its completion task awaits the singleflight lock
- **THEN** the singleflight retains that completion task until its work ends
- **AND** only the matching inflight refresh may update its failure cache

#### Scenario: Completion finishes or is cancelled
- **WHEN** a retained completion task succeeds, fails or is cancelled
- **THEN** the singleflight removes its retained reference after the task is done
