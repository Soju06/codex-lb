## ADDED Requirements

### Requirement: Native compact collection is negotiated and bounded across IPC

The adapter MUST require `http_compact_collect_v1` before requesting native
compact collection. Collection MUST apply only to successful responses selected
for SSE by the compact Content-Type contract. Other bodies MUST retain raw-body
handling. Collected result IPC text fragments MUST NOT exceed 16 KiB of UTF-8.
The adapter MUST reject malformed or truncated results without replaying the
request. Missing-helper fallback MUST remain limited to the pre-dispatch boundary.

#### Scenario: Large collected result

- **WHEN** a compact result exceeds one IPC text fragment
- **THEN** the adapter reconstructs one complete result with all unknown fields intact
- **AND** ready consumers receive scheduling opportunities between fragments

#### Scenario: Incompatible installed helper

- **WHEN** a launched helper lacks the compact collection capability
- **THEN** negotiation fails before dispatch and no Python replay occurs

#### Scenario: Cancellation while collecting

- **WHEN** a compact caller cancels before a terminal result
- **THEN** its native request and owned response/session close
- **AND** another request sharing the helper remains usable
