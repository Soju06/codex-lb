## 1. Probe construction

- [x] 1.1 Build a nonce-keyed filler prefix that is byte-identical for every
  call of one run and shares no line with any other run, with the nonce in the
  first line so no earlier run's cached prefix can match.
- [x] 1.2 Mark the corpus inert at both ends and keep the instruction and the
  output budget small enough that input tokens dominate the spend.
- [x] 1.3 Expose a token estimate for the pre-run cost preview that does not
  understate the generated prefix.

## 2. Routed execution

- [x] 2.1 Send each call through the existing credential refresh, upstream
  proxy resolution and streaming upstream client; do not open a transport.
- [x] 2.2 Run the calls sequentially, seed first, so the seed has warmed the
  prefix before any sibling asks for it.
- [x] 2.3 Return token counts, a latency and a sanitized error code only.
- [x] 2.4 Close each upstream stream before the next call starts, and turn a
  failing call into a row rather than an aborted run.
- [x] 2.5 Keep the generated corpus out of the conversation archive for the
  duration of the run without changing the operator's setting.

## 3. Spend gates

- [x] 3.1 Refuse a run that is not explicitly confirmed.
- [x] 3.2 Cap seed repetitions and sibling accounts, and treat the sibling
  count as an upper bound rather than a demand.
- [x] 3.3 Rate-limit runs on a budget shared by all operators.
- [x] 3.4 Refuse while the proxy is degraded, while every account breaker is
  open, while a quarter or more of the routable pool is throttled, while fewer
  than two accounts are active, or while another run is in flight on this
  replica.

## 4. Reporting

- [x] 4.1 Emit one row per call with account, role, status, cached tokens,
  input tokens, latency and error code.
- [x] 4.2 Derive the verdict, treating a run in which nothing cached at all,
  or in which every sibling call failed, as inconclusive rather than isolated.
- [x] 4.3 Audit the numeric summary and persist nothing else.

## 5. Dashboard

- [x] 5.1 Show the plan, the estimated cost and the pool verdict before the
  confirmation.
- [x] 5.2 Render the result table and state the interpretation, including that
  sporadic seed misses are expected.

## 6. Verification

- [x] 6.1 Prefix determinism within a run and difference across runs.
- [x] 6.2 Result table shape, verdicts and cap enforcement.
- [x] 6.3 Confirmation gate, shared rate limit and the unhealthy-pool refusal.
- [x] 6.4 Archive suppression, partial failure and stream close.
- [x] 6.5 Ruff, the dashboard route permission matrix, and strict OpenSpec
  validation.
