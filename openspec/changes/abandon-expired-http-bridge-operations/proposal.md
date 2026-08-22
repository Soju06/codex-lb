## Why

The durable HTTP bridge operation ledger fail-closes duplicate
`response.create` dispatches, but an operation that lost its upstream socket
before terminal status can remain `unknown` or `acknowledged` forever. The
existing retention job intentionally preserves those rows while an owner
lease is live, but it has no state transition that makes an ownerless,
eventless operation converge. Restarting therefore preserves the same fence
and does not unblock the next continuation.

Live evidence for #1876 shows non-terminal rows older than five days and
repeated `operation_already_recorded_no_status_proof` /
`upstream_operation_status_unknown` responses. This is independent of the
already-deployed anchor classification and denied-anchor retirement fixes.

## What Changes

- Add an explicit terminal `abandoned` operation state for an ambiguous
  operation whose inactivity exceeds the existing HTTP bridge request budget
  and whose durable owner/request proof is no longer live.
- Run a bounded, request-independent sweep from bridge heartbeat maintenance
  while protecting operation IDs that still have an in-process pending request.
  Oversized protection snapshots are scanned in finite keyset slices whose
  cursor advances across heartbeats.
- Make the transition a compare-and-set over the operation state,
  `updated_at`, durable event-spool progress, session owner instance, and owner
  epoch. A concurrent status proof or recovery claim wins; a late writer
  cannot revive an abandoned row.
- Retain the operation row and event spool for normal operation-ledger
  retention. Do not resend or delete an ambiguous upstream operation.
- Make a continuation that encounters `abandoned` return the canonical
  `previous_response_not_found` contract so Codex can retry with full history
  without the proxy dispatching the ambiguous operation again.
- Emit structured abandonment diagnostics and a low-cardinality Prometheus
  counter.
- Keep the oversized-protection scan from holding the SQLite writer section
  beyond one finite maintenance slice, without repeatedly rescanning a
  protected prefix.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Expire an unowned ambiguous HTTP bridge operation
  into a terminal, recovery-guiding state without duplicate dispatch.
- `proxy-runtime-observability`: Expose operation abandonment as an operator
  diagnostic and metric.

## Impact

- Affected code: durable bridge repository/coordinator, bridge heartbeat
  maintenance, HTTP bridge operation admission, metrics, and tests.
- Affected data: no new prompt/output data is stored; existing rows gain only
  a terminal state value and remain subject to existing retention.
- No automatic replay, account movement, or change to the existing request
  budget.
- A new `CODEX_LB_*` setting is intentionally not added: the existing bridge
  request budget is the safety boundary, with a 30-minute minimum floor in
  case an operator configures a shorter budget.
