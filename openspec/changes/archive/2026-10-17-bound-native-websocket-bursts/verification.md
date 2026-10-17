## Regression and scope

On base 72630bbfeee9e3b0d9acfb95e2b1e706b8b0188e, the new 256-frame regression fails with consumer_backpressure before the consumer drains. The old 64-event decoded-message queue is distinct from the already byte-bounded helper event queue.

Issue #2594 documents this exact failure and has no linked implementation. The earlier #2173 fixes HTTP/helper event buffering, not the decoded WebSocket queue. Open #1903 concerns HTTP bridge consumer backpressure; its WebSocket adapter changes select the transport, not the receive buffer. No duplicate repair was found.

## Verification

- Native wire, burst and native egress suites: 93 passed using the real helper and isolated loopback WebSocket servers. Raw and interpreted frames preserve the 256-frame order while another send acknowledgement completes before receive drains.
- WebSocket client/fallback and burst suites: 121 passed.
- Final burst tests: 7 passed, covering event/byte overflow, payload accounting, pending-send cancellation and peer isolation.
- Ruff and type checking passed; CI-pinned OpenSpec 1.11.0 validates all 67 canonical specs and the strict scoped change.
- Required full local CI ran with isolated SQLite and PostgreSQL. Frontend checks (1658 tests), Python lint/type checks, Rust tests/build and dependency audit passed. The backend suite reported 10487 passed, 99 skipped and 1 xfailed, then stopped on test_output_under_a_temporary_filesystem_is_refused. That unchanged macOS-specific assertion also fails on the base commit; later local integration/package stages were not reached. GitHub CI remains the authoritative complete gate.

## Review and limits

The committed production diff and existing overflow/cancellation paths were inspected manually. The independent review service rejected the request without a substantive review; no independent approval is claimed. No deployment or external provider test is part of this PR.
