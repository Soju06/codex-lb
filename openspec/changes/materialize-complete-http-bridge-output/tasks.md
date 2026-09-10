## 1. Implementation

- [x] 1.1 Materialize ordered `response.output_item.done` items from the durable event spool.
- [x] 1.2 Persist the materialized output only after a terminal completion event is present.
- [x] 1.3 Capture output-item completions in the live bridge state before the
  terminal operation-state write, so the event batcher cannot persist an empty
  terminal array first.
- [x] 1.4 Persist a bounded self-contained replay-input snapshot for completed
  operations and add the schema migration.
- [x] 1.5 Prefer a retained replay snapshot when reconstructing a continuation
  whose upstream parent-response chain is unavailable.
- [x] 1.6 Persist the first/root Codex operation with a session-scoped
  fingerprint when complete-transcript recovery is enabled.
- [x] 1.7 Deduplicate echoed preceding tool output during normal and retained-
  snapshot continuation replay, including known omitted reasoning/tool
  envelopes, while keeping partial/ambiguous prefixes fail-closed.
- [x] 1.8 Avoid re-appending a synthetic snapshot root's stored tool call when
  the continuation contains only the matching tool output.

## 2. Verification

- [x] 2.16 Apply the terminal boundary before source-stream malformed-data
  passthrough, while retaining pre-terminal passthrough and SSE controls.

- [x] 2.15 Omit hosted-tool bookkeeping only after completed status; reject
  unfinished hosted work in durable output and client history before replay.

- [x] 2.13 Discard all response data after the first terminal event, including
  late text, reasoning, content-part, tool-argument, and lifecycle frames.

- [x] 2.14 Verify exact parent ownership-registry preservation on downgrade,
  and test identity-free replay versus identity-bearing settlement through
  the backend WebSocket endpoint.

- [x] 2.11 Retain claim-fenced rebind ownership when compensation fails, so
  outer pre-dispatch cleanup can retry without dispatching replacement work.

- [x] 2.12 Reject transparent replay of every identity-bearing terminal error,
  assert no reconnect for model/auth failures, and preserve zero-based wait
  timestamps across account recovery attempts.

- [x] 2.10 Probe legacy anchored fingerprints for injected and client anchors
  under the same parent/session fences; align live terminal capture with the
  status-tolerant, identity-preserving replay comparison.

- [x] 2.9 Preserve the parent-owned migration registry when rolling back the
  rebind claim column, including production schemas with an empty registry.

- [x] 2.8 Integrate current release timing seams into recovery claims, rollback,
  generation cleanup, and parked waits; preserve generation fencing and avoid
  duplicate terminal publication when settlement is required.

- [x] 2.7 Recognize typeless error envelopes as terminal conflicts in public
  streaming and durable transcript materialization, with regression coverage.

- [x] 2.6 Reset per-attempt tool manifests before security-work replay and protect
  ambiguous-rebind compensation through repeated native cancellation, including
  durable rollback and event-fence restoration for both recovery paths.

- [x] 2.5 Align live terminal validation with durable materialization for malformed
  output and duplicate completions, and verify recovery rejects both.

- [x] 2.1 Add focused unit coverage for empty terminal output and missing completion.
- [x] 2.2 Add coverage for snapshot bounds and recovery after parent-chain purge.
- [x] 2.3 Add coverage for root-operation registration and fingerprint scoping.
- [x] 2.4 Run focused tests and lint, then run strict OpenSpec validation
  before publishing the change.
