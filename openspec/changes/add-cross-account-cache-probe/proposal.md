## Why

Whether the upstream prompt cache is partitioned per account decides how much
cross-account failover actually costs, and the answer is not the obvious one.
A hand-run experiment on this deployment sent a ~28,000-token single-use
prefix on one account and then the identical bytes on four siblings; the
siblings reported near-full `cached_tokens` (28,032/28,168, 27,136/27,328 and
28,416/28,588 across three runs). `prompt_cache_key` did not gate the hit --
one arm hit with a *different* key while several missed with the *same* one --
and neither did the account: same-account repeats hit 1/5 while other accounts
hit 3/8. That is what a node-local prefix cache which is not partitioned per
account looks like, and it is currently a benefit: a thread that fails over
from A to B still hits A's warm prefix, worth ~27-28k input tokens per hit.

That experiment only exists as a shell transcript. Before the owner turns on
per-account outbound cache-identity scoping -- and again afterwards -- it has
to be re-runnable on demand, by the operator, with the same construction, or
"did sharing actually stop?" gets answered from memory instead of measurement.

## What Changes

- Add an operator diagnostic that reproduces the experiment: build a filler
  prefix of roughly 28,000 tokens carrying a nonce fresh for the run, send it
  on one seed account N times, then once on each of up to M other accounts,
  and report `usage.input_tokens_details.cached_tokens` per call.
- Report a per-call table (account, role, cached tokens, input tokens, hit or
  miss) plus the headline: did any non-seed account register a hit.
- Interpret the result explicitly, including the asymmetry: a non-seed hit
  proves the cache is not partitioned per account, while the absence of one is
  evidence and not proof, and a miss on the seed account is expected rather
  than a probe failure.
- Gate the spend: show the estimated token cost first, require an explicit
  confirmation, cap repetitions and sibling accounts, rate-limit the endpoint
  on a shared budget, run one probe at a time, and refuse outright when the
  account pool is already under pressure.
- Reuse the existing routed upstream client, credential refresh and upstream
  proxy resolution rather than opening a transport of its own.
- Persist nothing but numbers: the generated prefix, the run nonce and every
  byte of upstream response content stay in memory, including when the
  conversation archive is enabled.
- Add a scoped suppression seam at the archive's single `archive_enabled()`
  gate so locally generated diagnostic traffic is excluded without touching
  the operator's setting or a concurrent real request.

## Capabilities

### New Capabilities

- `cross-account-cache-probe`: an operator-triggered diagnostic that measures
  whether the upstream prompt cache is shared across accounts.

### Modified Capabilities

- `proxy-runtime-observability`: exclude locally generated operator-diagnostic
  traffic from the conversation archive at its existing single gate.

## Impact

- Affected code: new `app/modules/cache_isolation_probe/` package, its router
  registration in `app/main.py`, a scoped suppression seam in
  `app/core/conversation_archive.py`, and a new dashboard settings section.
- Affected tests: new prefix, service and API tests; the dashboard route
  permission matrix gains the two new routes.
- New API surface: `GET /api/diagnostics/cache-isolation-probe` and
  `POST /api/diagnostics/cache-isolation-probe/run`, both behind `ops:write`.
- No schema, migration, setting or dependency change. The diagnostic is inert
  until an operator confirms a run.
