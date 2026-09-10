# Account Routing Context

## Purpose

The normative routing contract is in [spec.md](spec.md). This context explains
why transient health is replica-local and how drained accounts return to normal
routing without becoming permanently invisible behind healthier accounts.

## Reauthentication warning state

`reauth_required` means refresh-token exchange needs operator repair; it does not
prove that the stored access token is unusable. Such accounts remain eligible
for ordinary requests and retain sticky, bridge, file, response, and realtime
ownership while the access token remains unexpired. Proactive refresh is skipped.
At known access-token expiry, selection and bridge reuse reject the account
locally: movable soft affinity may fail over, while hard account-owned continuity
remains fail-closed. Before expiry, an upstream rejection plus permanent forced-
refresh failure excludes the account only from that request's remaining movable
retries. Paused, deactivated, deleted, and security-ineligible accounts retain
their hard exclusions.

## Replica-local soft health

Error counts, backoff, health tiers, and probe streaks are advisory signals.
They deliberately stay in memory: a different replica may have observed a
different network path, and persisted `status`, `reset_at`, and `blocked_at`
remain the authoritative cross-replica gates.

An account moves from draining to probing only after the fixed quiet period.
Probing is validation, not a permanent low-priority state. Health-tier-aware
selection therefore gives the oldest due probing account one bounded admission
opportunity when healthy accounts would otherwise mask it. The existing
selection timestamp supplies the cadence and fair ordering. Unbound and fallback
sticky selection reversibly reserve that timestamp under the runtime lock before
sticky database work, preventing concurrent requests from consuming the same
interval. The reservation carries both that timestamp token and the runtime
version it observed. Both must still match before the final lease and after
selection-state persistence; otherwise the request releases the reservation and
retries from the newer health state. Sticky selection returns one provisional
desired-state mutation instead of writing during selection. The caller applies
it only after cap classification, lease admission, state persistence, and the
probe CAS; a stale probing snapshot or fail-closed cap result therefore cannot
delete or replace the current owner. A successful rebind collapses the former
delete-plus-upsert sequence into one atomic upsert. Reserve/release remains separate from the
health-observation version used by Force Probe settlement. Recovery therefore
needs no scheduler, random sampling, or operator setting.

## Relative first-token latency weighting

