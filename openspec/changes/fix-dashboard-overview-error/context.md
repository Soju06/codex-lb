# Dashboard overview terminal-error context

## Purpose

Separate an initial pending query from a terminal failure that has no overview
data, while retaining healthy shell and cached-data behavior.

## Decision

Pending no-data state keeps the existing skeleton. Terminal no-data state uses
the existing alert and button primitives. Presentation state retains the error
while TanStack Query clears its error during refetch, then clears after the
request settles.

## Constraints

- Retry only the overview query.
- Do not change retry count, query key, API, shell, or global alert behavior.
- Cached overview content remains visible on later refetch errors.
- No timing waits or polling in tests.

## Example

The overview endpoint exhausts retries with HTTP 503. The shell remains, the
skeleton disappears, an alert and Retry appear, Retry becomes busy during the
single refetch, and recovered overview content replaces the error.
