## ADDED Requirements

### Requirement: Native SSE writes coalesce ready records without delaying delivery

The native helper MAY coalesce SSE IPC records already available from the current
body read. It MUST bound each coalesced write by encoded bytes and record count,
allowing an individual existing protocol record larger than the batch byte budget
to be written alone. It MUST flush all ready records before another upstream read
and before terminal or framing-error delivery. It MUST NOT wait for another event
or a batching timer. Existing JSON-line records, ordering, text-fragment bounds,
consumer scheduling and replay rules MUST remain unchanged.

Cancellation during an output write MUST NOT truncate or interleave IPC records.
Any accepted but unfinished write MUST remain owned by the shared output until it
completes or the output itself fails. A subsequent writer MUST finish that output
before emitting its own record.

#### Scenario: Quiet upstream after one event

- **WHEN** one event arrives and the upstream waits for the next request action
- **THEN** the event reaches the consumer without requiring another body read or EOF

#### Scenario: Valid prefix before a framing failure

- **WHEN** ready valid events precede an oversized event in the same body read
- **THEN** the valid prefix arrives in order before the typed size failure

#### Scenario: Cancel while output is backpressured

- **WHEN** a request is cancelled after its output write starts
- **THEN** a sibling event or cancellation acknowledgement follows complete JSON lines
- **AND** unrelated requests retain valid streams
