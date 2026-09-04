## Context

The process-local `OAuthStateStore` tracks pending browser flows, one shared `OAuthCallbackServer`, and serialized server-stop work. Pending flows already carry a 15-minute wall-clock deadline and are rejected after expiry, but local pruning only occurs when another OAuth operation touches the store. A fully abandoned flow can therefore leave the callback socket serving indefinitely.

Docker port publication is a separate lifetime layer. The stock launch commands and both Compose files publish `1455:1455`, so Docker owns host port 1455 whenever the container runs even after the in-container listener stops. Codex Desktop cannot bind its own OAuth callback listener in that state. Fixing only the process-local lifetime would therefore leave #2076 reproducible on the recommended Docker path.

The durable OAuth table deliberately treats expired pending rows as absent on read and purges them opportunistically. Fixing #2076 requires correcting the process-local listener lifetime and the Docker host-port default, but it does not require timer-driven durable-row deletion.

## Goals / Non-Goals

**Goals:**

- Release the process-local callback socket automatically when the last pending browser flow expires.
- Keep host port 1455 free in stock Docker deployments so Codex Desktop can use it independently.
- Preserve the shared listener across overlapping unexpired flows.
- Serialize a concurrent new browser start against listener shutdown.
- Keep all deadline work owned, replaceable, and drainable in tests and full store reset.

**Non-Goals:**

- Changing OAuth APIs, flow TTL, callback URI, database schema, or configuration.
- Replacing opportunistic durable-row cleanup with timer-driven database writes.
- Changing the fixed OpenAI redirect URI or the in-container callback port.
- Removing browser OAuth or its automatic callback for native/uvx and explicit dedicated-host deployments.

## Decisions

### Do not publish host port 1455 by default in Docker

The portable `docker run` examples and shipped Compose files publish only port 2455. Docker users can add accounts with the existing device-code flow or paste the browser's final localhost callback URL into the existing manual callback field. Documentation explains both paths, warns that the callback URL is sensitive, and offers an explicit loopback-only `127.0.0.1:1455:1455` opt-in for dedicated machines that do not run Codex Desktop or another callback consumer.

This changes a deployment default rather than adding a setting. A conditional Docker port mapping cannot follow the lifetime of an in-container task, and a permanently published default cannot coexist with Codex Desktop's listener. Keeping the port unpublished is the only zero-config default that leaves independent host applications free to authenticate.

### Use one store-owned deadline task

The store will retain one browser-expiry task rather than create one detached task per flow. The task computes the earliest pending deadline, sleeps through a browser-expiry-specific seam, reacquires the store lock, and prunes expired local flows. If another pending flow remains, it recomputes the next deadline; otherwise it begins the existing serialized callback-server stop path.

One task bounds background work, naturally supports overlapping flows, and gives reset/tests one handle to cancel and await. A periodic global scheduler was rejected because the lifetime is local to the process and exact deadlines are already available. Per-flow tasks were rejected because they multiply ownership and cancellation races around one shared listener.

### Revalidate state after every sleep

The deadline task will treat sleep completion only as a prompt to re-check state under the store lock. It will use the same wall clock as flow expiry and will neither assume the original flow still exists nor stop a listener while any unexpired pending flow remains. A separate sleep seam avoids interference from device-code polling tests that replace the existing device sleep function.

### Serialize listener installation and retirement under the store lock

A browser start currently waits for a known stop task before entering the store lock. Deadline cleanup could register a stop after that wait but before the flow is installed. The start path will therefore loop: wait for an observed stop, acquire the store lock, and retry if a stop appeared in the gap. It installs the flow/listener only while no stop task is registered.

The retiring deadline task clears only its own task slot by identity. It registers listener stop while holding the same lock, so a concurrent start either makes the listener non-idle first or observes the stop and waits for a replacement. Existing server identity checks prevent an old stop from clearing a newer listener.

