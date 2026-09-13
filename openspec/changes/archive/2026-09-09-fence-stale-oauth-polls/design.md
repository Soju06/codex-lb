## Context

`useOauth` stores the active dashboard OAuth flow in React state and a mirrored `stateRef`. `poll()` reads `stateRef.current` to choose the flow ID, awaits `getOauthStatus`, then reads `stateRef.current` again to complete, apply status, or invalidate account caches.

`reset()` and `start()` replace that state while the earlier `poll()` is still awaiting. After the await, the stale poll observes the new flow and can:

- call `completeOauth` with the new flow's credentials
- write success or error onto the new flow
- invalidate account/dashboard caches because the old flow succeeded

The backend already keys status by flow ID. The defect is client-side generation mixing, not API identity.

## Goals / Non-Goals

**Goals:**

- Drop stale poll results after reset or restart.
- Keep current-flow success, error, and pending behavior unchanged.
- Prove the race with a deferred-promise unit test (no sleeps) and a mocked-browser surface check.

**Non-Goals:**

- Backend OAuth store, API, or schema changes.
- Visible dialog copy, layout, or styling changes.
- Broad OAuth state-machine refactor of `complete` / `manualCallback`.
- Aborting in-flight HTTP; fencing the apply path is enough.

## Decisions

### Generation fence, not abort-only

Keep a monotonic generation counter on the hook instance. `reset()` and `start()` increment it before replacing state. `poll()` snapshots the generation plus the current flow ID, `deviceAuthId`, and `userCode` before any await.

After the status await and after the completion await, `poll()` continues only when the generation is unchanged and the captured flow ID still matches the live flow. Otherwise it returns without `completeOauth`, without `setOauthState`, and without cache invalidation.

`start()` uses the same generation: it increments before `startOauth`, then applies the new flow only if that generation is still current after the await (and skips error-state writes in `catch` when it is not). Closing or restarting while start is in flight is the UI path that needs this latch.

Aborting the HTTP request is optional later; a late response must still be ignored even if abort is unavailable.

### Capture credentials before awaits

Completion MUST use the captured flow credentials, never `stateRef.current` after an await. Re-reading the live ref is the defect.

### Identity check is in addition to generation

Generation covers reset-to-idle and restart-to-a-new-flow. The flow-ID check is the second latch so a poll cannot complete a different live flow even if a future caller mutates state without bumping generation.

### Alternatives considered

- **Compare flow ID only:** reset-to-idle then a later start can reuse timing windows; generation is the reset latch.
- **React Query / AbortController as the only fence:** still need an apply-side generation check for responses that complete after abort.
- **Fence `complete` and `manualCallback` in the same change:** those paths are not the reported poll race; leave them unless a second failing test appears. `start` is fenced because its await has the same generation-mixing shape as poll.

## Risks / Trade-offs

- [Stale poll still runs HTTP] → Accept; ignore the result. Do not add a new client dependency for cancellation.
- [Generation bump on both reset and start] → Harmless extra increment when start follows reset; still monotonic.
- [Timer-scheduled polls after restart] → `start` already reschedules timers against the new generation; stale interval callbacks are cleared on reset/start.

## Migration Plan

Frontend-only. Deploy with the dashboard bundle. Rollback is the previous bundle. No data migration.

## Open Questions

None.
