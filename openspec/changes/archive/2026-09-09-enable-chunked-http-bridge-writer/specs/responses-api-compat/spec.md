## ADDED Requirements

### Requirement: Chunk transcript writes require an explicit rollout selection

The durable HTTP bridge transcript writer MUST support `rows_v1` and
`chunks_v2` selection through one canonical setting and MUST default to
`rows_v1`. Enabling `chunks_v2` MUST NOT change public SSE output, replay
eligibility, logical byte caps, or owner fencing. A v2 writer MUST atomically
select `chunks_v2` on the first successful append only when the operation has
no legacy row or chunk material and no logical event bytes. A format conflict
MUST fail closed without persisting mixed material. Before compression, the
writer MUST validate owner fencing, logical byte capacity, and the reader's
cumulative transcript event-count limit. It MUST NOT mark a transcript complete
when the reader would reject its event count.

#### Scenario: Default release keeps writing rows

- **WHEN** no writer-format setting is provided
- **THEN** new durable transcript events are written as `rows_v1`

#### Scenario: First v2 append selects chunk format atomically

- **GIVEN** an owner-fenced operation with no persisted transcript material
- **AND** the writer format is `chunks_v2`
- **WHEN** its first event batch is persisted
- **THEN** the operation format and first chunk commit together

#### Scenario: Existing row transcript cannot switch formats

- **GIVEN** an operation already has a legacy event row or logical event bytes
- **WHEN** a v2 writer attempts to append
- **THEN** the append fails without writing a chunk or changing the format

#### Scenario: Rejected batch is not compressed

- **GIVEN** a batch exceeds the logical byte cap or fails owner fencing
- **WHEN** the v2 writer handles the batch
- **THEN** it rejects the batch before zlib compression

### Requirement: Chunk writer preserves batch and terminal settlement semantics

When `chunks_v2` is enabled, each successful nonterminal batch flush MUST
persist one ordered chunk containing the exact queued SSE blocks. The terminal
path MUST first drain pending chunks and then atomically persist the terminal
one-event chunk, authoritative operation state, optional response identifier,
and complete-spool marker. A size-cap failure or persistence error MUST leave
the transcript incomplete and use the existing terminal settlement fallback.

#### Scenario: Batch becomes one replay-equivalent chunk

- **WHEN** the in-memory batcher flushes multiple nonterminal events in v2 mode
- **THEN** one chunk is persisted
- **AND** replay returns every original event in the same order

#### Scenario: Terminal chunk and state commit together

- **WHEN** a terminal event fits inside the remaining logical byte budget
- **THEN** the terminal chunk, terminal operation state, response identifier,
  and complete marker commit atomically

#### Scenario: Oversized terminal event settles without complete transcript

- **WHEN** a terminal event exceeds the remaining logical byte budget
- **THEN** no terminal chunk is written
- **AND** the operation reaches its authoritative terminal state with an
  incomplete spool

#### Scenario: Event-count overflow settles without complete transcript

- **GIVEN** appending the terminal event would exceed the reader's cumulative
  transcript event-count limit
- **WHEN** the terminal path runs
- **THEN** no terminal chunk is written
- **AND** the operation reaches its authoritative terminal state with an
  incomplete spool
