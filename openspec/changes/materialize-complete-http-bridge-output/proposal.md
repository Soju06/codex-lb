## Why

Codex can send an empty `response.completed.response.output` while delivering
the actual assistant and tool items through `response.output_item.done` SSE
events. Persisting the empty terminal array makes the complete-transcript
recovery flag appear enabled while leaving replay without the prior output it
needs.

## What Changes

- Materialize terminal output items from the durable SSE spool when the terminal
  response output is empty.
- Persist a bounded, self-contained replay-input snapshot at completion so
  recovery does not depend on upstream retaining every parent response.
- Record the first/root Codex turn in the durable operation ledger so the
  replay chain has an anchor even when the client sends only deltas afterward.
- Keep malformed, incomplete, or oversized event transcripts fail-closed.
- Add focused regression coverage for ordered output-item reconstruction.

## Impact

Only the opt-in complete-transcript recovery path is affected. Normal streaming,
event replay, and upstream request/response shapes remain unchanged. Snapshot
construction is best effort and never blocks or fails a live terminal response.
