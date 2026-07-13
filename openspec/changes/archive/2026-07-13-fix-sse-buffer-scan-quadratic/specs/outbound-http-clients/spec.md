# outbound-http-clients Delta

## ADDED Requirements

### Requirement: Upstream SSE framing scans each byte a bounded number of times

The upstream SSE event reader MUST NOT rescan previously scanned buffer bytes on each network read; framing cost MUST be linear in event size so a single large event (up to the configured event-size cap) cannot stall the shared event loop. Framing semantics MUST be unchanged: all separator forms (`\r\n\r\n`, `\n\n`, `\r\r`) are honored, including separators straddling read boundaries, and event-size limits and idle timeouts apply as before.

#### Scenario: Large event frames in linear time

- **GIVEN** a single SSE event several megabytes long arriving across many reads
- **WHEN** the reader frames the stream
- **THEN** each received byte is scanned at most a bounded number of times (no full-buffer rescans per read)
- **AND** the event is delivered intact

#### Scenario: Separator straddling a read boundary still terminates the event

- **GIVEN** an event whose `\r\n\r\n` separator is split across two reads
- **WHEN** the reader frames the stream
- **THEN** the event terminates exactly at the separator and the following event is framed normally
