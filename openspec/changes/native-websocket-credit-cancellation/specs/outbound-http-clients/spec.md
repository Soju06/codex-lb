## ADDED Requirements

### Requirement: Credit return survives cancellation
The adapter MUST serialize receive calls and preserve receive credit across failed dispatch. If cancellation occurs while a credit return is being dispatched, the adapter MUST finish dispatch before propagating cancellation and MUST NOT dispatch that credit twice.
If the native helper cannot acquire a receive-credit permit for an accepted frame, it MUST report a socket failure and MUST NOT panic.

#### Scenario: Cancellation during credit dispatch
- **WHEN** a receive is cancelled during credit return
- **THEN** the dispatch completes before cancellation is propagated and the next receive does not duplicate the credit

#### Scenario: Credit dispatch fails
- **WHEN** credit dispatch fails
- **THEN** the adapter retains the credit for the next receive

#### Scenario: Native permit unexpectedly unavailable
- **WHEN** native code cannot acquire the permit for an accepted frame
- **THEN** it reports a socket failure without panicking the helper
