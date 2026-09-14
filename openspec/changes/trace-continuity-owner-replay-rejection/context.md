# Context

## Purpose and scope

Make one production failure attributable: a Codex thread whose upstream owner account
became unroutable returns 502 `previous_response_owner_unavailable` on every resume, and
today nothing says why the proxy refused to move that turn to a healthy account.

In scope: a typed refusal reason, one bridge event, one Prometheus counter, and a
request-log row for the bridge surface. Out of scope: changing whether any turn recovers.
That is the behavior work tracked in #1707 and it depends on the evidence this change
produces.

## Production evidence (`1.25.0-beta.7`, 24 accounts, 2026-09-11)

| Observation | Value |
|---|---|
| `previous_response_owner_unavailable` request-log rows | 1,480 / 36h |
| ... by owner account status | `reauth_required` 1,361; `paused` 22; `rate_limited` 3; owner now `active` again ~94 |
| ... by `transport` | `websocket` 1,491 / 48h; `http` **0** |
| `owner_unavailable_fresh_resend` bridge events | **0** / 24h |
| `dead_owner_fresh_resend` bridge events | **0** / 24h |
| Bridge 502s present in `request_logs` over the last 6h | **0** of 14,555 rows |

Two readings matter. First, the recovery path that exists
(`switch_to_account_neutral_replay`) has never fired in production, so the refusal is
systematic rather than incidental. Second, the bridge surface's failures are absent from
`request_logs` entirely, so the only record is a rotating container log (3x50MB, roughly
8 hours) — which is why the 2026-09-04 recurrence of this class had to be reconstructed
from `haproxy` logs and DB `updated_at` clusters.

## Decisions

**Why a reason instead of a boolean.** `durable_full_resend_allows_account_neutral_replay`
is a conjunction of six independent proofs. `no_durable_lookup` and
`prefix_fingerprint_mismatch` call for different fixes — the first means owner resolution
handed us a pin with no anchor context (request-log index, in-process registry), the
second means the client's body diverged from the stored turn. Collapsing them to `False`
makes the next PR a guess.

**Why the reason is reported at the first refusing proof.** Reporting every failed proof
would need all six evaluated, which costs a projection rebuild on the hot path for no
operational gain. Evaluation order is preserved from the conjunction it replaces, so the
reported reason is deterministic for a given request.

**Why one funnel.** `owner_unavailable_allows_account_neutral_replay` already gates all
three recovery attempt sites (session creation, owner forward, capacity retry). Emitting
there instead of at each raise site keeps "exactly one reason per refusal" structural
rather than a convention every future raise site must remember. The
`required_continuity_owner_missing` exit is the one case with no exception to classify, so
it calls the classifier directly.

**Why only two error codes get a request-log row.** Widening the bridge's request-log
coverage to every pre-submit failure is a bigger behavioral question (capacity waits,
cooldown suppressions, and transport failovers each have their own settlement story). The
two continuity-owner codes are the ones the other two surfaces already log, so this is
parity, not a new policy.

**Why no `service.py` re-export.** The `_service_global` indirection in
`_record_continuity_replay_rejected` falls back to the module global, so the recorder works
without one, and `app/modules/proxy/service.py` sits exactly at its 2600-line architecture
ratchet. Tests patch `app.modules.proxy.service` with `raising=False`, matching the
existing continuity-counter tests.

## Constraints

- No ceiling-guarded proxy file may grow: `service.py` (2600/2600),
  `load_balancer.py` (3000/3021), `_service/http_bridge/mixin.py` (2435/2436),
  `_service/streaming/mixin.py` (1097/1100). This change touches none of them.
- `_service/http_bridge/**` may only import from the `_service` domains
  `{api_key_usage, compact, http_bridge, observability, support, warmup}`;
  `_request_log_client_fields` comes from `support`.
- Observational only: no forwarded byte, routing decision, selection outcome, or settlement
  path changes. The predicate's truth value is identical at every existing call site.

## Failure modes

- **`input_not_itemized` is a guard, not an outcome.** A non-list body can never satisfy
  the stored-prefix proof, so the prefix check always refuses first. It stays in the closed
  set because the code path exists; `test_string_input_refuses_on_the_prefix_proof` pins
  that ordering so a future caller that admits a non-list body past the prefix proof shows
  up as a test change rather than a silent mislabel.
- **Reason drift after a replay switch.** `switch_to_account_neutral_replay` clears the
  anchor request-locals; both new locals are cleared with them, so a refusal after a switch
  reports `no_durable_lookup` rather than a stale pre-switch reason.
- **Double emission.** The connect loop can re-enter after a cap spill or a capacity wait.
  The funnel emits once per classifier refusal, and the integration test asserts exactly
  one line for one failing resume.
- **Counter cardinality.** Both labels are closed sets (`surface` is
  `http_bridge`; `reason` is the eight-value literal), asserted in
  `test_closed_reason_set_matches_the_literal` and `tests/unit/test_metrics.py`.
- **No error row for a turn that then succeeded.** The write sits inside the propagating
  branch, so the transport-failover branch (which hands the turn to the raw-HTTP upstream and
  lets it settle its own outcome) cannot produce one. That combination —
  `previous_response_owner_unavailable` carrying websocket connect-transport provenance — is
  not reachable today, because the code comes from a selection failure and the provenance from
  a handshake, so the constraint is stated in the spec to hold future code rather than covered
  by a test that would have to fabricate the attribute.

## Example

An OpenCode session pinned to a manually paused account, resumed (the shape reported on
#1707 on 2026-09-09):

```text
continuity_owner_resolution surface=http_bridge source=request_logs outcome=hit
Proxy account selection start request_stage=follow_up preferred_account_id=<A>
No account selected error=No available accounts
Proxy preferred account unavailable error_code=continuity_owner_unavailable
http_bridge_event event=owner_unavailable_replay_rejected bridge_kind=thread_header
  bridge_key=sha256:… account_id=<A> detail=reason=no_durable_lookup key_strength=hard
proxy_error_response status=502 code="previous_response_owner_unavailable"
```

The new line is the fifth. `reason=no_durable_lookup` says the owner was pinned from the
request-log index with no durable anchor context, so no fingerprint proof could ever have
been satisfied — which identifies the fix as decoupling the replay gate from the durable
lookup rather than loosening the fingerprint check. A row for the same `request_id` now
also exists in `request_logs`, so the dashboard shows the outage.
