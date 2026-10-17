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

The fence that refuses a late or duplicate terminal write MUST be keyed on a
durable marker recording where the operation's dispatch stands in its terminal
write, and MUST NOT be inferred from an incomplete event spool under a terminal
state. That shape is what every ordinary operation carries while its terminal
append runs, because the proxy publishes the terminal operation state before
appending the terminal transcript block; inferring the fence from it would
refuse every ordinary completion. A fresh dispatch of the same operation MUST
start unfenced.

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

#### Scenario: Ordinary completion stays replayable under state-before-append ordering

- **GIVEN** the proxy published the terminal operation state before appending
- **AND** the operation's event spool is therefore still incomplete
- **WHEN** its terminal transcript append runs within the bound
- **THEN** the append is not refused as an already-settled write
- **AND** the finalized operation is eligible for replay with its events persisted

#### Scenario: Late write after fallback settlement is refused

- **GIVEN** fallback settlement published a terminal outcome for a dispatch
- **WHEN** the abandoned terminal append later reaches the transcript writer
- **THEN** the append is refused and the settled outcome is preserved verbatim
- **AND** finalization cannot make the settled operation replayable

#### Scenario: Duplicate terminal write cannot rewrite a recorded outcome

- **GIVEN** a terminal transcript append already committed for a dispatch
- **WHEN** a second terminal append arrives with a conflicting outcome
- **THEN** the recorded state and response identity are preserved
- **AND** the pending attempt-fenced finalization still succeeds

#### Scenario: Late cleanup cannot strand a newer terminal attempt

- **GIVEN** a terminal append was abandoned at its bound and settled
- **AND** a newer attempt for the same operation registered its own spooler state
- **WHEN** the abandoned attempt's cleanup finally runs
- **THEN** the newer attempt keeps its spooler state
