## ADDED Requirements

### Requirement: Interpreted Responses SSE is negotiated across IPC

The adapter MUST require `http_responses_events_v1` before requesting interpreted
Responses events. Ordinary framing and compact collection MUST retain their
separate contracts. Interpreted text fragments MUST remain at most 16 KiB of
UTF-8. Type metadata MUST remain at most 16 KiB of UTF-8 and count toward the
queue byte budget; longer types MUST use Python normalization without metadata.
Only the final fragment MAY carry a type or request Python normalization.
Event type and Python-normalization metadata MUST be validated before use;
malformed or truncated events MUST fail without replay. Body-read deadlines,
original-byte event limits, cancellation and ready-consumer scheduling MUST remain
active. Missing-helper fallback MUST occur only before dispatch.

#### Scenario: Fragmented interpreted event

- **WHEN** one interpreted event spans multiple IPC fragments
- **THEN** the adapter emits one complete event with its validated metadata
- **AND** an active consumer can drain a burst beyond queue capacity

#### Scenario: Incompatible installed helper

- **WHEN** the installed helper lacks the required interpretation capability
- **THEN** the adapter rejects negotiation before dispatch without Python replay
