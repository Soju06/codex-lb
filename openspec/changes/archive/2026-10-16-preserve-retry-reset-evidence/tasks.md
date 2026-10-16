## 1. Reproduce and implement

- [x] 1.1 Restore the HTTP-route regression from #2440 and verify it fails on main because a five-day reset becomes 30 seconds.
- [x] 1.2 Preserve parsed reset metadata across bridge and direct WebSocket retry health writes; verify route and unit regressions pass.
- [x] 1.3 Cover malformed error metadata, file/client ownership, and deferred keyed health; verify parsing and settlement tests pass.

## 2. Verify and document

- [x] 2.1 Run affected suites, lint, type checking, and architecture checks; record outcomes and any baseline limitations.
- [x] 2.2 Validate the OpenSpec change and main specs strictly, verify scenario coverage, then sync requirements/context and archive the completed change.
- [x] 2.3 Prepare a focused PR with original-author credit, Refs #1707 / #2440, reproduction evidence, and explicit limits concerning transient backoff and automatic reviewer recovery.
