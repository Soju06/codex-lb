## ADDED Requirements

### Requirement: Native event dispatch schedules ready consumers

The native helper event reader MUST give ready response consumers a scheduling
opportunity between accepted events, including when helper output is already
buffered. A burst exceeding the per-request queue capacity MUST complete without
data loss when its consumer keeps draining. A stalled consumer MUST retain a
bounded queue and fail independently without blocking sibling requests.

#### Scenario: Buffered burst with active consumer

- **GIVEN** a helper emits more events than the queue capacity in one buffered burst
- **WHEN** the caller continuously consumes the response body
- **THEN** all body bytes arrive in order and the response completes

#### Scenario: Stalled consumer shares the helper

- **GIVEN** one caller stops consuming while another request shares its helper
- **WHEN** the stalled request exceeds its bounded event queue
- **THEN** only the stalled request fails and the other request completes

#### Scenario: Responses consumes buffered native bursts

- **GIVEN** direct or account-routed Responses uses the native helper
- **WHEN** framed SSE events, raw JSON success chunks, or raw HTTP error chunks arrive in a buffered burst exceeding queue capacity
- **THEN** an active consumer receives ordered SSE events, the complete JSON response, or the original HTTP error respectively
- **AND** the response is not replaced by a consumer-backpressure error
