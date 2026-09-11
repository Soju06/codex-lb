# Accepted scope and remaining delivery-stall proof

This records the accepted outcome and the regression needed before implementation. It does not select a duration or claim the stall path is implemented.

## Authority and boundary

[Soju06's September 10 disposition](https://github.com/Soju06/codex-lb/pull/1903#issuecomment-5614055969), repeated on [issue 2266](https://github.com/Soju06/codex-lb/issues/2266#issuecomment-5614718256), accepts the 256 MiB live queued-payload boundary. Blocked producer payloads count against it. Aggregate completed replay buffers and total process memory remain outside it. Completed replay retains its existing spool contract.

The same disposition requires bounded per-request delivery-stall termination, retained-prefix delivery followed by failure/EOS, reservation release, and same-session sibling progress. Those outcomes are accepted. The maximum continuous stall duration remains a maintainer decision. Upstream idle timeout measures upstream silence and cannot substitute for that decision.

Keep the native HTTP-bridge bypass pending separate validation. Native transport and residual performance acceptance remain separate. Do not archive this change or close issue 2266 on the strength of this reconciliation.

## Current implementation and evidence limits

At `d46c98b9f8ca53ed9072f9ff59ee9f229c7ba31b`, `upstream_events.py`'s `put_with_request_deadline` uses only `bridge_request_deadline`. With no deadline it awaits queue capacity without a timeout. This confirms the accepted independent stall requirement is missing; it is not a configuration mistake. No new runtime test is claimed by this document.

| Existing proof | What it establishes | What remains missing |
| --- | --- | --- |
| `test_http_bridge_reader_deadline.py` | Real shared reader releases after a shortened request deadline and fails an already-expired sibling | Healthy sibling success before long request deadlines; independent stall expiry |
| `test_http_bridge_terminal_flush_deadline_cannot_report_success_after_lost_output` | Real stream generator and dispatcher retain the prefix, emit `request_timeout`, end the stream, and restore byte accounting after draining | Combined healthy sibling proof; blocked credit measured at expiry; a non-null API-key reservation |
| `test_http_bridge_live_event_queue_applies_backpressure` | Paced/resumed order and detach unblocking through preparation and dispatch | Independent stall termination |
| `test_http_bridge_delivery_deadline.py` | Failure latch, budget fallback, and accepted-terminal/EOS-only exception | Independent delivery-stall timing |

The terminal-flush test drives service streaming and dispatch, with submission mocked. It is not an HTTP socket slow-reader trial. Its reservation starts as `None`; the final `None` assertion does not prove real API-key settlement. Historical execution results stay tied to their recorded candidates in `deadline-flush-20260908.md`.

## Concrete regression and design plan

Use the existing injected clock/scheduler and real shared reader plus stream generator. Call the future maintainer-selected duration `D`. `D` is a test parameter here, not a production default. Keep both request deadlines beyond the whole scenario. Do not shorten the request budget to simulate delivery-stall expiry.

1. Prepare A and B on one session through the existing bridge request preparation path. Attach their consumers and use a non-null API-key reservation for A. Consume A's `response.created`, then pause its consumer.
2. Send enough A events through the real reader to fill its two-event queue. Send one more A payload. Establish that its producer is waiting with charged bytes. Queue B's created/delta/completed frames behind it.
3. Advance virtual time to just before `D`. Require A's put to remain blocked and its retained bytes to remain accounted. Advance through `D` without reading A and with both request deadlines still in the future.
4. Require A's blocked put to return, its producer-owned credit to be released, and B's frames to reach successful completion on the same session. A's retained prefix and terminal bytes must remain charged. Compare accounting by ownership, not simply against zero.
5. While A remains paused, deliver A's upstream terminal before any further downstream read. Require persistence and settlement to finish before resuming A. Verify original upstream persistence and exactly-once settlement of its actual reservation. Do not replace upstream outcome with the downstream delivery failure or settle twice during later detach. Exercise explicit abandonment as a separate path.
6. Resume A. Require the exact retained prefix, one explicit failure, and EOS. Reject a later successful completion after rejected payload output. If only EOS was blocked after an accepted terminal payload, retain the existing accepted-terminal exception.
7. Drain owned tasks and require no leaked timers, pending puts, request ownership, or bytes. Repeat relevant controls with real timing and both HTTP-error propagation modes. Once SSE starts, terminal failure must stay inside the stream.

The initial regression must fail on the current head because the shared reader stays blocked past `D`, not because a proposed setting or helper is absent. Record setup/import failures separately. The implementation can then reuse the queue's existing expiry/failure latch and producer cleanup, using the earlier of the request deadline and the accepted stall bound for a capacity wait. Keep successful enqueue progress, timer cancellation, repeated cancellation, and EOS-only behavior covered. This is a proposed implementation route, not proof that the current helpers already satisfy the full contract.

A progressing-consumer control must complete even when total stream duration exceeds `D`; the bound concerns continuous blocked delivery. Test that wakeups without enqueue progress do not postpone expiry. Keep the existing process-budget failure control, including a failure payload that cannot reserve bytes.

## Recovery-mode removal composition

PR [2336](https://github.com/Soju06/codex-lb/pull/2336) merged as `aae61f6f30466b3e72fcc15e3c5a38c98e4971cb`. The composition preserves its removal of optional ambiguous-continuation recovery modes. It retains the bounded queue scheduler and shielded downstream detachment, together with their pending-terminal and cancellation regressions. Removed-mode cooldown tests are deleted; fail-closed tests remain.

This composition does not implement the independent live-queue stall bound or select its duration. The shared-reader and actual-settlement proof above remains required. Native bypass and completed-replay contracts remain unchanged.