Listener startup is also store-owned. A browser flow is persisted before any local listener is published, then the store publishes the flow, listener, startup task, and deadline task in one locked transition. Request cancellation cannot cancel the shared startup task, and reset waits for startup before stopping the listener. This prevents a canceled request or concurrent reset from leaving a started-but-untracked socket.

### Begin terminal cleanup atomically

When a local browser flow becomes terminal—or durable reconciliation removes or terminalizes it—the same store lock transition checks whether any browser flow remains pending. If none does, it cancels the obsolete deadline task and registers the store-owned listener-stop task immediately. Callers drain the canceled deadline task, but callback handlers do not await listener shutdown because the server cleanup may wait for the active callback response to finish.

The shared stop supervisor retains listener ownership and retries transient shutdown failures with bounded delay and a bounded attempt count. If one attempt batch is exhausted, its waiter receives the terminal error while the listener and completed stop task remain tracked; a later cleanup or browser start can create a fresh bounded attempt batch without publishing a replacement prematurely. Retry belongs to the shared supervisor, not the expiry watchdog, so expiry, terminal completion, durable reconciliation, reset, and partial-start cleanup all receive the same behavior.

Successful callback settlement is also store-owned across cancellation. Account-token persistence, cache invalidation, and the local terminal transition run in one shielded operation so cancellation cannot leave durable success paired with a locally pending flow and obsolete listener lifetime.

### Fence stale work across full-store reset

The store advances a generation on reset. Browser-start persistence and durable reconciliation capture their generation before leaving the store lock and revalidate it before publishing local state. Results from work begun before reset are therefore discarded locally even if the database operation completes later. Reset cancels pollers and drains the newly owned startup, stop, expiry, and terminal-transition work before returning.

### Keep durable expiry cleanup opportunistic

The watchdog removes expired process-local state and releases the process-local socket. It does not add a database write at every deadline. Durable getters already treat expired rows as absent, and ordinary OAuth writes opportunistically purge them; retaining that design avoids adding database availability to a local socket-safety path.

## Risks / Trade-offs

- **[Risk] A task wakes early or the wall clock changes.** → Recompute deadlines and pending-flow state under the lock after every wake.
- **[Risk] Docker browser OAuth no longer redirects into the container automatically by default.** → Keep device-code sign-in as the recommended Docker path, retain the existing manual callback field, and document a dedicated-host opt-in mapping.
- **[Risk] Existing containers retain their old published ports after an image update.** → Document that operators must recreate the container without the mapping while preserving the data volume.
- **[Risk] A callback completes while deadline cleanup wakes.** → Both paths use the same lock and the existing idempotent serialized server-stop task.
- **[Risk] A new flow starts while the old listener is stopping.** → Re-check the stop-task slot under the insertion lock and retry after shutdown completes.
- **[Risk] A request is canceled or reset runs while the listener is starting.** → Keep startup in a shielded store-owned task; reset serializes stop after startup, and the flow always has deadline ownership before the request awaits startup.
- **[Risk] A terminal callback tries to stop the server serving that callback.** → Register stop atomically but do not await it from the callback handler; the response returns before runner cleanup completes.
- **[Risk] Listener shutdown keeps failing.** → Bound each retry batch, propagate exhaustion, retain listener ownership, and let a later operation retry before any replacement can be installed.
- **[Risk] Database work begun before reset completes afterward.** → Capture and verify a store generation before publishing any local result.
- **[Risk] A successful callback request is canceled after account persistence.** → Run account persistence and local terminal settlement as one shielded, store-owned transition.
- **[Risk] Tests or reset leak a long-lived sleeper.** → Store the task explicitly, cancel and await it during reset, and drain injected stores in tests.

## Migration Plan

No data migration is required. Existing Docker containers created with `-p 1455:1455` must be recreated without that mapping because an image update cannot alter container port bindings; the named data volume is preserved. The runtime task exists only while a process has locally started browser OAuth. Rollback restores lazy local pruning and the old launch examples without changing persisted data.
