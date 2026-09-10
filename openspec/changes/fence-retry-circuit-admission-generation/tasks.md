## 1. Specification

- [x] 1.1 Define the immutable generation, atomic claim, deadline, and reset
  contracts in the delta spec.
- [x] 1.2 Define the nullable claim receipt, expiry/reclaim, terminal release,
  and purge-fence contracts; record that the guarded migration extends the
  current-main model from merged #1863, serializes rollback with claim writes,
  and refuses rollback while a receipt is live.

## 2. Implementation

- [x] 2.1 Add the typed immutable local/durable generation snapshot and carry
  it through stale-anchor claim state.
- [x] 2.2 Make the durable claim dialect-guarded and `RETURNING`-based, and
  make conditional clears return whether they matched the captured generation.
- [x] 2.3 Bound claims and one timeout reconciliation by the caller deadline;
  perform local pre/post-CAS checks and retain local state on failures.
- [x] 2.4 Preserve admission generation while merging delayed failure writes.
- [x] 2.5 Add the nullable claim start/generation/expiry columns and migration;
  carry the exact request budget plus cleanup grace into the lease.
- [x] 2.6 Keep active markers out of per-key and batch purge, allow expired
  marker reclaim, and release markers from terminal, abort, cancellation, and
  proven pre-dispatch cleanup paths with generation/timestamp fencing.

## 3. Coverage

- [x] 3.1 Cover local/remote claim races, stateless local-state cleanup, and
  the timeout reconciliation outcomes.
- [x] 3.2 Cover generation-fenced clear races, lookup failure retention, and
  delayed clock-skewed failure merges on SQLite; cover active purge blocking,
  expired reclaim, receipt fencing, and migration upgrade/downgrade.
- [x] 3.3 Run focused retry-circuit/durable bridge tests plus lint, format,
  type, architecture, and strict OpenSpec validation where the environment
  provides the CLI.

## 4. Review follow-ups

- [x] 4.1 Keep failed-claim cooldown diagnostics within the caller's remaining
  request deadline, including cancellation-resistant durable lookups.
- [x] 4.2 Detach cancellation-resistant claim operations at the deadline and
  suppress concurrent reconciliation while the original write is still
  running.
- [x] 4.3 Fence per-key and batch stale purges on captured `updated_at_epoch`
  plus `admission_generation`; cover claim-versus-purge, delayed-failure,
  purge-failure, and concurrent-loader races.
- [x] 4.4 Keep the lease longer than the request budget by the cleanup grace,
  and prove normal finalization releases every popped claim, including
  requests without an API-key reservation.
- [x] 4.5 Keep durable claim-release refusals and exceptions inside HTTP and
  WebSocket terminal cleanup, retain the receipt, and schedule the existing
  service-owned generation-fenced retry; cover normal, draining, and aborted
  cleanup paths.
- [x] 4.6 After a settled timeout reconciliation refusal, perform one bounded
  durable receipt lookup and adopt only an exact generation/start/expiry match;
  keep lookup failures undecided and mismatches fail-closed.
- [x] 4.7 Fence pre-dispatch claim cleanup on the captured response-create
  attempt count so an ambiguous no-operation-ID send retains its claim.
- [x] 4.8 Detach the stale-anchor claim receipt before terminal session reset
  and transfer its key, lease, and attempt fence to the same-owner retry state;
  initialize the transferred fence from the retry state's own attempt baseline
  and schedule release retry after a pre-dispatch refusal or exception.
- [x] 4.9 Derive claim expiry and active/expired receipt decisions from the
  database clock using only a relative caller lease; cover claim, live-receipt
  reconciliation, reset, purge, and guarded migration downgrade under replica
  clock skew.
- [x] 4.10 Mark every conditional stale-purge miss uncertain before reload and
  keep pre-created admission fail-closed even when the refreshed row is fresh
  and below threshold.
- [ ] 4.11 Obtain maintainer sign-off on the stranded claim-receipt lockout
  policy documented in `design.md` (shorter abandonment lease, explicit
  reclaim owner, or lease-aware retry-after and accepted lockout window).
- [x] 4.12 Document exact live-receipt adoption after settled reconciliation
  refusal in both normative specs, retaining one bounded lookup and fail-closed
  behavior for unconfirmed receipts.
- [x] 4.13 Extract generic scheduled-purge timestamp/generation/count fencing
  and its regressions to the independent #2270 candidate. Retain receipt ownership
  checks here; verify both application orders preserve both contracts.
- [x] 4.14 Reparent the unmerged marker migration onto main's subscription
  overflow revision, preserve one Alembic head, and verify upgrade, guarded
  downgrade, and re-upgrade preserve upstream schema/data and retry generations.
- [x] 4.15 Follow the merged overflow/transport graph repair, preserve both
  parent migrations and transport settings, and recheck populated marker
  upgrade, guarded downgrade, writer exclusion, and re-upgrade.
- [x] 4.16 Keep the upstream merge regression bound to its historical merge
  while proving that later descendants reach the sole current head without
  comparing a historical schema against a later ORM model.
- [x] 4.17 Follow the merged dashboard resilience and timeout migrations,
  preserve their nullable defaults and explicit overrides through marker
  lifecycle checks, and retain the historical merge boundary while reconciling
  independent upstream schema requirements.
- [x] 4.18 Follow the merged routing/overload migration without changing it;
  verify its nullable defaults and explicit values survive the marker lifecycle
  and recheck receipt cleanup with the upstream routing-tunables integration.
- [x] 4.19 Follow the merged report-rollup migration without changing it;
  preserve populated report history and fold watermarks through the marker
  lifecycle, and recheck report, retention and request-log integration.
- [x] 4.20 Follow the merged automation claim-budget migration without changing
  it; preserve captured budgets and legacy NULL budgets through the populated
  marker lifecycle and recheck automation reclaim behavior.
- [x] 4.21 Reconcile HTTP continuation promotion requirements with generation
  fencing; verify soft locality, hard ownership, Chat reservation handoff and
  affected retry/queue cleanup paths on the new composition.

- [x] 4.22 Reconcile the dashboard prewarm and stream/bridge budget migrations;
  preserve their NULL defaults and explicit overrides through receipt marker
  upgrade, guarded downgrade, and re-upgrade, and verify combined bridge paths.
- [ ] 4.23 Choose and verify a mixed-version activation contract. Receipt-unaware
  replicas can advance a live receipt's generation during a rolling upgrade;
  staged activation or a non-rolling cutover has not been accepted.

- [x] 4.24 Reconcile the model-context override migration without changing it;
  preserve populated override rows through receipt marker upgrade, guarded
  downgrade, and re-upgrade, and recheck settings/model responses.
