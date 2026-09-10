## Implementation

- [x] Add an atomic same-context enqueue path that does not wait for durable I/O.
- [x] Prove queued event ordering and terminal draining under a blocked append.
- [x] Preserve owner/recovery fencing tests and validate code and specs.
