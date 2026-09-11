# Telemetry capability — context

## Purpose

Give the project visibility into its install base (version distribution, deployment shapes,
client ecosystem, feature usage) without collecting anything that identifies an operator,
an account, or request content. Consent model is informed opt-out: active by default,
one-time dialog with the exact payload, settings toggle, env fallback for headless opt-out
before the first dashboard decision.

Decision record (2026-08-06, maintainer): default-on with first-run confirmation dialog for
both new and existing users; settings toggle; expanded field set over the minimal version.

## Collection endpoint

Self-hosted SHM (kOlapsis/shm) server operated by the maintainer at
`https://telemetry.tokmaxxing.com`. SHM provides Ed25519 instance signing, aggregate dashboards,
and public README badges (`/badge/codex-lb/instances`, `/badge/codex-lb/version`).
The SDK path is `/v1/register`, `/v1/activate`, `/v1/snapshot` (note: NOT `/api/v1/`,
which is SHM's admin namespace). codex-lb implements a small Python client (SHM ships
Go/Node SDKs only).

## Payload schema v1 (the allowlist)

Everything below derives from existing data (`request_logs`, settings, module registry).
No new per-request instrumentation. `*_bucket` fields use the documented bucket sets.

Every outbound request body has an explicit Pydantic model and is covered by the wire-schema
allowlist test. The registration body sent to `/v1/register` is:

```json
{
  "app_name": "codex-lb",
  "app_version": "1.20.2",
  "deployment_mode": "docker | k8s | pip | bare",
  "environment": "",
  "instance_id": "<random UUIDv4>",
  "os_arch": "linux/x86_64",
  "public_key": "<Ed25519 public key, hex>"
}
```

`app_name` identifies this project, `app_version` supports upgrade/deprecation decisions,
`deployment_mode` and `os_arch` are coarse deployment signals, `environment` is intentionally
empty, and `instance_id` plus `public_key` establish the random signing identity. Activation
sends only `{"action": "activate"}`.

The signed `/v1/snapshot` body is an envelope. The consent preview renders this same shape via
the same constructor; its timestamp is the current preview-generation time, while an actual
send regenerates the current transmission time.

```json
{
  "instance_id": "<random UUIDv4>",
  "metrics": { "<snapshot metrics below>": "..." },
  "timestamp": "2026-08-06T12:00:00Z"
}
```

The `metrics` object is the versioned snapshot schema:

```json
{
  "schema_version": 1,
  "instance_id": "<random UUIDv4, minted on first run>",
  "version": "1.20.2",
  "python": "3.13",
  "os": "linux",
  "arch": "x86_64",
  "uptime_hours": 168,

  "deploy": {
    "method": "docker | k8s | pip | bare",
    "db_backend": "sqlite | postgres",
    "db_size_bucket": "unknown | <bucket>",
    "replicas": 3,
    "reverse_proxy": true
  },

  "accounts": {
    "pool_bucket": "<bucket>",
    "plan_mix": {"plus": "<bucket>", "pro": "<bucket>", "team": "<bucket>", "free": "<bucket>"},
    "workspace_accounts": true,
    "routing_policy": "<enum>",
    "limit_warmup_enabled": true,
    "egress_proxy_used": false
  },

  "usage_7d": {
    "requests": 203051,
    "success_rate": 0.987,
    "tokens_input": 18800000000,
    "tokens_output": 94000000,
    "tokens_cached_ratio": 0.89,
    "cost_usd_bucket": "<bucket>",
    "request_kinds": {"responses": 0.0, "chat": 0.0, "images": 0.0, "unknown": 1.0},
    "transport_mix": {"ws": 0.6, "http_bridge": 0.4},
    "service_tier_mix": {"default": 0.90, "flex": 0.05, "priority": 0.05},
    "clients": {"codex-cli": 0.44, "openai-sdk-python": 0.3, "other": 0.02},
    "clients_other_ratio": 0.02,
    "models": [
      {
        "name": "gpt-5.4",
        "share": 0.62,
        "reasoning": {"xhigh": 0.31, "high": 0.48, "medium": 0.21},
        "avg_output_tokens_bucket": "<bucket>"
      }
    ],
    "latency_ms_p50": 1200,
    "ttft_ms_p50": 800,
    "ttft_ms_p95": 3400,
    "rate_limit_429_ratio": 0.004,
    "top_upstream_errors": ["server_overloaded", "usage_limit_reached"]
  },

  "features": {
    "api_firewall": true,
    "quota_planner": true,
    "sticky_sessions": true,
    "conversation_archive": false,
    "automations": false,
    "fleet": false,
    "model_sources_count": 2,
    "api_keys_bucket": "<bucket>",
    "prometheus": false,
    "otel": false,
    "dashboard_auth": true,
    "reset_credits": true,
    "image_api_used": true
  }
}
```