The weighted strategies discount an account whose recent first-token latency
on small, low-effort, unqueued, single-attempt `normal` turns sits above the
fleet by more than a deadband (retried or capacity-waited WebSocket/bridge rows
are skipped because their latency would charge a failed attempt, and possibly
another account's, to the destination). The signal is fleet-relative rather than an absolute threshold so
that an upstream-wide slowdown stays neutral and no operator has to know what
"slow" means for a given model or hour. It is a soft weight floored at 0.5,
never an exclusion: a slow account keeps at least half of its share so the
window keeps sampling it and the discount lifts within the hour once it
recovers. There is no setting because the weight is self-calibrating and
bounded; the alternative available today, `routing_policy=preserve` on the
slow accounts, is a hard exclusion that idles their capacity and goes stale as
cohort membership drifts. The per-account estimate trims the slowest decile
before averaging: the slow cohort is bimodal (a large share of its turns reason
before the first token), so a median would sit on the fast mode and miss the
share, while a plain mean would follow a single stall. Established sticky and
continuity owners never move because of the weight; only fresh weighted draws
and the destinations of reroutes that already exist are affected.

Example: eleven accounts answer in about 1.7 s and nine in about 2.0 s with a
40% share of 6 s reasoning turns. The fleet reference is 1.7 s, the slow
cohort's trimmed mean is about 3.3 s, so each slow account is drawn with half
of its credit weight while conversations already bound to it stay put.

## Relative per-model output-throughput weighting

First-token latency misses the dominant per-account speed difference observed
in production (2026-09-09, 24 h of Codex traffic, rows with more than 200
output tokens): on `gpt-5.6-sol` nine accounts streamed at a p50 of 39-40
tok/s while ten streamed at 66-75 tok/s (one at 108), with the same first-token
latency (7-8 s at 60k+ cached input); on `gpt-6-astra` the same accounts were
uniform at 44-56 tok/s. Throughput is therefore a property of the (account,
model) pair. The second signal samples, per account **and per model**, the
total output tokens over the generation span (`latency_ms -
latency_first_token_ms`) of successful, unqueued, single-attempt `normal` turns
with at least 200 output tokens, on any input size and reasoning effort, and
compares each account's estimate with the fleet median **for the model being
selected**. Evidence on one model never contributes to another, so an account
slow on sol keeps its full astra weight, and a build without a requested model
(quota planner) leaves the signal neutral.

Why total output tokens: the upstream reports `output_tokens` including
reasoning tokens. With reasoning summaries requested the first summary delta
is the first token, so the reasoning runs inside the span and the total is
what the span produced; without summaries the whole reasoning prefix sits
inside the first-token latency and the sample overstates the span's
throughput by `total / visible` -- several-fold on a turn that reasons for
longer than it answers. No numerator is exact for both kinds of turn (visible
tokens alone would under-read every summary-streaming turn by the same
factor). The total is kept because the comparison is fleet-relative: fresh
draws spread clients across accounts, so the summary/no-summary mix is shared
and the per-account medians move together; a median only shifts when more
than half of an account's samples are inflated, and the fleet reference only
when more than half of the accounts' medians are. Inflation reads fast, so it
never discounts the account it lands on.

Why a median: unlike first-token latency, throughput is not bimodal -- it is
the serving path's speed, largely independent of the prompt -- so a median
neither hides a mode share (the reason the TTFT estimator trims instead) nor
follows a single stalled stream (network pause, slow client) or a short burst.
The 200-token floor keeps the quantization of `tokens / duration` small.

Why the minimum, not the product: both signals measure the same defect -- a
slower serving path -- from two angles. An account slow on both would be
punished twice for one cause and drop below the 0.5 floor either signal
promises. The combined multiplier is the smaller of the two; the transition log
names which signal set it (`signal=ttft|tps|both|none`), the model and which
signal moved (`changed=`). Each signal is gated at its own scope: first-token
per account (one TTFT transition is one line however many models are
requested), throughput per (account, model) so alternating sol and astra
requests do not flap it. Only non-neutral throughput weights are retained, so
the per-model bookkeeping is bounded by the models an account is discounted on,
not by the model strings clients send.

Example: on the sol fleet above the reference is the median of the nineteen
per-account medians, 66 tok/s. Each 40 tok/s account is drawn at `40 / 66 =
0.6` of its credit weight for sol requests; its astra draws are unchanged
because the astra fleet is uniform. If one of those accounts also had a slow
first token (multiplier 0.5), its sol weight would be 0.5, not 0.3.

## Constraints and failure modes

- Eligibility, quota, cooldown, model, security, and local concurrency-cap
  checks still precede health-tier choice.
- A selectable sticky owner is retained; probing recovery uses unbound or
  fallback selection rather than moving an established owner.
- Hard-sticky fail-closed ownership does not let saturated fallback accounts
  bypass local concurrency caps. Saturated otherwise-available fallbacks return
  the stable local cap reason even when another under-cap fallback is unusable.
  Availability is compared over complete pre-cap and post-cap pools because
  opportunistic eligibility depends on what other foreground capacity exists;
  once a local cap reason is established, opportunistic error translation does
  not replace it. Nor can the post-cap selector revive an under-cap account that
  remains only a transient-backoff fallback.
- A lease race, stale persistence snapshot, or other local selection failure
  releases the provisional timestamp. After selection successfully returns a
  probe, a later caller cancellation may still postpone the next attempt by one
  quiet interval; that conservative bound cannot starve the account permanently.
- A planned sticky mutation runs after admission commits. If that database write
  fails, the request releases its local lease but retains the committed selection
  timestamp; attempting to decrement the shared runtime version would make
  concurrent health settlement ambiguous.
- A failed real request can drain the account again through the ordinary error
  path. Recovery never permits replay after downstream output is visible.
- Restarting a replica clears advisory health as before; persisted account
  status is unchanged.

## Example

Accounts A and B are healthy while C is probing after an upstream incident.
C's last selection is older than the quiet interval, so the next unbound
health-tier-aware selection admits C once. Existing sessions on A and B stay in
place. Budget and account routing-policy preferences cannot mask this bounded
recovery pass. A successful request advances C's local probe streak; C is not due for
another bounded admission until the interval elapses. Three successful
observations restore healthy routing, while an intervening failure restarts
recovery.

## Operational notes

The dashboard Force Probe action can accelerate validation on the replica that
handles the operator request. Only a valid completed probe contributes to local
recovery; operators should inspect `probeStatusCode`, `probeCompleted`, and
`holdRecovered` when an account remains unused. Non-2xx results, persistent quota exhaustion, and high usage correctly
keep the account out of healthy routing. Successful settlement reloads standard
usage and applies the same weekly/monthly and zero-primary-capacity normalization
as ordinary selection, so plan-specific windows cannot be omitted, mistaken for
short windows, or evaluated for a quota the plan does not have.
Settlement is discarded if newer replica-local runtime activity arrives while
that snapshot is loading, preventing an older probe success from clearing a
later failure.

## Verified persisted-hold recovery

The new proof is a completed request for the held model and service tier. Usage percentages remain advisory under the existing recovery rules. For example, a held Astra/default request can be recovered by a completed Astra/default probe; a Spark probe cannot clear it.

Generation fencing is necessary because two rejections can share blocked_at and reset_at down to the same stored second. Status writers conservatively advance the generation and discard scope unless they carry explicit rejected-request provenance. Migration leaves historical scope unknown. No inference from asynchronously written request logs is used.

The provider request is bounded to 30 seconds. A 60-second per-account database claim is renewed immediately before dispatch and checked at recovery. Competing probes receive 409, cancellation releases the claim, and expiry permits recovery from a crashed caller. Sessions remain request-owned.

Scope metadata describes the execution that can prove recovery, not a new model-scoped upstream penalty policy. Account-wide hold and ownership rules remain intact. All rejection writers must run the new version before operators rely on generation fencing in a multi-replica deployment.

The response separately reports HTTP status, completed execution and whether the guarded hold update landed. A later rejection can therefore leave accountStatusAfter limited even when an earlier guarded recovery did land. No credits are consumed or identity replaced by this endpoint.

## Recovery before failure recording

A replica can receive a recovered account row before its next selection. For example, an ordinary transient error can arrive just after an operator probe clears the old hold. Generation reconciliation runs before either selection or failure recording constructs runtime state. This prevents the error path from persisting the previous hold again while retaining transient-error accounting. Older recovered snapshots cannot clear newer rejection evidence. See [the recovery requirements](spec.md#requirement-completed-operator-probe-recovery).

## WebSocket rejection scope

Handshake classification forwards the request model and tier to the shared health writer. Terminal batch cleanup captures scope before awaiting settlement and logs. A request-specific error identifies its request; otherwise all pending requests must share one model and tier. Mixed shared failures retain unknown scope, so a probe for one of those requests cannot clear another scope's hold. For example, a priority-model handshake rejected with 429 retains enough evidence for a completed matching operator probe to clear the unchanged hold. Transport-neutral errors and failed settlement still follow their existing exclusions. See [completed operator probe recovery](spec.md#requirement-completed-operator-probe-recovery).
