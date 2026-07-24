## ADDED Requirements

### Requirement: Fresh reattach startup and downstream teardown are bounded

A fresh HTTP bridge continuation with a verified anchorless replay body MUST reach recovery or terminal settlement no later than 30 seconds after each current upstream send, unless an existing configured or request deadline is earlier.

The continuation MUST use a response-created startup deadline capped at 30 seconds for each of its original and
single recovery attempts. The deadline MUST retain the existing eventless
eligibility checks and MUST be measured from the current upstream send. Once
the single recovery is exhausted, the proxy MUST emit the existing structured
`upstream_request_timeout` failure, settle ownership once, and retire the whole
bridge without an account-health penalty.

The streaming response lifecycle MUST own the pending first-item probe and its
source iterator. If downstream teardown occurs before the response body begins
iteration, it MUST cancel and await the probe, close the source, and allow the
existing bridge detach path to release response-create admission and account
leases. Cleanup MUST be idempotent and MUST NOT depend on garbage collection.

#### Scenario: Verified fresh reattach is bounded before the general watchdog

- **GIVEN** a verified fresh-bridge continuation produces no matched response event
- **WHEN** the earlier configured deadline or 30 seconds elapses after its current send
- **THEN** the proxy performs its one safe recovery or terminally fails it
- **AND** it does not wait for the general 240-second eventless cap on either attempt

#### Scenario: Disconnect before first body poll closes the bridge source

- **GIVEN** the startup probe timed out while awaiting the first bridge event
- **AND** the ASGI response is torn down before body iteration begins
- **WHEN** response cleanup runs
- **THEN** the first-item task and source iterator are closed promptly
- **AND** the request does not remain pending until the eventless watchdog