Field notes:

- `top_upstream_errors`: enum `upstream_error_code` values only, top 5 by count. Free-text
  `error_message` is banned by spec.
- `request_kinds`: current `request_logs` rows do not persist ingress route family. The existing
  `request_kind` column is a workload class (`normal`, `warmup`, `compaction`, and similar),
  while `source` identifies the upstream. Until an authoritative route-family signal exists,
  rows are reported as `unknown`; source and model-name heuristics are deliberately forbidden.
- `clients`: canonical family shares from the normative mapping table in `spec.md`. Raw
  `useragent_group` values never leave the instance.
- `models[].name`: the immutable bundled bootstrap model slug allowlist, shared by heartbeat
  and day aggregation. Live catalog discovery and operator configuration cannot extend it;
  custom/unknown model names fold into a single `{"name": "other"}` entry.
- Exact `requests` / token counts support fleet aggregates. Account totals and their per-plan
  and per-status counts are exact local row counts in v2, without account identifiers.
  API key counts, cost, and database size remain bucketed.
- `replicas`: size of the configured HTTP bridge instance ring (multi-replica adoption signal).

## Bucket sets

- count buckets (API keys): `0`, `1`, `2-5`, `6-20`, `21-100`, `100+`; account totals, per-plan
  counts, and per-status counts are exact integers in v2.
- `db_size_bucket`: `unknown`, `<100MB`, `100MB-1GB`, `1-5GB`, `5-10GB`, `10-50GB`, `50GB+`
- `cost_usd_bucket` (7d): `<10`, `10-100`, `100-1k`, `1k-10k`, `10k-50k`, `50k+`
- `avg_output_tokens_bucket`: `<250`, `250-1k`, `1k-4k`, `4k-16k`, `16k+`

## Consent resolution precedence

persisted decision > `CODEX_LB_TELEMETRY_ENABLED` env (when set) > default
(`undecided` ⇒ active). The env value only decides while the persisted state is `undecided`
(headless opt-out before first boot); a saved dashboard decision always wins. The dialog is
only shown while persisted state is `undecided` and no env value is set.

## Consent API and preview cost

`GET /api/settings/telemetry` always returns `state`, `source`, `active`, and `preview`. The
default GET includes a preview envelope only for undecided/default consent, when the dialog can
appear; decided and environment-overridden responses return `preview: null` without running the
seven-day aggregate queries. Settings requests the same endpoint with
`include_preview=true` to fetch the current envelope on demand. `PUT /api/settings/telemetry`
persists the decision and returns `preview: null`.

## Cadence and replica ownership

The startup and 24-hour ticks run through the shared scheduler leader-election gate. Only the
leader constructs aggregates, transmits the snapshot, and logs the undecided-consent startup
notice. Followers perform none of that work, avoiding duplicate snapshots and duplicate notices.

For v2, the heartbeat is sent before day construction. A tick captures one UTC date and discovers
normal-traffic days in SQL within `[today_utc - 7 days, today_utc)`, further bounded by the
acknowledgement watermark. This is a calendar window, even with sparse traffic. Older days are
acknowledged without constructing their bodies, including when there is no recent traffic.
The scalar watermark first covers dates before the window, then advances through successful
populated dates from oldest to newest, stopping at the first failure. Newer successes may be
resent safely because the collector upserts by instance, date, and schema version.

For example, a September 10 tick can send only September 3–9. Traffic on September 9 and
August 1 produces only the September 9 body; August 1 is covered by the September 2 watermark
floor even if September 9 fails. An old-only or empty history also advances to that floor.

## Retention

Each snapshot summarizes the previous seven days of existing local request logs. codex-lb does
not create a second local telemetry history or queue failed transmissions. The project-operated
collector is `https://telemetry.tokmaxxing.com`; it retains per-instance detail for 365 days
and then deletes it. Non-identifying fleet aggregates may be retained beyond that period.

