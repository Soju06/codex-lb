## Context

The outer in-flight middleware currently tracks only HTTP requests and bypasses all WebSocket scopes. Shutdown therefore cannot close WebSocket admission or observe an active WebSocket turn. Responses WebSocket connections are intentionally long-lived, so blindly waiting for every session without teaching the session loop how to drain would turn idle clients into shutdown blockers.

Uvicorn 0.47 closes every connection before it waits for ASGI tasks or runs application lifespan shutdown. A lifespan-only barrier is therefore too late for terminal WebSocket delivery. The existing Helm preStop calls `/internal/drain/start`, but ignores `in_flight`, swallows start/status errors, and ends after a fixed dwell. The embedded metrics server also installs its own process-signal handlers.

## Goals / Non-Goals

**Goals:**

- Close the WebSocket handshake race at the same outer admission barrier as HTTP.
- Preserve terminal logging, API-key settlement, and account-health ordering for turns active when drain begins.
- Make idle admitted WebSocket sessions release the drain promptly.
- Put the barrier before Uvicorn connection shutdown, preserve launcher failure semantics, and declare the Uvicorn API floor the launcher uses.
- Reuse one absolute drain deadline across preStop, SIGTERM, Uvicorn, lifespan persistence, and detached control-plane drain.
- Make Helm preStop in-flight-driven without adding a setting or dependency.
- Keep later embedded/TestClient lifespans isolated without erasing a signal committed for the current server start.

**Non-Goals:**

- Add a second drain timeout or operator setting.
- Force-close an active turn before the existing shutdown deadline.
- Make Realtime/Live sessions participate in Responses turn-aware drain.
- Treat post-deadline process cleanup as part of the application drain budget.
- Change replay, settlement, or routing policy beyond the lifecycle ownership races required to preserve their existing exactly-once behavior.
- Intercept an operator who deliberately bypasses every shipped launcher and invokes a third-party ASGI server directly.

## Decisions

The outer `InFlightMiddleware` increments before it checks the barrier. A synchronous Python signal can interleave between bytecodes; increment-first makes the result fail-closed: the scope is either rejected and immediately unregistered or already visible as pre-barrier work. Every WebSocket route gets late-admission rejection, but only `/v1/responses` and `/backend-api/codex/responses` remain counted through handler exit. Realtime/Live routes stay subject to normal Uvicorn connection shutdown and cannot pin the Responses drain deadline.

The Responses WebSocket service loop inspects drain state at its receive polling boundary. With no pending request or owned terminal-message task, it closes the downstream socket with 1012 and exits. A `response.create` observed after the barrier receives a local service-unavailable error before preparation, reservation, registration, or upstream send.

Each received upstream text message gets an explicitly registered child task before its first await. That task owns processing plus downstream delivery, is stored on `_WebSocketUpstreamControl`, is included in the persistence-task registry, and is shielded from reader cancellation. If the reader is cancelled, it waits for the child only within the remaining shared deadline before propagating cancellation. This closes the terminal-state-pop window without adding a second turn counter or deadline. For every terminal outcome that writes account health, API-key settlement is awaited before finalization returns; terminal request-log ownership is published before the health write, and a failed settlement or log handoff suppresses it. Account-health failures are isolated so they cannot prevent terminal downstream delivery. Detached request-log and follow-up settlement tasks remain covered by the same persistence drain. Cancellation while a turn is staged for transparent replay retains the last upstream account for its single cancelled request log and releases its reservation exactly once.

Scope teardown uses the same outer deadline and a fixed ownership order: request reader cancellation once, close the upstream transport so a cancellation-resistant `receive()` can unwind, then await that already-cancelled reader without cancelling it again. Reader failure is consumed so lease release and the one terminal cleanup of remaining request state still run.

