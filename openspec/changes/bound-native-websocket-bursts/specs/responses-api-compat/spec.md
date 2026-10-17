## ADDED Requirements

### Requirement: Native WebSocket bursts retain bounded ordered delivery
The native WebSocket adapter MUST retain ordered delivery of at least 256 small frames while its consumer is temporarily unscheduled. Its decoded receive queue MUST be bounded by 4096 events and 32 MiB of accounted payload bytes, including UTF-8 text, binary data and the second payload representation for interpreted Responses frames. It MUST continue processing send acknowledgements without waiting for the data consumer. A genuinely overflowing connection MUST fail locally without blocking unrelated connections or penalizing account health. This change MUST preserve the existing HTTP event-queue single-event allowance.

#### Scenario: Small-frame burst
- **WHEN** 256 small frames arrive before the consumer next runs
- **THEN** all frames remain available in order without a transport failure

#### Scenario: Send acknowledgement during a burst
- **WHEN** an acknowledgement arrives after queued data while the consumer has not drained
- **THEN** the send completes without waiting for the data consumer

#### Scenario: Genuine overflow
- **WHEN** the event or accounted-byte budget would be exceeded
- **THEN** the connection fails with an account-neutral local-buffer classification
- **AND** pending sends and the connection are finalized while unrelated connections remain usable
