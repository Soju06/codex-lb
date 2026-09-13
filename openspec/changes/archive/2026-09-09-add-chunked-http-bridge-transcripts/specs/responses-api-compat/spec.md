## ADDED Requirements

### Requirement: Durable recovery transcripts use an explicit storage format

Each durable HTTP bridge operation MUST identify its transcript storage format.
Existing operations and new operations created before the chunk-writer cutover
MUST use `rows_v1`. A dual-reader release MUST replay both `rows_v1` event rows
and `chunks_v2` event chunks without changing the public SSE blocks or their
order. This expand release MUST continue writing `rows_v1` so rolling
deployments do not expose chunk-only data to an older replica.

#### Scenario: Historical row transcript remains replayable

- **GIVEN** an existing completed operation whose format is `rows_v1`
- **WHEN** recovery loads its transcript after the schema expansion
- **THEN** it receives the same ordered SSE blocks as before the migration

#### Scenario: Chunk transcript replays exact events

- **GIVEN** a completed `chunks_v2` operation with valid contiguous chunks
- **WHEN** recovery loads its transcript
- **THEN** every original SSE block is returned byte-for-byte in sequence order

#### Scenario: Expand release keeps the legacy writer

- **WHEN** the dual-reader release records or appends an ordinary operation
- **THEN** it persists the operation and events in `rows_v1` format

### Requirement: Chunk transcript decoding fails closed

Chunk decoding MUST enforce the operation's logical transcript byte bound and
the 65,536-event operation-wide chunk transcript limit. The chunk writer MUST
reject transcript growth beyond that same limit. Chunk decoding MUST reject
unknown codecs, decompression beyond the declared bound, hash or
byte-count mismatch, non-hexadecimal or incorrectly sized hashes, malformed
framing, invalid UTF-8, incorrect event count,
non-contiguous sequence ranges, and trailing bytes. Any rejected chunk MUST
make the transcript ineligible for recovery and MUST NOT produce a partial
replay or a new upstream dispatch.

#### Scenario: Corrupt chunk produces no partial transcript

- **GIVEN** a chunk payload whose hash, framing, or declared counts are invalid
- **WHEN** recovery loads the operation
- **THEN** the transcript is ineligible and no prefix is replayed

#### Scenario: Sequence gap produces no partial transcript

- **GIVEN** individually valid chunks whose sequence ranges contain a gap or
  overlap
- **WHEN** recovery loads the operation
- **THEN** the transcript is ineligible

#### Scenario: Logical event byte mismatch produces no transcript

- **GIVEN** decoded events do not total the operation's persisted `event_bytes`
- **WHEN** recovery loads either storage format
- **THEN** the transcript is ineligible

### Requirement: Transcript lifecycle handles both storage formats

Spool reset, failed-operation retry, rollback-before-dispatch, and retention
cleanup MUST inspect or delete both legacy event rows and event chunks under
the existing owner and operation fences. A format transition MUST NOT leave
stale transcript material that a later retry can mix into a fresh response.
Any path that clears all transcript material for a retry MUST reset the format
to `rows_v1` in the same transaction so the expand release remains a usable
rollback writer.

#### Scenario: Retry clears both transcript stores

- **GIVEN** an operation has legacy or chunk transcript material
- **WHEN** an owner-fenced retry resets the spool
- **THEN** both event stores are empty before new output is accepted

#### Scenario: Rollback refuses an operation with chunk evidence

- **GIVEN** an operation has a persisted chunk
- **WHEN** rollback-before-dispatch checks whether upstream work exists
- **THEN** it preserves the operation instead of deleting its durable fence
