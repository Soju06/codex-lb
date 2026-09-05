## ADDED Requirements

### Requirement: Terminal transcript persistence has a live-delivery bound

The HTTP bridge MUST apply a finite application-level bound to draining and
appending optional transcript data for a terminal upstream event. If the bound
expires, the proxy MUST keep the event spool incomplete, MUST queue the selected
terminal event and end-of-stream marker without waiting for transcript
persistence, and MUST attempt the existing owner-fenced terminal settlement.
The bounded failure MUST NOT make a partial or uncertain transcript replayable.
A terminal append MUST remain incomplete until it finishes within the bound, at
which point the proxy MUST schedule an attempt-fenced finalization; only a
successful finalization makes it replayable. Cleanup that removes the in-memory
operation context MUST still require fallback settlement. When the event spooler
closes, a terminal append or finalization task still pending after the bound
MUST be cancelled and awaited to completion rather than abandoned.

#### Scenario: Busy transcript writer does not hold live completion

- **GIVEN** an acknowledged HTTP-bridge operation has selected a terminal event
- **AND** its transcript drain or terminal append does not finish within the bound
- **WHEN** the persistence bound expires
- **THEN** the terminal event and end-of-stream marker are queued
- **AND** fallback settlement keeps the event spool incomplete
- **AND** reconnect recovery does not replay the partial transcript

#### Scenario: Timely terminal append remains replayable

- **WHEN** terminal transcript drain and append finish within the bound
- **THEN** the terminal event and intended operation state are persisted
- **AND** the proxy schedules attempt-fenced finalization
- **AND** successful finalization makes the completed event spool eligible for replay

#### Scenario: Shutdown owns a terminal write pending past the bound

- **GIVEN** a terminal append or finalization task absorbs cancellation while its
  shielded session teardown waits on the transcript writer
- **WHEN** the event spooler closes and the persistence bound expires
- **THEN** the spooler logs a warning naming the still-pending tasks
- **AND** the spooler awaits those tasks to completion before close returns
