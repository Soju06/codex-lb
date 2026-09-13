## Context

Both Images adapters commit a limited API-key reservation before constructing an
internal Responses stream. They intentionally pass
`api_key_reservation=None` into `stream_responses`, because captured
`tool_usage.image_gen` tokens make the Images route the sole settlement owner.
Before returning either an SSE or JSON response, `_prime_upstream_stream` awaits
the first upstream event. Its existing `ProxyResponseError` branch invokes the
route-provided `on_error` release callback, but `asyncio.CancelledError` is a
`BaseException` and bypasses that branch. A cancellation before the first event
therefore ends request ownership while quota remains reserved until the
six-hour stale sweep.

PR #1822 established tracked settlement after image usage is captured and
explicitly excluded broader pre-terminal cancellation cleanup. The proxy
already provides `_await_cleanup_deferring_cancellation` and
`_release_reservation_deferring_cancellation` for request-owned cleanup that
must finish despite active cancellation.

## Goals / Non-Goals

**Goals:**

- Close the upstream iterator when cancellation interrupts its first
  `__anext__` call.
- Invoke the route-owned error callback exactly once in a cancellation-safe
  cleanup window, then propagate the original terminal unchanged.
- Restore limited-key quota immediately when release persistence succeeds.
- Cover generation and edit through their actual HTTP route surface.

**Non-Goals:**

- Changing the existing `ProxyResponseError` conversion or callback behavior.
- Changing post-first-event streaming cancellation, captured-token
  finalization, billing, settlement retry policy, or stale-reclamation timing.
- Passing the reservation into the internal Responses settlement path or
  adding another reservation owner.
- Changing public Images request, response, or SSE formats.

## Decisions

1. **Handle cancellation at the first-frame ownership seam.** Catch
   `asyncio.CancelledError` only around `iterator.__anext__()`. At that point
   the caller has committed the Images reservation, no image usage has been
   captured, and the supplied `on_error` callback remains its sole cleanup
   owner. Broader route `finally` blocks were rejected because they would
   overlap successful captured-token settlement. Catching `GeneratorExit` was
   also rejected: `_prime_upstream_stream` is a normal coroutine, so synchronous
   coroutine close forbids suspension and cannot await iterator or reservation
   cleanup. FastAPI request disconnects cancel the owning task with
   `CancelledError`, which is the confirmed route terminal.
2. **Close and release as separate cancellation-deferring operations.** First
   close the iterator, then run `on_error`, using the existing cleanup deferral
   primitive for each operation. Attempting them independently ensures a close
   failure cannot skip reservation release. A raw await was rejected because
   repeated task cancellation could interrupt cleanup before persistence
   completes.
3. **Preserve the original cancellation.** Record close or callback failures
   with cancellation-neutral warnings and use bare `raise`. Cleanup diagnostics
   must never replace the client's `CancelledError`.
4. **Prove both Images owners at the route surface.** A parameterized
   integration regression creates a real limited key, starts generation or
   edit, waits until the fake upstream is blocked before its first yield,
   cancels the client request, and verifies cancellation propagation, iterator
   closure, one release call, persisted `released` state, and restored quota.

## Risks / Trade-offs

- **Cleanup delays cancellation propagation.** The handler deliberately waits
  for the existing bounded persistence operation because returning earlier
  would recreate the reservation leak.
- **Release persistence can still fail.** The handler logs the failure,
  preserves the original terminal, and leaves the reservation eligible for the
  existing stale-reclamation backstop. This exceptional fallback does not
  weaken normal request-owned cleanup.
- **Synchronous coroutine close cannot await cleanup.** Calling `.close()` on
  the suspended priming coroutine injects `GeneratorExit` and rejects any
  attempted suspension. The handler therefore leaves `GeneratorExit` to normal
  coroutine unwinding instead of pretending asynchronous cleanup can complete;
  the Images HTTP routes use task cancellation and deliver `CancelledError`.
- **Post-first-event cancellation is distinct.** Existing translated-stream
  finalization owns that lifecycle and remains unchanged by keeping this catch
  limited to the initial `__anext__` call.