## Failure modes

- Endpoint down: bounded timeout (5s), at most one retry per interval, debug-level log,
  proxy path untouched. Snapshot is rebuilt fresh next interval (no queue/backlog).
- Aggregation query cost: snapshot queries reuse the same 7-day aggregate shapes as the
  dashboard reports module; they run on the leader scheduler once per tick and only on an API
  request when the undecided dialog or an explicit settings preview needs them. On Postgres
  instances with very large `request_logs` this is the same load class as one dashboard load.
- Clock skew / restart loops: the elected leader transmits the startup snapshot; SHM's
  `/v1/activate` is idempotent (active → active refreshes last-seen). Rapid restart loops are
  bounded by one snapshot per elected-leader process start; no local rate limiter in v1.

## Example: privacy review quick check

An instance with accounts `alice@corp.com` (workspace W1) + 12 others, a custom model source
`corp-internal-gpt`, and traffic from an internal tool `senpi/1.0`:

- payload has `accounts.total: 13`, exact `accounts.per_plan` and `accounts.per_status` counts,
  and `workspace_accounts: true`, with no account identifiers
- `corp-internal-gpt` traffic appears as `models[].name == "other"`
- `senpi` traffic appears in `clients` under `other` and inflates `clients_other_ratio`
- the strings `alice`, `corp.com`, `W1`, `corp-internal-gpt`, `senpi` appear nowhere in the
  serialized payload (schema snapshot test enforces this)

## Payload schema v2 additions

The heartbeat retains the v1 envelope and rolling `usage_7d` as non-summable instantaneous
state, with `schema_version: 2`. `accounts.total`, `accounts.per_plan`, and
`accounts.per_status` are exact local row counts; `features.api_keys_bucket`,
`usage_7d.cost_usd_bucket`, and `deploy.db_size_bucket` remain bucket strings (`unknown` is used
when database size cannot be measured).

A completed UTC day is sent as one `/v1/day` body with `instance_id`, `utc_date`, and
`schema_version`. It contains independent marginal lists for `models`, `clients`, `transport`,
`upstream_transport`, and `service_tier`, each with exact `requests` and `latency_ms`, `ttft_ms`,
and `tps` histograms, plus `global` and integer `request_kinds` counts. No entry is nested under
another dimension. Unknown allowlist values are `other`; models are capped at ten named entries
plus `other`.

Day discovery and aggregation reuse the reports normal-traffic predicate: source `limit_warmup`
and request kinds `warmup` / `limit_warmup` are excluded, as in the heartbeat. Upstream transport
uses the producer spellings `websocket` → `ws`, `openai_compatible_http` → `http`, and `http` →
`http`; unknown values remain `other`. Request transport maps `websocket` to `ws`, `http` to
`http_bridge`, and automation or other producer values to `other`.

Histograms use sparse bucket indexes with `sample_count` equal to the sum of counts. Latency and
TTFT upper boundaries are `[0, 50, 100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600, 51200,
102400, +inf]`; TPS boundaries are `[0, 5, 10, 20, 40, 80, 160, 320, 640, 1280, +inf]`.
TPS uses `(output_tokens - coalesce(reasoning_tokens,0))*1000/(latency_ms-
latency_first_token_ms)` only when both timing measurements exist and the numerator and
denominator are positive. A measured TTFT of zero is valid; NULL TTFT cannot supply a duration.

Day errors contain exact `upstream_error_class`, `failure_phase`, `http_status_class` (with `429`
separate), and `outcomes` counts. Unregistered values map to `other`; free-text failure fields
are omitted. Per-instance detail is retained by the collector for 365 days.

Failure phases translate the persisted producer vocabulary into the fixed wire enumeration:

| Persisted phase | Wire phase | Producer |
|---|---|---|
| `usage_settlement` | `settle` | `app/modules/proxy/_service/api_key_usage.py:465`, failed usage settlement |
| `upstream` | `stream` | `app/modules/proxy/_service/streaming/helpers.py:780`, upstream stream failure |
| `bridge` | `bridge_queue` | `app/modules/proxy/_service/http_bridge/streaming.py:4329`, pre-response bridge handoff timeout |

Existing wire-enum spellings remain valid; all other phases become `other`. Failure details,
exception names, and bridge-stage strings do not participate in this mapping or leave the instance.
