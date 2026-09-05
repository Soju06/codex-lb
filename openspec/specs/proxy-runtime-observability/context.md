# Proxy Runtime Observability Context

## Purpose and Scope

This capability defines what operators should be able to see in the live server console while debugging proxy traffic.

See `openspec/specs/proxy-runtime-observability/spec.md` for normative requirements.

## Decisions

- **Timestamps are always on:** timestamped console logs are a baseline operator need, not a debug-only feature.
- **Request tracing is opt-in:** outbound request summary and payload tracing remain configurable because payload logs can be noisy or sensitive. Since issue #1340 phase 1 the switch is the single `CODEX_LB_TRACE` comma-separated channel list (`shape`, `shape_raw_cache_key`, `payload`, `service_tier`, `upstream_summary`, `upstream_payload`); empty default = all off. It is an incident-debugging knob for interactive use only.
- **Error logs must be correlated:** request id, endpoint, status, code, and message are the minimum useful fields for debugging 4xx/5xx failures.
- **Prewarm observability is outcome-only:** the Codex HTTP-bridge prewarm canary experiment finished, so its bucket/cohort dimensions were retired (issue #1340 phase 4). The `codex_lb_http_bridge_prewarm_total` counter is labelled by `outcome` only, request logs record `prewarm_status` / `prewarm_latency_ms` (statuses: `not_applicable`, `skipped`, `success`, `timeout`, `error` — `canary_miss` no longer occurs), and the legacy `prewarm_canary_bucket` / `prewarm_eligible_reason` request-log columns stay declared but unwritten for one release for rolling-upgrade safety; the Alembic drop revision ships next release (see the next-release queue in `openspec/specs/deployment-installation/context.md`).
- **TTFT datasource selection stays in Grafana:** the Helm chart packages the
  TTFT dashboard but does not provision a PostgreSQL datasource or its
  credentials. The visible, single-select `DS_SQL` variable keeps
  installation-specific datasource UIDs out of chart values while routing all
  four SQL panels through one explicit selection.

## Operational Notes

- Use request ids to correlate inbound proxy logs, outbound upstream traces, and client-visible failures.
- Prefer summary tracing in normal debugging sessions; enable payload tracing only when the exact normalized outbound request matters.
- For direct compact `5xx` failures, look for `proxy_compact_failure` alongside `upstream_request_complete`; together they show the compact failure phase, failure detail, exception type, retry metadata, and affinity source.
- After the Grafana sidecar imports the TTFT dashboard, select the ordinary
  PostgreSQL datasource that points to the codex-lb database from the visible
  **PostgreSQL** dropdown. A datasource registered only as a frontend runtime
  plugin is not listed by Grafana's datasource variable.
- Timeout invariant violation logs describe startup `Settings` and imported
  constant validation only. They intentionally avoid request-scoped overrides,
  runtime-derived effective timeout values, payloads, API keys, access tokens,
  raw affinity keys, account emails, and other high-cardinality identifiers.


## Responses latency investigation (issue #2029)

A 2026-09-05 controlled study compared PR2030 `9a01cb3` with main `aec4d7b`. It used the real app, normal routes/services and temporary file SQLite with two synthetic Pro accounts, unkeyed traffic, no account proxy and a local scripted TLS origin. Nine fixed events separated created from first tool output. Each ref had760 LB attempts across retained/churned WebSocket, native HTTP and HTTP bridge, small/roughly500KB payloads and concurrency1/4; direct-origin controls ran alongside each scenario. This measures local work, not model inference, real tokenization or WAN latency.

On main, small retained-WS/concurrency4 first-content medians were about11 ms both direct and through LB. Large retained-WS medians were39.9 ms through LB versus14.5 ms direct. Large HTTP-bridge content/terminal medians were149.1/247.8 ms with connection reuse and substantial durable work; native HTTP and direct WS bypass that bridge. These values do not explain historical waits lasting seconds or minutes. Differences between refs include all intervening changes and scheduling variation; they are not isolated PR speedup estimates.

A separate supported anchored-HTTP race was reproducible: after terminal/EOF, owner lookup could return no row before detached request-log persistence finished. Immediate follow-ups failed35/96 times; a100 ms delay or experimental existing-cache publication gave0/96. The publication experiment proves the missing same-process ownership seam, not a speedup or cross-replica guarantee. Installed CLI0.153.2 through real LB retained one upstream socket across prewarm, generation and a single incremental tool-output item (~1.8KB). Its direct-origin HTTP fallback instead sent full history without `previous_response_id`, so the anchored race cannot explain that normal fallback.

The main `aec4d7b` research baseline filled native HTTP queue and TTFT while leaving the existing first-upstream-event/created fields null. HTTP attempts now populate those fields from observed upstream events using the [existing attempt origin](spec.md#requirement-ttft-phase-timings-are-persisted-and-exported). The interval before an upstream event can include local/network/provider work; created-to-content is not asserted to be pure model compute. Historical nulls remain null, and client receipt remains a distinct boundary.

Current saved-store read checks (~277MB,96,732 request logs,83,060 usage rows) found indexed request-path reads around0.95–2.33 ms with fresh SQLite connections. This does not reconstruct the old5.4GB store, historical contention or upstream overload. Earlier every-turn bridge churn in the synthetic run was a fixture startup-order error and was corrected before the quoted baseline. No native-helper performance, multi-replica timing or long-duration saturation result is claimed.

### Accepted scope links

- [DB completion grace](#prevent-ssl-starvation-false-reclaim)
- [Direct WSS trust-context reuse](#reuse-direct-wss-system-trust)
- [Immediate HTTP response owner](#publish-http-response-owner)
- [HTTP upstream timing](#observe-http-upstream-latency)
- [HTTP preparation](#skip-unused-http-preparation-serialization)

#### prevent-ssl-starvation-false-reclaim

PR2030 keeps main's shipped aiohttp cache and limits its correction to DB completion-grace simplification and real-worker coverage; [database-backends](../database-backends/spec.md) owns the domain.

#### reuse-direct-wss-system-trust

Python WSS connection churn can reuse its current system-default context without importing certifi roots. [Outbound HTTP clients](../outbound-http-clients/spec.md) owns trust/lifecycle policy; retained turns are not per-connection builds.

#### publish-http-response-owner

Publish authoritative observed HTTP response IDs through the existing bounded owner cache before delivery, retaining caller scope and durable miss fallback. [Responses compatibility](../responses-api-compat/spec.md) owns continuity behavior.

#### observe-http-upstream-latency

Fill existing upstream-first-event and created fields while preserving attempt clocks and the lazy/verbatim path; [this capability](spec.md) owns those meanings.

#### skip-unused-http-preparation-serialization

Defer unused whole-body dumps when their consumers are inactive. Exact-body ablation measured preparation5.40→2.36 ms at1.06MB and46.02→18.85 ms at8.55MB; these are preparation-only measurements. [Outbound HTTP clients](../outbound-http-clients/spec.md) owns the unchanged wire/transport contract.

Bridge batching, global client pooling, replica readiness changes, scheduler tuning, native IPC and database rewrites are deferred. The accepted changes are checked together as one local integration; combined validation is not an additional feature or PR scope.

### HTTP event timing and owner provenance

First-upstream-event, response-created, first-token and total latency use the same post-admission attempt origin; selection and admission remain queue time. Each attempt owns fresh timing state. Unobserved phases stay null and legitimate zero values remain zero. These fields record accepted events entering the service attempt, not socket-first-byte, downstream receipt or model-only execution. Later token frames retain the existing lazy/verbatim forwarding path.

Timing depends on `publish-http-response-owner` for internal SSE provenance and inherits that branch through a normal merge. A generated failure event does not create upstream activity. A real upstream error remains activity even when normalization supplies a local response ID: owner publication rejects that ID, while timing observes the event. The owner scope defines both provenance flags and the producer formatter; timing does not duplicate them.

The subsequent matched local study compared integration snapshot `ffef2a6` with contemporaneous main `5ad638b`, using the same scripted TLS origin and temporary data. Timing and instrumentation ran separately; failures and account populations remain part of the comparison. Its small fixture CA store does not measure normal host trust-loading costs. The API observer counts only `ssl.create_default_context` calls, excluding direct or implicit `SSLContext` construction; those counts do not establish comparative WSS reuse. The owning WSS lifecycle regression establishes reuse and refresh behavior. Exact-body preparation checks observed two removed encodes, with median preparation thread CPU of 5.599 to 2.467 ms for a 1,062,374-byte body and 47.415 to 18.947 ms for an 8,538,142-byte body. These intervals exclude HTTP wire serialization, network and server work.

A separate installed CLI 0.153.4 witness retained one upstream socket for prewarm, generation and a real harmless tool continuation. Rejecting the first handshake with 426 led to a WebSocket retry, not an observed HTTP fallback. The 1,870-test combined acceptance used the real metrics dependency and checked owner/timing/provenance, body/consumer identity, WSS lifecycle and DB cleanup together. These local results do not establish native latency parity or resolve the attribution of historical minute-scale waits.
