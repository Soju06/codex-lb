# Cache-rate investigation, 2026-09-27

These are bounded production observations, not new routing requirements. All times below are UTC. Queries were read-only. No credential, source assignment, source enablement or production binary was changed during this investigation. Synthetic endpoint probes used the original source credential internally without printing or saving it and did not replay user content.

## Measurements

The measurement is `sum(cached_input_tokens) / sum(input_tokens)` over successful request logs, rather than an average of per-request percentages.

| Scope | Sample | Cached input fraction |
| --- | --- | --- |
| Original source, `ch/linxaq`, Sep 26 before 20:30 | 3,557 successful requests | 76.17% |
| Original source, `ch/linxaq`, Sep 27 02:39–03:06 | 137 successful requests | 45.03% |
| Same older `codex_exec` Desktop 0.154 client, before deployment | 61 successful requests | 43.32% |
| Same older `codex_exec` Desktop 0.154 client, after deployment through about 03:08 | 75 successful requests | 41.15% |
| Subscription traffic, Sep 27 02:00 hour | 589 successful requests | 92.08% |

The client mix changed: the earlier `ch/linxaq` traffic was dominated by Desktop 0.155 (3,261 requests, 76.88% cached); the recent sample is mostly the older 0.154 exec client and Desktop 0.158. This is evidence that aggregate before/after percentages are not a controlled comparison of the deployment. It does not prove that client version alone causes the difference.

All recent logged successful custom requests used the original source. The four added sources were created at 02:30:15 and disabled at 02:38:24–36; none had a successful application request log in the inspected period. The original source already reported 2.18% cache on a request at 02:09, before the four sources existed. Direct health probes are outside these application request logs.

Recent per-turn samples in one conversation show a large input shrinking from about 242,000 tokens to 22,000, then growing again. That is consistent with context replacement/compaction but cannot establish the actual body transformation because production request-body archives and request-shape tracing were not enabled.

## Direct upstream experiment

Ten sequential synthetic requests went directly to the original admin-pc `/v1/responses` endpoint, bypassing Codex-LB's routing, request shaping and usage storage. The credential, model `ch/linxaq`, request JSON and `prompt_cache_key` were identical across all ten requests. Requests were spaced by three seconds after each response. Every response completed, identified its upstream model as `gpt-6-astra`, and reported 12,735 input tokens.

Reported cached token counts, in order:

```text
0, 0, 0, 0, 0, 12032, 12032, 12032, 12032, 0
```

The four hits correspond to 94.48% cached input. The initial misses could include cache population delay; the final miss after four consecutive hits shows that the endpoint does not consistently report reuse even for the identical warmed request in this experiment. This establishes an upstream observation, not the internal cause. An earlier five-request probe with a smaller prompt likewise returned four zero-cache responses before its first hit.

## Code comparison and limits

Comparing the pre-pool commit `be2a2b98` with deployed `eb2df654` shows identical request shaping (`_shape_source_responses_payload`), source headers (`_source_headers`), Responses cached-token extraction (`_usage_from_responses_mapping`) and alias projection. The local 409 fix changes classification copies and reference extraction only; it has not been deployed and does not rewrite the forwarded prompt or cache key.

The pool selector currently ranks by replica-local load and recent selection; it has no preference based on `prompt_cache_key` or session headers. Known upstream-owned state pins requests through durable ownership. Source-free requests can rotate credentials when several sources are enabled, so affinity deserves a separate design before optimizing cache in that configuration. It does not explain the measured incident where all successful requests used one source. The existing direct-source transport also does not forward client session headers; that behavior predates the pool deployment and was not changed here.

The strongest supported conclusion is that the recent lower aggregate includes a different client/workload mix, while direct admin-pc responses independently demonstrate intermittent cache misses. There is no evidence here that Codex-LB dropped reported cached tokens or that the four new credentials received the successful conversations. The exact reason for misses inside admin-pc remains unresolved without its upstream routing/cache diagnostics; account changes, backend selection or cache eviction must not be asserted as established causes.

Next diagnostic evidence should be sanitized upstream traces linking a stable cache-key hash and prompt-prefix hash to the selected account/backend and returned cache usage. Do not disable ownership checks, force arbitrary credential switching, or promise that the 409 fix will restore cache percentages.