`GracefulDrainServer.handle_exit()` commits the barrier synchronously, and its `shutdown()` waits for `in_flight` before calling Uvicorn's connection-closing implementation. It passes only the remaining float budget into Uvicorn, then waits through that remaining application budget plus a fixed 25-second post-drain phase. If that reserve expires, the launcher cancels Uvicorn cleanup and terminates with the most recently captured signal, or SIGTERM for programmatic shutdown, rather than return a cancellation-resistant task to asyncio runner teardown. Helm reserves two seconds for a failed preStop start plus 30 seconds after the application deadline, so five seconds remain after the cleanup bound for signal delivery and process exit. Reversible and committed deadlines are independent monotonic candidates whose effective value is always their minimum. Committed candidates are append-only so a synchronous signal that re-enters deadline publication before or after an outer preStop publication can only tighten, never overwrite, the effective deadline. A monotonic drain-start token is the lock-free signal linearization point: an invocation that observed no prior start publishes its candidate even if a synchronous signal commits between later Python bytecodes. Headerless later shutdown calls reuse the committed deadline; an explicit preStop deadline may still tighten it because that absolute value was anchored before the request reached the process. A server-start generation resets process state before Uvicorn captures signals and marks the matching application lifespan as prepared; that lifespan consumes the marker without resetting a signal latch. Only a lifespan that completed without that pending server marker permits a later embedded/TestClient lifespan to reset process state. The CLI forces one worker per instance even when `WEB_CONCURRENCY` is present, preserves startup-failure and KeyboardInterrupt behavior, and keeps the embedded metrics server signal-neutral. Shipped Compose and documented local/probe commands delegate to that CLI; Compose watch uses source sync plus service restart instead of Uvicorn reload so development keeps the same signal owner. The launcher calls `Config.load_app()`, so package metadata declares `uvicorn>=0.47.0`.

The existing `proxy-admission-control` contract requires one worker per instance for shared per-account caps, so the owned launcher does not delegate ambient multiprocess configuration to Uvicorn.

Every non-text/binary upstream receive result synchronously marks the socket for reconnect before archive attribution or pending-request inspection can await a lock. A request state records when its current attempt reaches the immediate pre-send boundary. A clean close leaves an admitted but still unsent state owned by the sender rather than failing it or consuming post-send replay state. After all admission, ownership, account-cap, and installation-metadata awaits, the sender performs a final latch check immediately before send. It identity-claims only that unsent state, releases the retired account-local create lease, and reconnects without incrementing or rewriting replay state; a fresh account re-acquires its own lease. A distinct older reader-owned replay wins, and the separately registered current state is terminally finalized once rather than orphaned.

A send failure can race the reader after the send boundary. Before cancelling that reader, the sender marks its control object for reconnect so reader teardown cannot close the downstream socket before ownership is harvested. A generic send exception may reuse only a clean-close replay owner already published by the reader; the retired account-local create lease is released before the replacement account acquires its own lease, and successful terminal delivery, logging, and settlement occur once. A typed transport send failure is treated as ambiguous delivery and is never replayed: a reader-published claim is transferred to a registered finalization task that emits and persists the typed terminal failure exactly once.

Pending-batch failure uses the same ownership rule. While holding the pending queue lock, cleanup removes the batch and creates and registers one shielded finalization task before releasing the lock. Caller cancellation waits only within the shared deadline (or the existing bounded non-drain timeout) and does not cancel that sole child owner, which remains visible to persistence drain. Per-request finalization releases admission, the account-local create lease, reservation, and create gate independently so one release error cannot skip the remaining owners. Scope cleanup finalizes request state before releasing the connection lease; a connection-lease release failure is logged without replacing the original cancellation.

Operator drain and committed shutdown use separate state. `/internal/drain/stop` may clear the reversible operator state, but committed admission and its deadline are one-way and cannot be erased by a signal interleaving.

