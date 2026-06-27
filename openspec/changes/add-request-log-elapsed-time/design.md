## Context

`request_logs` already persist `latency_ms`, `latency_first_token_ms`, transport, tier, and failure metadata for unary, streaming, and WebSocket proxy flows. The current `latency_ms` value is measured from the proxy request start marker, so it includes pre-upstream work such as account selection, refresh, routing, or bridge setup. The requested enhancement needs a separate timing that starts only when the proxy is about to submit the request upstream and ends when the upstream response or final stream frame completes.

The dashboard request-log detail dialog already renders request metadata from `GET /api/request-logs`, so the change can flow through the existing request-log model, mapper, and frontend schema chain.

## Goals / Non-Goals

**Goals:**
- Persist a nullable `elapsed_ms` value on `request_logs` without changing existing `latency_ms` semantics.
- Record `elapsed_ms` consistently for unary, SSE/streaming, WebSocket, and warmup-related request-log rows.
- Expose `elapsedMs` through the dashboard request-log API.
- Render separate `Upstream elapsed` and `Total elapsed` fields in Request Details using the shared duration formatter.

**Non-Goals:**
- Reinterpreting or renaming `latency_ms`.
- Backfilling historical rows with inferred elapsed values.
- Adding request-log list columns, filtering, sorting, or analytics based on `elapsed_ms` in this change.

## Decisions

### Persist `elapsed_ms` as a separate nullable request-log column

`latency_ms` already has operator value as a proxy-wide duration. Reusing it for upstream-only elapsed timing would silently change semantics and lose the broader signal. A separate nullable `elapsed_ms` column preserves backward compatibility and keeps legacy rows valid.

Alternative considered:
- Overwrite `latency_ms` with the upstream-only timing. Rejected because it breaks current meaning and prevents the requested side-by-side comparison between upstream and total durations.

### Capture elapsed start and completion at existing request-log write surfaces

Each proxy path already has a closeout point where it computes final status and writes a request-log row. The implementation should thread an additional monotonic upstream-start marker from the point immediately before upstream submission through to that closeout. That keeps the change local to existing lifecycle boundaries instead of introducing a separate timing persistence subsystem.

Expected capture model:
- Unary HTTP requests: start immediately before the upstream request helper is invoked; complete when the upstream response returns or the final failure path is handled.
- Streaming/SSE requests: start immediately before the upstream stream call is initiated; complete in the existing `finally` block that writes the request log after the final emitted frame or terminal error.
- WebSocket-backed requests: start when the upstream turn is sent to the upstream transport; complete when the request state settles and the request log is written.
- Warmup and similar internal rows: use the same pattern so `elapsed_ms` coverage matches the existing request-log population behavior.

Alternative considered:
- Derive elapsed from archive records or downstream event timestamps. Rejected because it would be less reliable, more complex, and unavailable for rows without archive data.

### Expose and format both timings in the Request Details dialog only

The user request targets the detail dialog, and the existing UI already reserves a compact metadata grid there. The frontend should add `elapsedMs` to the request-log schema and use a shared formatter for `elapsedMs` and `latencyMs`, producing `x.x ms` up to and including `1000.0 ms`, then `x.x s` above that threshold. The dialog should present `Upstream elapsed` for `elapsedMs` and `Total elapsed` for `latencyMs` as separate fields so operators can compare the two timings without parsing combined text.

Alternative considered:
- Add `elapsed_ms` to the main recent-requests table. Rejected as scope expansion beyond the requested enhancement.

## Risks / Trade-offs

- [Upstream start markers drift from real submission point] → Place the marker adjacent to the actual upstream client invocation in each path and cover it with regression tests per transport.
- [Not every error path currently has a natural upstream-start marker] → Leave `elapsed_ms` nullable for pre-upstream failures rather than writing misleading zero or proxy-wide values.
- [Legacy rows do not have `elapsed_ms`] → Keep the field nullable end to end and render a placeholder when absent.
- [Formatting inconsistency between elapsed and total timings] → Use one shared frontend formatter for both values and cover threshold behavior in component or utility tests.

## Migration Plan

1. Add a new Alembic revision that introduces nullable `request_logs.elapsed_ms`.
2. Update ORM and request-log persistence layers to accept and store the new field.
3. Thread the new value through request-log API schemas and mappers.
4. Update the dashboard Request Details dialog to render separate `Upstream elapsed` and `Total elapsed` fields.
5. Validate the OpenSpec change and run backend/frontend regression tests.

Rollback: retain code compatibility with `null` values and drop the column in the Alembic downgrade if the change must be reverted before dependent features rely on it.

## Open Questions

None.
