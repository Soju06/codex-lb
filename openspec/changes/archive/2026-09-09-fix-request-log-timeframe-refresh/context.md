# Request-log timeframe and refresh context

## Purpose

Keep rolling Request Logs windows truthful during long sessions and retain
useful rows during transient refresh failures.

## Decision

The frontend sends `timeframe=1h|24h|7d`; each backend request derives its
lower bound from server UTC. `all` remains unbounded. Literal bounds remain
compatible, but `timeframe` and `since` are mutually exclusive.

Membership always uses the live lower bound. Only display total metadata keeps
the 30-second cache, keyed by symbolic timeframe rather than derived timestamp.

A listing error renders independently from content. If data exists, the same
section-local alert/Retry appears above retained filters/table/pagination.

## Constraints

- Overview and request-log timeframes remain independent.
- List and options remain separate requests.
- Preserve all manual filters, literal bounds, response schemas, and retry
  cadence.
- No new component, token, setting, or dependency.