Helm runs `python -m app.core.prestop`. The Python helper measures routing dwell from its own process start and conveys that helper-anchored absolute monotonic deadline in the loopback-only drain-start request. A deadline-bearing start commits the one-way barrier, while a headerless operator start remains reversible. The server accepts only a finite deadline, clamps it to at most its own configured timeout from receipt, and returns the effective absolute deadline. The helper validates that committed response and tightens its local deadline to the returned value, so request latency or an earlier operator/signal deadline cannot create a second period. It polls strict status and returns once the dwell has elapsed with `draining=true` and `in_flight=0`, or at that effective deadline. Start/status failures return promptly so direct SIGTERM is the fallback, without rolling back an already committed barrier. Kubelet's hard termination grace begins before helper launch; exec/Python launch latency therefore consumes only that hard grace and cannot be represented in the application's monotonic budget. Rendering requires the drain timeout to cover the dwell and the Kubernetes grace period to reserve two seconds for failed preStop start plus the launcher's fixed 30-second post-drain process-exit reserve after helper start. The default adds three seconds of launch headroom above that arithmetic minimum.

## Risks / Trade-offs

- [An active turn never becomes terminal] → The existing configured shutdown drain timeout remains the hard bound.
- [An idle socket is blocked in receive] → Bounded receive polling wakes the loop to observe drain.
- [A client races `response.create` with drain] → The turn is registered before its final drain check. If that check observes the barrier, the turn is removed and rejected; if the check completes first, the turn is already visible to cleanup and drains to terminal.
- [A clean upstream close races the next turn] → The reconnect latch is set immediately after receive and before pending-state attribution can block.
- [A clean close lands while the next turn waits in admission] → The reader leaves the unsent state sender-owned; the final pre-send check transfers it to a fresh transport without spending replay budget or retaining the retired account lease.
- [A send exception lands after the reader classified a clean close] → Reconnect is latched before reader cancellation; a generic failure reuses only the reader's single replay owner, while a typed ambiguous transport failure is finalized once without replay.
- [Caller cancellation lands after pending cleanup empties the queue] → The batch already belongs to a registered shielded finalization task that remains visible to persistence drain and is not cancelled at the wait bound.
- [A cancelled receive waits for transport close] → Scope cleanup publishes one tracked finalization task before waiting, cancels the reader once, closes the upstream, and awaits cleanup only inside the remaining shared deadline without cancelling its sole local-state owner at the bound.
- [Connection-lease release raises during scope cancellation] → Request-state owners finalize first; the release failure is logged and cannot replace the original cancellation.
- [SIGTERM re-enters deadline initialization] → Every invocation that observed the drain-start token open publishes its candidate; the effective deadline is the minimum of reversible and committed candidates.
- [A signal lands between server start and lifespan startup] → The prepared server generation makes lifespan startup consume, rather than reset, the committed signal latch.
- [A non-Responses WebSocket is long-lived] → It is rejected after the global barrier but does not enter turn-aware accounting; Uvicorn closes it after the Responses pre-barrier work drains or times out.
- [Application drain consumes the whole deadline] → The launcher stops awaiting Uvicorn post-drain cleanup 25 seconds after the application deadline and forces the captured signal, or SIGTERM for programmatic shutdown, so runner teardown cannot extend the bound. Kubernetes termination grace reserves two seconds for failed preStop start plus 30 seconds after the helper-anchored application deadline, including five seconds for process exit; its default also retains three seconds for helper-launch headroom.

## Migration Plan

The chart default increases from 60 to 65 seconds and needs no operator action
when no termination-grace override or reused release value is retained. Before
installing or upgrading, audit existing values files, `--set` arguments, and
values retained by `helm upgrade --reuse-values`. A retained
`terminationGracePeriodSeconds` below
`config.shutdownDrainTimeoutSeconds + 32` fails Helm rendering before resources
are applied; at the default 30-second drain timeout the minimum is 62 seconds
and the chart default is 65 seconds. Raise every retained low value explicitly
to at least the computed minimum; 65 preserves the default helper-launch
headroom when the drain timeout remains 30 seconds. Merely omitting the key does
not clear its stored value under `--reuse-values`. Adopting the chart default
requires an intentional non-reuse or `--reset-values` upgrade with the key absent.
A failed upgrade leaves the existing release in place. Rollback remains a
code/chart rollback; no schema, data, or dependency migration is involved.

## Open Questions

None.
