## Context

The request detail dialog already renders Plan, Status, Model, etc. from `RequestLog` data. `latencyMs` is available in the typed response but not displayed. Adding it is a one-field addition with a format helper.

## Goals / Non-Goals

**Goals:**
- Show `latencyMs` as `Elapsed` in the request detail dialog next to `Plan`

**Non-Goals:**
- Latency columns in the table rows
- TTFT (`latencyFirstTokenMs`) display
- Latency charting or aggregation

## Decisions

**Formatter: `formatElapsed(ms)`**
- `< 1000 ms` → `"500 ms"` (whole number, ms unit)
- `>= 1000 ms` → `"1.5 s"` (one decimal, s unit)
- `null | undefined` → `"—"` (em dash, consistent with other fields)

**Placement**: In the existing `<RequestDetailField>` grid next to `Plan`, matching the user's request and keeping related metadata together.

## Risks / Trade-offs

- None. Purely additive display of already-fetched data.
