# TTFT Phase Observability Context

The request log remains the durable source for 24-hour phase analysis. `spec.md` contains only requirements; this context captures operator queries and decisions.

## Decisions

- Phase timings are nullable integer milliseconds. Older rows and non-bridge traffic remain valid with `NULL` phase values.
- Prewarm canary metadata uses stable strings: bucket values `control`, `treatment`, `not_eligible`; status values `skipped`, `success`, `timeout`, `error`, `not_applicable`, `canary_miss`.
- The canary sample key is hashed from API key id plus normalized session/affinity identity. Only bucket/cohort/status are persisted.
- Prompt size cohorting in SQL uses `input_tokens` when available. For rows without tokens, the prompt size cohort is `unknown`.

## 24h SQL Examples

```sql
-- 24h TTFT by user agent, upstream transport, model, and cache ratio.
WITH recent AS (
  SELECT
    COALESCE(useragent_group, 'unknown') AS useragent_group,
    COALESCE(upstream_transport, 'unknown') AS upstream_transport,
    COALESCE(model, 'unknown') AS model,
    CASE
      WHEN input_tokens IS NULL OR input_tokens = 0 THEN 'unknown'
      WHEN COALESCE(cached_input_tokens, 0) * 1.0 / input_tokens >= 0.75 THEN 'cache_75_100'
      WHEN COALESCE(cached_input_tokens, 0) * 1.0 / input_tokens >= 0.25 THEN 'cache_25_75'
      ELSE 'cache_0_25'
    END AS cache_ratio,
    latency_first_token_ms,
    latency_ms
  FROM request_logs
  WHERE requested_at >= now() - interval '24 hours'
)
SELECT useragent_group, upstream_transport, model, cache_ratio,
       COUNT(*) AS requests,
       percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_first_token_ms) AS ttft_p50_ms,
       percentile_cont(0.90) WITHIN GROUP (ORDER BY latency_first_token_ms) AS ttft_p90_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_first_token_ms) AS ttft_p95_ms,
       percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms) AS total_p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS total_p95_ms
FROM recent
WHERE latency_first_token_ms IS NOT NULL
GROUP BY useragent_group, upstream_transport, model, cache_ratio;
```

```sql
-- 24h TTFT by session gap cohort.
SELECT
  CASE
    WHEN session_previous_gap_ms IS NULL THEN 'unknown'
    WHEN session_previous_gap_ms < 120000 THEN 'lt_2m'
    WHEN session_previous_gap_ms < 600000 THEN '2m_10m'
    ELSE 'gte_10m'
  END AS session_gap_cohort,
  COUNT(*) AS requests,
  percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_first_token_ms) AS ttft_p50_ms,
  percentile_cont(0.90) WITHIN GROUP (ORDER BY latency_first_token_ms) AS ttft_p90_ms,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_first_token_ms) AS ttft_p95_ms,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS total_p95_ms
FROM request_logs
WHERE requested_at >= now() - interval '24 hours'
  AND latency_first_token_ms IS NOT NULL
GROUP BY session_gap_cohort;
```

```sql
-- 24h TTFT by prompt size cohort.
SELECT
  CASE
    WHEN input_tokens IS NULL THEN 'unknown'
    WHEN input_tokens < 2000 THEN 'lt_2k'
    WHEN input_tokens < 10000 THEN '2k_10k'
    WHEN input_tokens < 50000 THEN '10k_50k'
    ELSE '50k_plus'
  END AS prompt_size_cohort,
  COUNT(*) AS requests,
  percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_first_token_ms) AS ttft_p50_ms,
  percentile_cont(0.90) WITHIN GROUP (ORDER BY latency_first_token_ms) AS ttft_p90_ms,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_first_token_ms) AS ttft_p95_ms,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS total_p95_ms
FROM request_logs
WHERE requested_at >= now() - interval '24 hours'
  AND latency_first_token_ms IS NOT NULL
GROUP BY prompt_size_cohort;
```

```sql
-- 24h TTFT by prewarm bucket, outcome, and cohort.
SELECT
  COALESCE(prewarm_canary_bucket, 'unknown') AS bucket,
  COALESCE(prewarm_status, 'unknown') AS outcome,
  COALESCE(prewarm_eligible_reason, 'unknown') AS cohort,
  COUNT(*) AS requests,
  percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_first_token_ms) AS ttft_p50_ms,
  percentile_cont(0.90) WITHIN GROUP (ORDER BY latency_first_token_ms) AS ttft_p90_ms,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_first_token_ms) AS ttft_p95_ms,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS total_p95_ms
FROM request_logs
WHERE requested_at >= now() - interval '24 hours'
GROUP BY bucket, outcome, cohort;
```
